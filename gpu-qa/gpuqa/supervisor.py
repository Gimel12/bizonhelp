import fcntl
import hashlib
import math
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess as sp
import time
from .common import (
    QAError,
    PACKAGE,
    PROFILES,
    TERMINAL,
    atomic_json,
    read_json,
    command,
    boot_id,
    source_hash,
    worker_env,
    load_config,
    verify_manifest,
)
from .hardware import (
    snapshot,
    select_gpus,
    busy_processes,
    health_faults,
    kernel_faults,
)
from .reporting import build_report


def make_plan(uuids, profile, stage_seconds=10):
    """All cardinalities use the same UUID-based coverage contract, including heterogeneous sets."""
    short = profile in ("quick", "validate")
    individual = []
    for u in uuids:
        for mode in (
            "numeric",
            "memory",
            "compute_fp32",
            "compute",
            "inference",
            "training",
            "vision",
        ):
            individual.append(
                {
                    "stage": mode,
                    "scope": "individual",
                    "uuids": [u],
                    "seconds": (
                        stage_seconds
                        if profile == "validate"
                        else (15 if short else 60)
                    ),
                }
            )
    shared = []
    for mode, seconds in [
        ("memory", 90),
        ("compute_fp32", 90),
        ("compute", 180),
        ("bandwidth", 30),
        ("inference", 120),
        ("training", 180),
        ("vision", 30),
    ]:
        shared.append(
            {
                "stage": mode,
                "scope": "simultaneous",
                "uuids": list(uuids),
                "seconds": (
                    stage_seconds
                    if profile == "validate"
                    else (min(seconds, 30) if short else seconds)
                ),
            }
        )
    if len(uuids) > 1:
        for stage in ("peer", "collective"):
            shared.append(
                {
                    "stage": stage,
                    "scope": "multi_gpu",
                    "uuids": list(uuids),
                    "seconds": stage_seconds if profile == "validate" else 60,
                }
            )
    return individual, shared


def validate_completions(records, stage, uuids):
    """A clean process exit alone never establishes correctness or required-rank coverage."""
    completed = [
        r for r in records if r.get("event") == "complete" and r.get("stage") == stage
    ]
    if stage == "collective":
        if {r.get("rank") for r in completed} != set(range(len(uuids))):
            raise QAError("Missing NCCL rank completion", "INCOMPLETE", "coverage")
        if len(completed) != len(uuids) or any(
            r.get("world_size") != len(uuids) for r in completed
        ):
            raise QAError(
                "Invalid NCCL world size/completion", "INCOMPLETE", "coverage"
            )
    elif len(completed) != 1:
        raise QAError(
            f"Missing or duplicate {stage} completion", "INCOMPLETE", "coverage"
        )
    if any(
        not isinstance(r.get("checks"), (int, float))
        or not isinstance(r.get("active_seconds"), (int, float))
        or not math.isfinite(r["checks"])
        or not math.isfinite(r["active_seconds"])
        or r["checks"] <= 0
        or r["active_seconds"] <= 0
        for r in completed
    ):
        raise QAError("Empty workload completion", "INCOMPLETE", "coverage")
    if stage == "peer":
        expected = {
            (i, j) for i in range(len(uuids)) for j in range(len(uuids)) if i != j
        }
        c = completed[0]
        if (
            c.get("world_size") != len(uuids)
            or {tuple(x) for x in c.get("pairs", [])} != expected
        ):
            raise QAError("Missing GPU-to-GPU pair coverage", "INCOMPLETE", "coverage")
    return min(r["active_seconds"] for r in completed)


def qualification_key(devices, probes, build):
    # A 2-GPU pilot cannot silently qualify 4/8 GPUs, different drivers or power limits.
    return json.dumps(
        {
            "build": build,
            "devices": [
                {
                    k: g.get(k)
                    for k in (
                        "name",
                        "memory_total_mib",
                        "driver",
                        "ecc",
                        "power_limit_w",
                        "display",
                    )
                }
                for g in devices
            ],
            "runtime": [
                {
                    k: p.get(k)
                    for k in (
                        "python",
                        "torch",
                        "cuda",
                        "cudnn",
                        "transformers",
                        "capability",
                    )
                }
                for p in probes
            ],
        },
        sort_keys=True,
    )


class Supervisor:
    def __init__(self, run_dir, config_path):
        self.path = pathlib.Path(run_dir)
        self.request = read_json(self.path / "request.json")
        self.config = load_config(config_path)
        self.config_path = pathlib.Path(config_path)
        self.state = {
            "schema_version": 1,
            "run_id": self.path.name,
            "version": "0.1.0",
            "status": "STARTING",
            "profile": self.request["profile"],
            "pilot": bool(self.request.get("pilot")),
            "production_qualified": False,
            "start_time": time.time(),
            "heartbeat": time.time(),
            "boot_id": boot_id(),
            "stages": [],
            "events": [],
            "warnings": [
                "Finite workload validation cannot guarantee future reliability.",
                "Performance has no automatic hardware pass/fail threshold without a matched baseline.",
            ],
            "source_sha256": source_hash(),
            "error": None,
            "per_gpu_active_seconds": {},
            "coverage": [],
        }
        self.children = []
        self.locks = []
        self.journal = None
        self.journal_file = None
        self.journal_pos = 0
        self.journal_buffer = ""
        self.last_report = 0
        self.start_mono = time.monotonic()
        self.last_sample = 0
        self.last_heartbeat = 0

    def save(self):
        self.state["heartbeat"] = time.time()
        self.state["elapsed_seconds"] = time.monotonic() - self.start_mono
        atomic_json(self.path / "result.json", self.state)
        if time.monotonic() - self.last_report > 30 or self.state["status"] in TERMINAL:
            build_report(self.path)
            self.last_report = time.monotonic()

    def event(self, data):
        data = dict(time=time.time(), **data)
        self.state["events"].append(data)
        with (self.path / "events.jsonl").open("a") as f:
            f.write(json.dumps(data) + "\n")

    def acquire(self, uuids):
        lockdir = self.path.parent.parent / "locks"
        lockdir.mkdir(exist_ok=True)
        for u in sorted(uuids):
            f = (lockdir / (u + ".lock")).open("a+")
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                f.close()
                raise QAError("Selected GPU is already owned by another QA run")
            self.locks.append(f)

    def start_journal(self):
        # Explicitly require kernel journal readability; no silent monitoring downgrade.
        raw = command(["journalctl", "-k", "-n", "1", "-o", "json", "--no-pager"], 15)
        try:
            last = json.loads([l for l in raw.splitlines() if l.startswith("{")][-1])
            cursor = last["__CURSOR"]
        except (ValueError, IndexError, KeyError) as e:
            raise QAError(
                "Kernel journal access unavailable; use the installed service"
            ) from e
        self.journal_file = (self.path / "kernel.jsonl").open("w")
        self.journal = sp.Popen(
            [
                "journalctl",
                "-k",
                "--after-cursor",
                cursor,
                "--follow",
                "--no-pager",
                "-o",
                "json",
            ],
            stdout=self.journal_file,
            stderr=sp.STDOUT,
            start_new_session=True,
        )
        self.state["journal_cursor"] = cursor

    def preflight(self):
        devices, raw = snapshot()
        self.devices = select_gpus(devices, self.request["gpus"])
        self.uuids = [g["uuid"] for g in self.devices]
        self.acquire(self.uuids)
        if busy_processes(self.uuids):
            raise QAError(
                "Selected GPU has an existing compute workload; no processes were stopped"
            )
        self.state["devices"] = self.devices
        self.state["per_gpu_active_seconds"] = {u: 0.0 for u in self.uuids}
        (self.path / "initial-nvidia.xml").write_text(raw)
        (self.path / "topology.txt").write_text(command(["nvidia-smi", "topo", "-m"]))
        faults = health_faults(
            self.devices, self.devices, self.config.get("fallback_max_operating_c")
        )
        if faults:
            raise QAError(faults[0][2], faults[0][0], faults[0][1])
        for g in self.devices:
            if (g["memory_free_mib"] or 0) < 14000:
                raise QAError(
                    "At least 14,000 MiB free VRAM is required per selected GPU for this model/training profile"
                )
            if any(
                v and any(term in k for term in ("pending", "failure", "unrepairable"))
                for k, v in g["counters"].items()
            ):
                raise QAError(
                    "GPU has a pending memory repair/failure condition",
                    "FAIL",
                    "memory_health",
                )
            if g["ecc"] != "Enabled":
                self.state["warnings"].append(
                    g["uuid"]
                    + ": ECC is not enabled; ECC-protected operation is not validated."
                )
            if g["memory_temperature_c"] is None:
                self.state["warnings"].append(
                    g["uuid"] + ": memory junction temperature unavailable."
                )
            if g["display"] == "Enabled":
                self.state["warnings"].append(
                    g["uuid"]
                    + ": display is active; bounded kernels preserve watchdog protections."
                )
        if shutil.disk_usage(self.path).free < 5 * 1024**3:
            raise QAError("At least 5 GiB free result storage is required")
        model_bytes = sum(
            p.stat().st_size
            for p in pathlib.Path(self.config["model_path"]).glob("*.safetensors")
        )
        if not model_bytes:
            raise QAError("No local safetensors model weights prepared")
        meminfo = pathlib.Path("/proc/meminfo").read_text()
        available = int(re.search(r"MemAvailable:\s+(\d+)", meminfo)[1]) * 1024
        if available < 4 * 1024**3 + len(self.uuids) * max(1024**3, model_bytes * 2):
            raise QAError("Insufficient host RAM for simultaneous model workers")
        self.start_journal()
        manifest = verify_manifest(self.config)
        atomic_json(self.path / "software-manifest.json", manifest)
        self.probes = []
        for u in self.uuids:
            # Short probes are also supervised and GPU allocation happens only after locks.
            records = self.execute(
                [
                    self.config["python"],
                    str(PACKAGE / "gpuqa/worker.py"),
                    "probe",
                    "--config",
                    str(self.config_path),
                ],
                [u],
                "probe-" + u,
                120,
            )[0]
            probe = next((r for r in records if r.get("event") == "probe"), None)
            if not probe:
                raise QAError("Missing runtime capability probe")
            if tuple(probe.get("capability", [])) < (7, 5):
                raise QAError(
                    "This runtime requires Turing/CUDA capability 7.5 or newer"
                )
            self.probes.append(probe)
        self.state["runtime"] = self.probes
        key = qualification_key(
            self.devices,
            self.probes,
            self.state["source_sha256"]
            + "|"
            + json.dumps(
                {
                    k: self.config.get(k)
                    for k in (
                        "model_revision",
                        "dependency_fingerprint",
                        "memory_fraction",
                        "fallback_max_operating_c",
                    )
                },
                sort_keys=True,
            ),
        )
        key += (
            "|topology="
            + hashlib.sha256((self.path / "topology.txt").read_bytes()).hexdigest()
        )
        self.state["qualification_key"] = key
        self.factory_validated = key in self.config.get("qualified_configurations", [])
        if (
            self.request["profile"] in ("production", "rma")
            and not self.factory_validated
            and not self.request.get("pilot")
        ):
            raise QAError(
                "This build/hardware/runtime combination is not qualified. Use --pilot for validation; it cannot issue a production PASS."
            )
        self.state["tool_validation"] = (
            "qualified_configuration" if self.factory_validated else "pilot_only"
        )
        self.state["status"] = "RUNNING"
        self.save()

    def monitor(self):
        if (self.path / "STOP").exists():
            raise QAError("Operator requested stop", "INCOMPLETE", "operator_stop")
        if boot_id() != self.state["boot_id"]:
            raise QAError("Boot ID changed", "INCOMPLETE", "host_restart")
        if self.journal:
            if self.journal.poll() is not None:
                raise QAError("Kernel event stream stopped", "INCOMPLETE", "monitoring")
            with (self.path / "kernel.jsonl").open() as f:
                f.seek(self.journal_pos)
                new = f.read()
                self.journal_pos = f.tell()
            self.journal_buffer += new
            while "\n" in self.journal_buffer:
                line, self.journal_buffer = self.journal_buffer.split("\n", 1)
                try:
                    entry = json.loads(line)
                except ValueError:
                    if line.strip():
                        raise QAError(
                            "Invalid kernel event stream", "INCOMPLETE", "monitoring"
                        )
                    continue
                fault = kernel_faults(entry.get("MESSAGE", ""), self.devices)
                if fault:
                    self.event(fault)
                    raise QAError(fault["message"], "FAIL", "gpu_or_platform_fault")
        if time.monotonic() - self.last_sample >= 5:
            try:
                now, raw = snapshot()
            except QAError as e:
                raise QAError(str(e), "INCOMPLETE", "monitoring") from e
            selected = [g for g in now if g["uuid"] in self.uuids]
            (self.path / "latest-nvidia.xml").write_text(raw)
            with (self.path / "telemetry.jsonl").open("a") as f:
                f.write(
                    json.dumps(
                        {
                            "time": time.time(),
                            "stage": self.state.get("current_stage"),
                            "gpus": selected,
                        }
                    )
                    + "\n"
                )
            faults = health_faults(
                self.devices, now, self.config.get("fallback_max_operating_c")
            )
            self.last_sample = time.monotonic()
            if faults:
                for verdict, category, msg in faults:
                    self.event({"kind": category, "message": msg})
                v, c, msg = faults[0]
                raise QAError(msg, v, c)
            if shutil.disk_usage(self.path).free < 1024**3:
                raise QAError(
                    "Result storage reserve exhausted", "INCOMPLETE", "storage"
                )
        self.save()

    def kill_children(self):
        for child in self.children:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 10
        for child in self.children:
            try:
                child.wait(max(0.1, deadline - time.monotonic()))
            except sp.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
        self.children = []

    def execute(self, argv, uuids, label, timeout, cwd=None):
        """One supervised process, used for probes and independent bandwidth tool."""
        job = {"argv": argv, "uuids": uuids, "label": label, "cwd": cwd}
        return self.execute_many([job], timeout)[0]

    def execute_many(self, jobs, timeout):
        files = []
        paths = []
        start = time.monotonic()
        try:
            for job in jobs:
                path = self.path / (job["label"] + ".log")
                paths.append(path)
                f = path.open("w")
                files.append(f)
                child = sp.Popen(
                    job["argv"],
                    stdout=f,
                    stderr=sp.STDOUT,
                    env=worker_env(job["uuids"]),
                    cwd=job.get("cwd"),
                    start_new_session=True,
                )
                self.children.append(child)
            while any(p.poll() is None for p in self.children):
                self.monitor()
                for p, job in zip(self.children, jobs):
                    if p.poll() is not None and p.returncode not in job.get(
                        "success_codes", [0]
                    ):
                        raise QAError(
                            f"Worker exited {p.returncode}; inspect stage logs",
                            "FAIL",
                            "workload",
                        )
                if time.monotonic() - start > timeout:
                    raise QAError("Workload deadline exceeded", "FAIL", "timeout")
                time.sleep(0.5)
            self.monitor()
            if any(
                p.returncode not in job.get("success_codes", [0])
                for p, job in zip(self.children, jobs)
            ):
                raise QAError("Worker failed; inspect stage logs", "FAIL", "workload")
            out = []
            for f, path in zip(files, paths):
                f.flush()
                text = path.read_text(errors="replace")
                records = []
                for line in text.splitlines():
                    if line.startswith("{"):
                        try:
                            records.append(json.loads(line))
                        except ValueError:
                            pass
                out.append((records, text))
            return out
        finally:
            self.kill_children()
            for f in files:
                f.close()

    def stage(self, spec):
        index = len(self.state["stages"])
        prefix = f'{index:04d}-{spec["scope"]}-{spec["stage"]}'
        entry = dict(spec, status="RUNNING", start_time=time.time(), logs=[])
        self.state["stages"].append(entry)
        self.state["current_stage"] = prefix
        self.save()
        start = time.monotonic()
        jobs = []
        seconds = spec["seconds"]
        kind = spec["stage"]
        uuids = spec["uuids"]
        try:
            if kind in ("collective", "peer"):
                config = dict(
                    self.config, rendezvous=str(self.path / (prefix + ".rendezvous"))
                )
                workerconfig = self.path / (prefix + "-config.json")
                atomic_json(workerconfig, config)
                jobs = [
                    {
                        "argv": [
                            self.config["python"],
                            str(PACKAGE / "gpuqa/worker.py"),
                            kind,
                            "--seconds",
                            str(seconds),
                            "--config",
                            str(workerconfig),
                        ],
                        "uuids": uuids,
                        "label": prefix,
                    }
                ]
            else:
                for u in uuids:
                    if kind == "memory":
                        argv = [
                            self.config["memory_test"],
                            "--seconds",
                            str(seconds),
                            "--fraction",
                            str(self.config.get("memory_fraction", 0.90)),
                        ]
                        if self.request["profile"] == "validate":
                            argv += ["--mib", "256"]
                        cwd = None
                    elif kind in ("compute", "compute_fp32"):
                        argv = (
                            [self.config["gpu_burn"]]
                            + (["-tc"] if kind == "compute" else [])
                            + ["-m", "90%", str(seconds)]
                        )
                        cwd = str(pathlib.Path(self.config["gpu_burn"]).parent)
                    else:
                        argv = [
                            self.config["python"],
                            str(PACKAGE / "gpuqa/worker.py"),
                            kind,
                            "--seconds",
                            str(seconds),
                            "--config",
                            str(self.config_path),
                        ]
                        cwd = None
                    jobs.append(
                        {
                            "argv": argv,
                            "uuids": [u],
                            "label": prefix + "-" + u,
                            "cwd": cwd,
                        }
                    )
            entry["logs"] = [j["label"] + ".log" for j in jobs]
            results = self.execute_many(jobs, seconds + 300)
            active = []
            for job, (records, text) in zip(jobs, results):
                if kind in ("compute", "compute_fp32"):
                    if not re.search(r"GPU 0: OK", text) or re.search(
                        r"FAULTY|errors:\s*[1-9]", text, re.I
                    ):
                        raise QAError(
                            "GPU Burn did not verify clean completion",
                            "FAIL",
                            "calculation",
                        )
                    if not re.search(r"proc.d:\s*[1-9]", text):
                        raise QAError(
                            "GPU Burn performed no verified work",
                            "INCOMPLETE",
                            "coverage",
                        )
                    duration = seconds
                else:
                    duration = validate_completions(records, kind, job["uuids"])
                if kind not in ("numeric",) and duration < seconds * 0.95:
                    raise QAError(
                        "Worker completed before required active duration",
                        "INCOMPLETE",
                        "coverage",
                    )
                active.append(duration)
                for u in job["uuids"]:
                    self.state["per_gpu_active_seconds"][u] += duration
                    key = f'{spec["scope"]}:{kind}:{u}'
                    if key not in self.state["coverage"]:
                        self.state["coverage"].append(key)
            entry.update(
                status="PASS",
                wall_seconds=time.monotonic() - start,
                active_seconds=min(active),
                end_time=time.time(),
            )
            self.save()
        except QAError as e:
            entry.update(
                status=e.verdict,
                wall_seconds=time.monotonic() - start,
                end_time=time.time(),
                error=str(e),
            )
            raise

    def validate_memory_tool(self):
        sanitizer = self.config.get("compute_sanitizer")
        if not sanitizer or not pathlib.Path(sanitizer).is_file():
            raise QAError(
                "validate requires CUDA compute-sanitizer; prepare the toolkit path"
            )
        for u in self.uuids:
            label = "selftest-" + u
            entry = dict(
                scope="tool_validation",
                stage="memory_checker",
                uuids=[u],
                status="RUNNING",
                logs=[label + "-injection.log", label + "-sanitizer.log"],
            )
            self.state["stages"].append(entry)
            self.state["current_stage"] = label
            self.save()
            base = [self.config["memory_test"], "--seconds", "1", "--mib", "32"]
            result = self.execute_many(
                [
                    dict(
                        argv=base + ["--inject-error"],
                        uuids=[u],
                        label=label + "-injection",
                        success_codes=[3],
                    )
                ],
                120,
            )[0]
            records, text = result
            errors = [
                r.get("errors") for r in records if r.get("event") == "memory_pass"
            ]
            if errors != [1]:
                raise QAError(
                    "Memory checker failed the intentional corruption control",
                    "INCOMPLETE",
                    "tool_validation",
                )
            records, text = self.execute(
                [sanitizer, "--tool", "memcheck", "--error-exitcode", "99"] + base,
                [u],
                label + "-sanitizer",
                300,
            )
            validate_completions(records, "memory", [u])
            if not re.search(r"ERROR SUMMARY: 0 errors", text):
                raise QAError(
                    "Missing clean compute-sanitizer result",
                    "INCOMPLETE",
                    "tool_validation",
                )
            entry["status"] = "PASS"
            self.save()

    def bandwidth_reference(self, label):
        self.state["current_stage"] = label
        argv = [
            self.config["nvbandwidth"],
            "-b",
            "128",
            "-i",
            "3",
            "-t",
            "host_to_device_memcpy_ce",
            "device_to_host_memcpy_ce",
            "host_to_device_bidirectional_memcpy_ce",
            "device_to_host_bidirectional_memcpy_ce",
            "device_local_copy",
            "device_local_read_sm",
            "device_local_write_sm",
        ]
        records, text = self.execute(argv, self.uuids, label, 600)
        # Tool verification remains enabled. Require every requested test's results.
        for name in argv[argv.index("-t") + 1 :]:
            match = re.search(
                r"^SUM " + re.escape(name) + r"\s+([0-9.eE+-]+)", text, re.M
            )
            if not match or not math.isfinite(float(match[1])) or float(match[1]) <= 0:
                raise QAError(
                    "Missing/empty nvbandwidth result: " + name,
                    "INCOMPLETE",
                    "coverage",
                )
        self.state.setdefault("bandwidth_references", []).append(
            {"name": label, "log": label + ".log", "status": "PASS"}
        )

    def run(self):
        def stop(signum, frame):
            raise QAError("Service interrupted", "INCOMPLETE", "signal")

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            self.save()
            self.preflight()
            if self.request["profile"] == "validate":
                self.validate_memory_tool()
            self.bandwidth_reference("bandwidth-initial")
            individual, shared = make_plan(
                self.uuids,
                self.request["profile"],
                self.request.get("stage_seconds", 10),
            )
            required = {
                f'{s["scope"]}:{s["stage"]}:{u}'
                for s in individual + shared
                for u in s["uuids"]
            }
            for spec in individual:
                self.stage(spec)
            target = PROFILES[self.request["profile"]]
            while True:
                for spec in shared:
                    self.stage(spec)
                if min(self.state["per_gpu_active_seconds"].values()) >= target:
                    break
            self.bandwidth_reference("bandwidth-final")
            for u in self.uuids:
                self.stage(
                    {"stage": "numeric", "scope": "final", "uuids": [u], "seconds": 1}
                )
            self.last_sample = 0
            self.monitor()
            if not required.issubset(self.state["coverage"]):
                raise QAError(
                    "Required GPU/stage coverage missing", "INCOMPLETE", "coverage"
                )
            if self.request["profile"] == "quick":
                self.state["status"] = "SCREEN_PASS"
            elif (
                self.request["profile"] == "validate"
                or self.request.get("pilot")
                or not self.factory_validated
            ):
                self.state["status"] = "PILOT_PASS"
            else:
                self.state["status"] = "PASS"
                self.state["production_qualified"] = True
        except QAError as e:
            self.state.update(
                status=e.verdict,
                error=str(e),
                failure_category=e.category,
                production_qualified=False,
            )
        except BaseException as e:
            self.state.update(
                status="INCOMPLETE",
                error=type(e).__name__ + ": " + str(e),
                failure_category="supervisor",
                production_qualified=False,
            )
        finally:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            self.kill_children()
            if self.journal:
                self.journal.terminate()
                try:
                    self.journal.wait(5)
                except sp.TimeoutExpired:
                    self.journal.kill()
                    self.journal.wait()
                self.journal_file.close()
            for entry in self.state["stages"]:
                if entry.get("status") == "RUNNING":
                    entry.update(
                        status=self.state["status"], error=self.state.get("error")
                    )
            self.state["end_time"] = time.time()
            self.state["current_stage"] = None
            try:
                _, raw = snapshot()
                (self.path / "final-nvidia.xml").write_text(raw)
            except QAError:
                pass
            self.save()
            for f in self.locks:
                f.close()
        return 0 if self.state["status"] in ("PASS", "SCREEN_PASS", "PILOT_PASS") else 2
