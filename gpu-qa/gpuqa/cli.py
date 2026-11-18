import argparse
import datetime
import json
import os
import pathlib
import subprocess
import sys
import uuid
from .common import (
    PROFILES,
    RUN_RE,
    QAError,
    atomic_json,
    read_json,
    boot_id,
    effective_result,
    pending_result,
)
from .hardware import snapshot, select_gpus
from .reporting import build_report, export_rma


def parser():
    p = argparse.ArgumentParser(
        prog="gpu-qa",
        description="BIZON GPU workload validation (1-8 physical NVIDIA GPUs).",
    )
    p.add_argument("--state-dir", default="/var/lib/bizon-gpu-qa")
    p.add_argument("--config", default="/etc/bizon-gpu-qa/config.json")
    sub = p.add_subparsers(dest="command", required=True)
    for name in PROFILES:
        q = sub.add_parser(name)
        q.add_argument("--gpus", default="all")
        q.add_argument("--pilot", action="store_true")
        q.add_argument(
            "--foreground",
            action="store_true",
            help="Development/pilot use; terminal interruption stops the run",
        )
        if name == "validate":
            q.add_argument("--stage-seconds", type=int, default=10)
    for name in ("status", "stop", "report", "export-rma"):
        q = sub.add_parser(name)
        sel = q.add_mutually_exclusive_group()
        sel.add_argument("--run-id")
        sel.add_argument("--latest", action="store_true")
        q.add_argument("--json", action="store_true")
    sub.add_parser("inventory")
    sub.add_parser("doctor")
    q = sub.add_parser("_run", help=argparse.SUPPRESS)
    q.add_argument("run_id")
    return p


def run_path(root, run_id=None):
    runs = root / "runs"
    if not run_id:
        candidates = sorted(
            p.name for p in runs.glob("*") if p.is_dir() and RUN_RE.fullmatch(p.name)
        )
        if not candidates:
            raise QAError("No QA runs found")
        run_id = candidates[-1]
    if not RUN_RE.fullmatch(run_id):
        raise QAError("Invalid run ID")
    p = runs / run_id
    if not p.is_dir():
        raise QAError("Run not found")
    return p


def run_supervisor(path, config):
    from .supervisor import Supervisor

    try:
        return Supervisor(path, config).run()
    except (QAError, OSError, ValueError) as e:
        request = read_json(path / "request.json")
        state = dict(
            run_id=path.name,
            profile=request.get("profile", "unknown"),
            status="BLOCKED",
            production_qualified=False,
            error="Setup failed: " + str(e),
        )
        atomic_json(path / "result.json", state)
        build_report(path, state)
        return 2


def main(argv=None):
    a = parser().parse_args(argv)
    root = pathlib.Path(a.state_dir)
    try:
        if a.command == "inventory":
            g, _ = snapshot()
            print(json.dumps(g, indent=2))
            return 0
        if a.command == "doctor":
            from .common import load_config

            c = load_config(a.config)
            g, _ = snapshot()
            print(
                json.dumps(
                    {
                        "status": "DEPENDENCIES_PRESENT",
                        "gpu_count": len(g),
                        "config": a.config,
                        "note": "Runtime, model, journal access and GPU conflicts are checked again under locks when a run starts.",
                    },
                    indent=2,
                )
            )
            return 0
        if a.command == "_run":
            from .supervisor import Supervisor

            if os.geteuid() == 0:
                raise QAError(
                    "GPU workloads must run as the unprivileged service account"
                )
            p = run_path(root, a.run_id)
            request = read_json(p / "request.json")
            if (p / "result.json").exists():
                raise QAError(
                    "Refusing to overwrite an existing run; create a new run ID"
                )
            return run_supervisor(p, a.config)
        if a.command in PROFILES:
            # Read-only inventory verifies selectors before creating a service request.
            devices, _ = snapshot()
            chosen = select_gpus(devices, a.gpus)
            seconds = getattr(a, "stage_seconds", 10)
            if not 1 <= seconds <= 60:
                raise QAError("Validation stage seconds must be in 1..60")
            run_id = (
                datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
                + uuid.uuid4().hex[:8]
            )
            request = {
                "profile": a.command,
                "gpus": ",".join(g["uuid"] for g in chosen),
                "pilot": a.pilot or a.command == "validate",
                "stage_seconds": seconds,
            }
            if a.foreground:
                if os.geteuid() == 0:
                    raise QAError("Foreground GPU tests must not run as root")
                p = root / "runs" / run_id
                p.mkdir(parents=True, exist_ok=False)
                atomic_json(p / "request.json", request)
                from .supervisor import Supervisor

                return run_supervisor(p, a.config)
            # This installed root-owned helper has fixed service/config locations and validates all input.
            helper = "/usr/local/libexec/gpu-qa-start"
            cmd = [
                "sudo",
                helper,
                run_id,
                a.command,
                request["gpus"],
                "1" if request["pilot"] else "0",
                str(seconds),
            ]
            subprocess.run(cmd, check=True)
            print(
                json.dumps(
                    {
                        "accepted": True,
                        "run_id": run_id,
                        "status": "STARTING",
                        "note": "Accepted does not mean passed. Use gpu-qa status.",
                    }
                )
            )
            return 0
        p = run_path(root, a.run_id)
        if not (p / "result.json").exists():
            s = pending_result(p)
        else:
            s = effective_result(read_json(p / "result.json"), boot_id())
        if a.command == "status":
            if a.json:
                print(json.dumps(s, indent=2))
            else:
                print(
                    f"{s['run_id']}  {s['status']}  production_qualified={s.get('production_qualified',False)}"
                )
                print(
                    f"Phase: {s.get('current_stage') or '-'} | elapsed {s.get('elapsed_seconds',0)/3600:.2f} h"
                )
                if s.get("error"):
                    print(s["error"])
                print("Report:", p / "report.html")
            return 0
        if a.command == "stop":
            if s["status"] in (
                "PASS",
                "SCREEN_PASS",
                "PILOT_PASS",
                "FAIL",
                "INCOMPLETE",
                "BLOCKED",
            ):
                raise QAError("Run is already terminal")
            (p / "STOP").touch()
            print("Stop requested for test-owned workloads only.")
            return 0
        if a.command == "report":
            print(build_report(p, s))
            return 0
        if a.command == "export-rma":
            if s["status"] in ("RUNNING", "STARTING"):
                raise QAError(
                    "Wait for a terminal result before exporting RMA evidence"
                )
            z, pdf = export_rma(p, s)
            print(json.dumps({"zip": str(z), "pdf": str(pdf)}))
            return 0
    except (
        QAError,
        PermissionError,
        FileNotFoundError,
        subprocess.CalledProcessError,
    ) as e:
        print(json.dumps({"status": "BLOCKED", "error": str(e)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
