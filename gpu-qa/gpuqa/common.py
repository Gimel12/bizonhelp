import math
import hashlib
import json
import os
import pathlib
import re
import subprocess
import time

VERSION = "0.1.0"
TERMINAL = {"PASS", "SCREEN_PASS", "PILOT_PASS", "FAIL", "INCOMPLETE", "BLOCKED"}
PROFILES = {"quick": 1800, "production": 28800, "rma": 86400, "validate": 0}
UUID_RE = re.compile(r"GPU-[a-fA-F0-9-]{16,64}\Z")
RUN_RE = re.compile(r"[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}\Z")
PACKAGE = pathlib.Path(__file__).resolve().parent.parent


class QAError(Exception):
    def __init__(self, message, verdict="BLOCKED", category="environment"):
        super().__init__(message)
        self.verdict, self.category = verdict, category


def command(argv, timeout=30, env=None):
    try:
        return subprocess.check_output(
            argv, stderr=subprocess.STDOUT, text=True, timeout=timeout, env=env
        )
    except (OSError, subprocess.SubprocessError) as e:
        # Never echo an entire environment or arbitrary subprocess output into a report.
        raise QAError(
            f"{pathlib.Path(argv[0]).name} command failed: {type(e).__name__}"
        ) from e


def atomic_json(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w") as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def read_json(path):
    return json.loads(pathlib.Path(path).read_text())


def boot_id():
    return pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def source_hash():
    h = hashlib.sha256()
    paths = list((PACKAGE / "gpuqa").glob("*.py")) + [PACKAGE / "memory_test.cu"]
    for p in sorted(paths):
        h.update(str(p.relative_to(PACKAGE)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def verify_manifest(config):
    path = pathlib.Path(config.get("manifest", ""))
    if not path.is_file():
        raise QAError("Missing prepared dependency manifest")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != config.get("dependency_fingerprint"):
        raise QAError("Dependency manifest changed since setup")
    data = json.loads(raw)
    required = {config[k] for k in ("memory_test", "gpu_burn", "nvbandwidth")}
    required.update(
        str(p) for p in pathlib.Path(config["model_path"]).glob("*.safetensors")
    )
    if not required.issubset(data.get("files", {})):
        raise QAError("Dependency manifest lacks required artifacts")
    for name, expected in data["files"].items():
        h = hashlib.sha256()
        with open(name, "rb") as f:
            for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
                h.update(chunk)
        if h.hexdigest() != expected:
            raise QAError("Dependency content changed: " + pathlib.Path(name).name)
    if data.get("worker_packages"):
        actual = command([config["python"], "-m", "pip", "freeze"], 60)
        if sorted(actual.splitlines()) != sorted(data["worker_packages"].splitlines()):
            raise QAError("Worker Python dependencies changed since setup")
    return data


def load_config(path):
    c = read_json(path)
    for key in (
        "python",
        "model_path",
        "model_revision",
        "gpu_burn",
        "nvbandwidth",
        "memory_test",
    ):
        if not c.get(key):
            raise QAError(f"Missing setup configuration: {key}")
    for key in ("python", "model_path", "gpu_burn", "nvbandwidth", "memory_test"):
        if not pathlib.Path(c[key]).is_absolute() or not pathlib.Path(c[key]).exists():
            raise QAError(f"Missing absolute dependency path: {key}")
    if not re.fullmatch(r"[a-f0-9]{40}", c["model_revision"]):
        raise QAError("Model revision must be an immutable 40-character commit")
    if not 0.5 <= c.get("memory_fraction", 0.90) <= 0.94:
        raise QAError("memory_fraction must be between 0.50 and 0.94")
    return c


def worker_env(uuids):
    e = dict(os.environ)
    # Offline workloads never inherit API credentials from the invoking shell.
    for key in list(e):
        if any(s in key.upper() for s in ("TOKEN", "SECRET", "PASSWORD", "API_KEY")):
            del e[key]
    e.update(
        CUDA_VISIBLE_DEVICES=",".join(uuids),
        CUDA_DEVICE_ORDER="PCI_BUS_ID",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        TOKENIZERS_PARALLELISM="false",
        PYTHONUNBUFFERED="1",
        OMP_NUM_THREADS="2",
        CUBLAS_WORKSPACE_CONFIG=":4096:8",
    )
    return e


def effective_result(state, current_boot=None, now=None):
    result = dict(state)
    if state.get("status") not in TERMINAL:
        if current_boot is not None and current_boot != state.get("boot_id"):
            result.update(
                status="INCOMPLETE",
                error="Host rebooted before completion",
                production_qualified=False,
            )
        elif (time.time() if now is None else now) - state.get("heartbeat", 0) > 120:
            result.update(
                status="INCOMPLETE",
                error="Supervisor heartbeat is stale",
                production_qualified=False,
            )
    return result


def pending_result(run_dir, now=None):
    p = pathlib.Path(run_dir)
    request = read_json(p / "request.json")
    age = (time.time() if now is None else now) - (p / "request.json").stat().st_mtime
    failed = (p / "start-error.txt").exists() or age > 120
    return dict(
        run_id=p.name,
        profile=request.get("profile", "unknown"),
        status="INCOMPLETE" if failed else "STARTING",
        production_qualified=False,
        error="Service failed to start or publish a heartbeat" if failed else None,
    )
