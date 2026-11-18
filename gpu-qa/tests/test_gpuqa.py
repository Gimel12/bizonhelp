"""CPU-only correctness and fault injection. These do not qualify physical GPUs."""

import contextlib
import copy
import hashlib
import io
import json
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from gpuqa.common import (
    QAError,
    atomic_json,
    effective_result,
    pending_result,
    worker_env,
    verify_manifest,
)
from gpuqa.hardware import parse_snapshot, select_gpus, health_faults, kernel_faults
from gpuqa.supervisor import (
    Supervisor,
    make_plan,
    validate_completions,
    qualification_key,
)
from gpuqa.cli import run_path, run_supervisor, main
from gpuqa.reporting import build_report, export_rma, redact_value

RUN = "20260101T000000Z-1234abcd"


def uuid(i):
    return "GPU-" + f"{i:08x}-0000-0000-0000-000000000000"


def gpu(i):
    return dict(
        index=i,
        uuid=uuid(i),
        name="Synthetic GPU",
        serial="TEST-ONLY",
        pci=f"00000000:{i+1:02x}:00.0",
        driver="TEST",
        ecc="Enabled",
        display="Disabled",
        power_limit_w=300,
        memory_total_mib=24576,
        memory_free_mib=24000,
        temperature_c=40,
        thermal_margin_c=40,
        max_operating_c=85,
        memory_temperature_c=50,
        counters={"ecc_errors/volatile/uncorrected": 0},
        thermal_flags=[],
        pcie_replays=0,
        sw_thermal_us=0,
        hw_thermal_us=0,
        power_brake_us=0,
    )


def state(n=1):
    return dict(
        run_id=RUN,
        status="PILOT_PASS",
        profile="validate",
        production_qualified=False,
        devices=[gpu(i) for i in range(n)],
        per_gpu_active_seconds={uuid(i): 10 for i in range(n)},
        stages=[],
        events=[],
        warnings=[],
        coverage=[],
    )


class PlannerTests(unittest.TestCase):
    def test_every_gpu_and_required_stage_at_all_cardinalities(self):
        for n in range(1, 9):
            with self.subTest(n=n):
                ids = [uuid(i) for i in range(n)]
                for profile in ("validate", "quick", "production", "rma"):
                    a, b = make_plan(ids, profile)
                    self.assertEqual(len(a), n * 7)
                    for u in ids:
                        self.assertEqual(
                            {s["stage"] for s in a if s["uuids"] == [u]},
                            {
                                "numeric",
                                "memory",
                                "compute_fp32",
                                "compute",
                                "inference",
                                "training",
                                "vision",
                            },
                        )
                    for s in b:
                        self.assertEqual(s["uuids"], ids)
                    self.assertEqual(
                        sum(s["stage"] == "collective" for s in b), int(n > 1)
                    )

    def test_uuid_identity_survives_index_order_change(self):
        inv = [gpu(i) for i in range(4)]
        inv[0]["index"], inv[3]["index"] = 3, 0
        self.assertEqual(
            {x["uuid"] for x in select_gpus(inv, uuid(0) + "," + uuid(3))},
            {uuid(0), uuid(3)},
        )

    def test_selection_rejects_invalid_duplicate_and_over_eight(self):
        for selector in ("0,0", "0," + uuid(0), "9", "", "all"):
            with self.subTest(selector=selector), self.assertRaises(QAError):
                select_gpus([gpu(i) for i in range(9)], selector)

    def test_qualification_is_specific_to_count_driver_and_build(self):
        a = [gpu(0), gpu(1)]
        probes = [{"torch": "x", "cuda": "y", "capability": [8, 6]}] * 2
        key = qualification_key(a, probes, "a")
        self.assertNotEqual(key, qualification_key(a[:1], probes[:1], "a"))
        self.assertNotEqual(key, qualification_key(a, probes, "b"))
        a[0]["driver"] = "changed"
        self.assertNotEqual(key, qualification_key(a, probes, "a"))


class CompletionTests(unittest.TestCase):
    def test_all_ranks_required(self):
        for n in (2, 4, 8):
            records = [
                dict(
                    event="complete",
                    stage="collective",
                    rank=i,
                    world_size=n,
                    checks=9,
                    active_seconds=12,
                )
                for i in range(n)
            ]
            self.assertEqual(
                validate_completions(
                    records, "collective", [uuid(i) for i in range(n)]
                ),
                12,
            )
            for bad in (
                records[:-1],
                records + records[:1],
                [dict(r, world_size=1) for r in records],
            ):
                with self.assertRaises(QAError):
                    validate_completions(bad, "collective", [uuid(i) for i in range(n)])

    def test_clean_exit_missing_empty_wrong_and_nan_completion_rejected(self):
        good = dict(event="complete", stage="memory", checks=38, active_seconds=5)
        for bad in (
            [],
            [dict(good, stage="other")],
            [good, good],
            [dict(good, checks=0)],
            [dict(good, active_seconds=float("nan"))],
            [dict(good, active_seconds="5")],
        ):
            with self.assertRaises(QAError):
                validate_completions(bad, "memory", [uuid(0)])

    def test_reboot_and_stale_heartbeat_cannot_pass(self):
        base = dict(
            status="RUNNING", boot_id="a", heartbeat=100, production_qualified=False
        )
        self.assertEqual(effective_result(base, "b", 101)["status"], "INCOMPLETE")
        self.assertEqual(effective_result(base, "a", 221)["status"], "INCOMPLETE")
        self.assertEqual(effective_result(base, "a", 101)["status"], "RUNNING")
        self.assertEqual(
            effective_result(dict(base, status="FAIL"), "b", 999)["status"], "FAIL"
        )

    def test_failed_start_does_not_stay_starting(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)
            atomic_json(p / "request.json", {"profile": "production"})
            self.assertEqual(pending_result(p)["status"], "STARTING")
            self.assertEqual(
                pending_result(p, time.time() + 121)["status"], "INCOMPLETE"
            )
            (p / "start-error.txt").touch()
            self.assertEqual(pending_result(p)["status"], "INCOMPLETE")

    def test_invalid_setup_publishes_terminal_report(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / RUN
            p.mkdir()
            atomic_json(p / "request.json", {"profile": "validate"})
            self.assertEqual(run_supervisor(p, p / "missing.json"), 2)
            self.assertEqual(
                json.loads((p / "result.json").read_text())["status"], "BLOCKED"
            )
            self.assertTrue((p / "report.html").exists())


class HealthTests(unittest.TestCase):
    def test_parse_xml_capabilities_and_pending_counter(self):
        xml = f"""<nvidia_smi_log><driver_version>TEST</driver_version><gpu id="00000000:01:00.0"><uuid>{uuid(0)}</uuid><product_name>Fake</product_name><ecc_mode><current_ecc>Enabled</current_ecc></ecc_mode><temperature><gpu_temp>40 C</gpu_temp><gpu_temp_tlimit>45 C</gpu_temp_tlimit></temperature><remapped_rows><pending>Yes</pending><bank_remap_availability_histogram><max>8</max></bank_remap_availability_histogram></remapped_rows><mig_mode><current_mig>Disabled</current_mig></mig_mode></gpu></nvidia_smi_log>"""
        g = parse_snapshot(xml)[0]
        self.assertEqual(g["temperature_c"], 40)
        self.assertEqual(g["counters"], {"remapped_rows/pending": 1})
        with self.assertRaises(QAError):
            parse_snapshot(xml.replace("<current_mig>Disabled", "<current_mig>Enabled"))

    def test_missing_gpu_or_counter_reset_and_new_error(self):
        b = [gpu(0)]
        self.assertFalse(health_faults(b, copy.deepcopy(b)))
        self.assertEqual(health_faults(b, [])[0][1], "device_lost")
        for key in ("pcie_replays", "sw_thermal_us", "hw_thermal_us", "power_brake_us"):
            c = copy.deepcopy(b)
            c[0][key] = 1
            self.assertEqual(health_faults(b, c)[0][0], "FAIL")
            c[0][key] = -1
            self.assertEqual(health_faults(b, c)[0][0], "INCOMPLETE")
            c[0][key] = None
            self.assertEqual(health_faults(b, c)[0][0], "INCOMPLETE")
        c = copy.deepcopy(b)
        c[0]["counters"]["ecc_errors/volatile/uncorrected"] = 1
        self.assertEqual(health_faults(b, c)[0][0], "FAIL")

    def test_temperature_margin_and_configuration_change(self):
        for key, value, verdict in [
            ("thermal_margin_c", 3, "FAIL"),
            ("temperature_c", None, "INCOMPLETE"),
            ("driver", "changed", "INCOMPLETE"),
            ("power_limit_w", 250, "INCOMPLETE"),
        ]:
            c = [gpu(0)]
            c[0][key] = value
            self.assertEqual(health_faults([gpu(0)], c)[0][0], verdict)

    def test_xid_attribution_and_platform_errors(self):
        msg = "NVRM: Xid (PCI:0000:01:00): 8, test fault"
        self.assertEqual(kernel_faults(msg, [gpu(0)])["uuid"], uuid(0))
        self.assertIsNone(kernel_faults(msg, [gpu(1)]))
        self.assertEqual(
            kernel_faults("AER: Uncorrected (Fatal) error received", [gpu(0)])["kind"],
            "platform",
        )


class ProcessTests(unittest.TestCase):
    def supervisor(self, d):
        s = Supervisor.__new__(Supervisor)
        s.path = pathlib.Path(d)
        s.children = []
        s.monitor = lambda: None
        return s

    def test_subprocesses_get_isolated_uuid_environments(self):
        with tempfile.TemporaryDirectory() as d:
            s = self.supervisor(d)
            jobs = [
                dict(
                    argv=[
                        sys.executable,
                        "-c",
                        'import os,json;print(json.dumps({"uuid":os.environ["CUDA_VISIBLE_DEVICES"]}))',
                    ],
                    uuids=[uuid(i)],
                    label=str(i),
                )
                for i in range(8)
            ]
            results = s.execute_many(jobs, 5)
            self.assertEqual(
                [r[0][0]["uuid"] for r in results], [uuid(i) for i in range(8)]
            )
            self.assertFalse(s.children)

    def test_nonzero_exit_stops_other_owned_worker(self):
        with tempfile.TemporaryDirectory() as d:
            s = self.supervisor(d)
            jobs = [
                dict(argv=[sys.executable, "-c", code], uuids=[uuid(i)], label=str(i))
                for i, code in enumerate(
                    ["raise SystemExit(3)", "import time;time.sleep(30)"]
                )
            ]
            start = time.monotonic()
            with self.assertRaises(QAError) as e:
                s.execute_many(jobs, 5)
            self.assertEqual(e.exception.verdict, "FAIL")
            self.assertLess(time.monotonic() - start, 5)
            self.assertFalse(s.children)

    def test_timeout_and_operator_monitor_abort(self):
        for abort in (False, True):
            with tempfile.TemporaryDirectory() as d:
                s = self.supervisor(d)
                if abort:

                    def monitor():
                        raise QAError("stop", "INCOMPLETE", "operator_stop")

                    s.monitor = monitor
                with self.assertRaises(QAError) as e:
                    s.execute(
                        [sys.executable, "-c", "import time;time.sleep(30)"],
                        [uuid(0)],
                        "worker",
                        0.05,
                    )
                self.assertEqual(e.exception.verdict, "INCOMPLETE" if abort else "FAIL")
                self.assertFalse(s.children)

    def test_conflicting_gpu_lock_blocks_only_same_uuid(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            a = root / "runs" / "a"
            b = root / "runs" / "b"
            a.mkdir(parents=True)
            b.mkdir()
            s = self.supervisor(a)
            t = self.supervisor(b)
            s.locks = []
            t.locks = []
            try:
                s.acquire([uuid(0)])
                with self.assertRaises(QAError):
                    t.acquire([uuid(0)])
                t.acquire([uuid(1)])
            finally:
                for f in s.locks + t.locks:
                    f.close()


class ReportTests(unittest.TestCase):
    def test_html_escapes_evidence_and_covers_all_gpus(self):
        with tempfile.TemporaryDirectory() as d:
            s = state(8)
            s["error"] = "<script>bad</script>"
            p = build_report(d, s)
            text = p.read_text()
            self.assertNotIn("<script>", text)
            self.assertIn("&lt;script&gt;", text)
            for i in range(8):
                self.assertIn(uuid(i), text)
            self.assertIn("not a production release approval", text)

    def test_rma_hashes_sanitization_and_archive_path_guard(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)
            s = state(2)
            s["status"] = "FAIL"
            s["error"] = "token=secret-value at 192.0.2.1"
            s["password"] = "do-not-export"
            s["stages"] = [
                dict(
                    scope="individual",
                    stage="memory",
                    uuids=[uuid(0)],
                    status="FAIL",
                    logs=["worker.log", "../escape.log"],
                )
            ]
            (p / "worker.log").write_text(
                "Bearer abc123 at /home/example/private 192.0.2.2\n"
            )
            z, pdf = export_rma(p, s)
            self.assertTrue(pdf.read_bytes().startswith(b"%PDF"))
            with zipfile.ZipFile(z) as a:
                self.assertNotIn("../escape.log", a.namelist())
                safe = json.loads(a.read("result.json"))
                self.assertEqual(safe["password"], "[REDACTED]")
                content = a.read("evidence/worker.log").decode()
                self.assertNotIn("abc123", content)
                self.assertNotIn("192.0.2.2", content)
                for line in a.read("SHA256SUMS.txt").decode().splitlines():
                    h, name = line.split("  ", 1)
                    self.assertEqual(hashlib.sha256(a.read(name)).hexdigest(), h)

    def test_run_id_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(QAError):
                run_path(pathlib.Path(d), "../../etc")

    def test_credentials_removed_from_workers(self):
        with patch.dict(
            os.environ,
            {"EXAMPLE_API_KEY": "secret", "MY_TOKEN": "secret", "NORMAL": "ok"},
        ):
            env = worker_env([uuid(0)])
            self.assertNotIn("EXAMPLE_API_KEY", env)
            self.assertNotIn("MY_TOKEN", env)
            self.assertEqual(env["NORMAL"], "ok")

    def test_modified_dependency_cannot_keep_qualification(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)
            tool = p / "tool"
            tool.write_text("original")
            model = p / "model"
            model.mkdir()
            weight = model / "model.safetensors"
            weight.write_text("test")
            manifest = p / "manifest.json"
            raw = json.dumps(
                {
                    "files": {
                        str(x): hashlib.sha256(x.read_bytes()).hexdigest()
                        for x in (tool, weight)
                    }
                }
            )
            manifest.write_text(raw)
            c = dict(
                manifest=str(manifest),
                dependency_fingerprint=hashlib.sha256(raw.encode()).hexdigest(),
                memory_test=str(tool),
                gpu_burn=str(tool),
                nvbandwidth=str(tool),
                model_path=str(model),
            )
            verify_manifest(c)
            tool.write_text("modified")
            with self.assertRaises(QAError):
                verify_manifest(c)


class FullRunTests(unittest.TestCase):
    """Exercise the actual orchestration/verdict path with simulated stage results."""

    def fake(self, d, n=1, profile="validate", qualified=False, pilot=True):
        s = Supervisor.__new__(Supervisor)
        s.path = pathlib.Path(d)
        s.state = state(n)
        s.state.update(status="STARTING", profile=profile)
        s.request = dict(profile=profile, pilot=pilot, stage_seconds=1)
        s.children = []
        s.locks = []
        s.journal = None
        s.journal_file = None
        s.factory_validated = qualified
        s.uuids = [uuid(i) for i in range(n)]
        s.save = lambda: atomic_json(s.path / "result.json", s.state)

        def preflight():
            if profile in ("production", "rma") and not qualified and not pilot:
                raise QAError("Unqualified configuration")
            s.state["status"] = "RUNNING"

        s.preflight = preflight
        s.bandwidth_reference = lambda label: None
        s.monitor = lambda: None
        s.validate_memory_tool = lambda: None

        def stage(spec):
            s.state["stages"].append(dict(spec, status="PASS"))
            for u in spec["uuids"]:
                s.state["coverage"].append(f"{spec['scope']}:{spec['stage']}:{u}")
                s.state["per_gpu_active_seconds"][u] += 100000

        s.stage = stage
        return s

    def run_fake(self, s):
        handlers = {x: signal.getsignal(x) for x in (signal.SIGTERM, signal.SIGINT)}
        try:
            with patch("gpuqa.supervisor.snapshot", return_value=([], "<test/>")):
                return s.run()
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)

    def test_complete_simulated_one_two_four_eight(self):
        for n in (1, 2, 4, 8):
            with tempfile.TemporaryDirectory() as d:
                s = self.fake(d, n)
                self.assertEqual(self.run_fake(s), 0)
                self.assertEqual(s.state["status"], "PILOT_PASS")
                self.assertFalse(s.state["production_qualified"])

    def test_production_gate_and_quick_screen(self):
        for profile, pilot, qualified, verdict in [
            ("production", False, False, "BLOCKED"),
            ("production", True, False, "PILOT_PASS"),
            ("quick", False, False, "SCREEN_PASS"),
            ("production", False, True, "PASS"),
            ("production", True, True, "PILOT_PASS"),
        ]:
            with tempfile.TemporaryDirectory() as d:
                s = self.fake(d, 2, profile, qualified, pilot)
                self.run_fake(s)
                self.assertEqual(s.state["status"], verdict)
                self.assertEqual(s.state["production_qualified"], verdict == "PASS")

    def test_missing_gpu_stage_blocks_final_pass(self):
        with tempfile.TemporaryDirectory() as d:
            s = self.fake(d, 8)
            original = s.stage

            def stage(spec):
                if spec["stage"] == "training":
                    return
                original(spec)

            s.stage = stage
            self.assertEqual(self.run_fake(s), 2)
            self.assertEqual(s.state["status"], "INCOMPLETE")

    def test_failure_never_reaches_final_pass(self):
        with tempfile.TemporaryDirectory() as d:
            s = self.fake(d, 4)

            def stage(spec):
                raise QAError("Injected mismatch", "FAIL", "calculation")

            s.stage = stage
            self.assertEqual(self.run_fake(s), 2)
            self.assertEqual(s.state["status"], "FAIL")
            self.assertEqual(len(s.state["stages"]), 0)

    def test_every_peer_pair_required(self):
        for n in (2, 4, 8):
            c = dict(
                event="complete",
                stage="peer",
                world_size=n,
                checks=n * (n - 1),
                active_seconds=1,
                pairs=[[i, j] for i in range(n) for j in range(n) if i != j],
            )
            validate_completions([c], "peer", [uuid(i) for i in range(n)])
            c["pairs"].pop()
            with self.assertRaises(QAError):
                validate_completions([c], "peer", [uuid(i) for i in range(n)])


class ShellIntegrationTests(unittest.TestCase):
    def test_help_preserves_alias_and_is_idempotent(self):
        fragment = pathlib.Path(__file__).resolve().parents[1] / "bizonhelp-gpu-qa.bash"
        code = (
            'alias bizonhelp="echo ORIGINAL_HELP"; source "$1"; source "$1"; bizonhelp'
        )
        r = subprocess.run(
            ["bash", "-O", "expand_aliases", "-c", code, "test", str(fragment)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("ORIGINAL_HELP"), 1)
        self.assertEqual(r.stdout.count("GPU QA (candidate"), 1)

    def test_help_preserves_function(self):
        fragment = pathlib.Path(__file__).resolve().parents[1] / "bizonhelp-gpu-qa.bash"
        code = 'bizonhelp() { echo ORIGINAL_FUNCTION; }; source "$1"; bizonhelp'
        r = subprocess.run(
            ["bash", "-c", code, "test", str(fragment)], capture_output=True, text=True
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ORIGINAL_FUNCTION", r.stdout)
        self.assertIn("gpu-qa status", r.stdout)


class ValidationControlTests(unittest.TestCase):
    def test_expected_corruption_exit_is_not_a_generic_success(self):
        for code in (0, 3):
            with tempfile.TemporaryDirectory() as d:
                s = Supervisor.__new__(Supervisor)
                s.path = pathlib.Path(d)
                s.children = []
                s.monitor = lambda: None
                jobs = [
                    dict(
                        argv=[sys.executable, "-c", f"raise SystemExit({code})"],
                        uuids=[uuid(0)],
                        label="injection",
                        success_codes=[3],
                    )
                ]
                if code == 3:
                    self.assertEqual(len(s.execute_many(jobs, 5)), 1)
                else:
                    with self.assertRaises(QAError):
                        s.execute_many(jobs, 5)

    def test_memory_control_requires_exact_injection_and_sanitizer(self):
        for errors, clean, accepted in [
            ([1], True, True),
            ([0], True, False),
            ([2], True, False),
            ([1], False, False),
        ]:
            with tempfile.TemporaryDirectory() as d:
                s = Supervisor.__new__(Supervisor)
                s.path = pathlib.Path(d)
                s.uuids = [uuid(0)]
                s.state = state()
                s.save = lambda: None
                tool = s.path / "fake-sanitizer"
                tool.touch()
                s.config = {"compute_sanitizer": str(tool), "memory_test": "unused"}
                s.execute_many = lambda jobs, timeout: [
                    ([{"event": "memory_pass", "errors": e} for e in errors], "")
                ]
                s.execute = lambda *args: (
                    [
                        {
                            "event": "complete",
                            "stage": "memory",
                            "checks": 38,
                            "active_seconds": 1,
                        }
                    ],
                    "ERROR SUMMARY: 0 errors" if clean else "ERROR SUMMARY: 1 errors",
                )
                if accepted:
                    s.validate_memory_tool()
                    self.assertEqual(s.state["stages"][0]["status"], "PASS")
                else:
                    with self.assertRaises(QAError):
                        s.validate_memory_tool()

    def test_new_pending_repair_is_detected(self):
        xml = f'<nvidia_smi_log><gpu id="00000000:01:00.0"><uuid>{uuid(0)}</uuid><temperature><gpu_temp>40</gpu_temp><gpu_temp_tlimit>40</gpu_temp_tlimit></temperature><remapped_rows><pending>No</pending></remapped_rows></gpu></nvidia_smi_log>'
        before = parse_snapshot(xml)
        after = parse_snapshot(xml.replace("<pending>No", "<pending>Yes"))
        self.assertEqual(before[0]["counters"]["remapped_rows/pending"], 0)
        self.assertTrue(
            any(f[1] == "memory_health" for f in health_faults(before, after))
        )

    def test_workload_performance_is_in_html(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)
            s = state()
            name = "inference-" + uuid(0) + ".log"
            s["stages"] = [
                dict(
                    scope="individual",
                    stage="inference",
                    uuids=[uuid(0)],
                    status="PASS",
                    logs=[name],
                )
            ]
            (p / name).write_text(
                json.dumps(
                    dict(event="inference", batch=2, context=128, decode_tokens_s=123.5)
                )
                + "\n"
            )
            report = build_report(p, s).read_text()
            self.assertIn("123.5", report)
            self.assertIn("decode_tokens_s", report)
            self.assertIn("batch=2, context=128", report)


if __name__ == "__main__":
    unittest.main()
