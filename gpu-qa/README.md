# BIZON GPU QA 0.1.0 — release candidate

GPU QA adds real AI workloads and verifiable results to the existing `gpu-test` burn test. **This candidate is not approved for factory acceptance yet.** CPU-only tests and CUDA compilation have passed; physical GPU qualification and installation testing are pending. See [VALIDATION.md](VALIDATION.md).

## Team commands

After installation and a fresh login, `bizonhelp` lists these commands. Existing commands remain available.

```bash
gpu-qa inventory
gpu-qa doctor
gpu-qa validate --gpus all          # Short tool validation, including memory fault injection
gpu-qa quick --gpus all             # Screening: at least 30 active workload minutes/card
gpu-qa production --gpus all --pilot  # At least 8 active workload hours/card
gpu-qa rma --gpus all --pilot       # At least 24 active workload hours/card
gpu-qa status --latest
gpu-qa status --run-id RUN_ID --json
gpu-qa report --latest
gpu-qa export-rma --latest
gpu-qa stop --latest
```

Use `--gpus 0,1` or comma-separated full GPU UUIDs to select a subset. Selection is converted to physical UUIDs before launching. The same runner handles 1–8 GPUs, including odd counts. An 8-card system is not implicitly qualified by a successful 2-card test.

Normal runs execute as an unprivileged systemd service on the workstation, surviving SSH disconnects and laptop sleep. `--foreground` is for development only and does not survive terminal interruption. A host reboot interrupts the run; it is never automatically resumed or reported as a pass. Profiles repeat whole cycles, so wall time exceeds active workload time, especially with sequential isolation tests on eight cards.

## Workload coverage

| Stage | What is checked |
| --- | --- |
| Numerical reference | GPU FP64/FP32/FP16 and supported BF16 matrix results against a CPU reference |
| VRAM | Zero/one, alternating, walking-bit and address-dependent patterns; 90% of available memory; kernels limited to 64 MiB per launch |
| GPU Burn | Upstream FP32 and tensor workloads and their computation verification |
| Transfers | NVIDIA nvbandwidth host/device and local-memory measurements, plus verified pinned-memory round trips |
| AI inference | Pinned Qwen3-0.6B weights, several context lengths/batches, prefill/decode, finite and repeated-logit checks |
| AI training | Transformer forward/backward/FP32 AdamW update with mixed precision, finite losses/gradients and update checks |
| Vision training | cuDNN convolution forward/backward and optimizer updates |
| Multiple GPUs | Individual card isolation, concurrent loading, every directed GPU pair copy, NCCL all-reduce/all-gather and DDP training |

Peer tests record whether direct peer access is supported. A supported copy does not prove NVLink was used; some systems route through host memory. NCCL failures block acceptance rather than silently dropping the distributed stage. The initial distributed workload is a compact MLP, not a distributed large-language-model training benchmark.

The model and compiler targets are intentionally modest for portability. Larger customer models, quantized FP8/FP4, long-context extremes, MIG, multi-node networking, and every framework are **not** covered. This is a workload acceptance suite, not a guarantee against all future faults or a substitute for manufacturer diagnostics.

## Supported candidate envelope

- One Linux/systemd workstation, Ubuntu-style tools, Python 3.10+, x86-64.
- 1–8 physical NVIDIA GPUs, CUDA capability 7.5 or newer, typically 16 GB VRAM or larger. Each selected card needs **at least 14,000 MiB free** for the current transformer training profile. Smaller cards are blocked, not silently tested with missing stages.
- Existing CUDA toolkit 12.9+ and driver compatible with PyTorch's CUDA 13.0 runtime. Installer does not replace drivers or the toolkit.
- Kernel journal access, available temperature/safety-limit telemetry, at least 5 GiB free result storage at launch and sufficient host RAM for concurrent model loading. Setup itself needs additional tens of GB for environments, builds and model downloads; budget 40 GiB free before installing.
- MIG-enabled inventory is rejected. Mixed models adapt precision/batch per card, but every exact configuration must undergo hardware qualification.
- Existing compute processes on selected cards cause a refusal. Schedule a maintenance window: GPU QA locks coordinate other GPU QA runs, not third-party schedulers.

## Installation for a validation bench

Review this candidate first. Install build prerequisites using the system's package manager: Python/venv, Git, CMake 3.20+, make, g++, Boost program-options development files, sudo, systemd and the existing CUDA toolkit (with `compute-sanitizer`). On Ubuntu the non-CUDA prerequisites can be installed with:

```bash
sudo apt-get install python3-venv git cmake make g++ libboost-program-options-dev sudo
sudo bash gpu-qa/install.sh bizon
```

The argument is the team's local operator username. CUDA's `bin` directory must be on root's installation PATH. Installation downloads pinned upstream source/model revisions and pinned direct Python dependencies. The full resolved Python package list, compiler version and executable/model hashes are retained in `software-manifest.json`. Transitive Python packages are recorded, not currently distributed as a fully locked offline wheel bundle; a different resolved environment needs its own qualification.

Files are installed under `/opt/bizon-gpu-qa`, configuration under `/etc/bizon-gpu-qa`, and private results under `/var/lib/bizon-gpu-qa/runs/RUN_ID`. Code/configuration remain root-owned. The limited start helper is the only new passwordless sudo command. Workloads run as `bizon-gpu-qa`, with GPU-device and kernel-journal access.

The installer appends one help-extension line to the operator's `.bashrc` and backs it up as `.bashrc.before-gpu-qa`; it does not replace the shell configuration. Log out/in to refresh group membership. Other operators can be added to group `bizon-gpu-qa` by an administrator and source `/opt/bizon-gpu-qa/bizonhelp-gpu-qa.bash` at the end of their `.bashrc`.

The installer refuses an existing installation, including partial installs. For a failed installation, inspect its error before retrying; an administrator should preserve results, confirm no QA services are running, and move aside `/opt/bizon-gpu-qa` and `/etc/bizon-gpu-qa` before a clean rebuild. Do not replace code during active runs. Existing results stay in `/var/lib/bizon-gpu-qa`.

## Results and release gate

| Result | Meaning |
| --- | --- |
| `SCREEN_PASS` | Quick screening completed; no production approval |
| `PILOT_PASS` | Candidate/tool validation or pilot profile completed; no production approval |
| `PASS` | Required production/RMA profile completed on an explicitly qualified configuration |
| `FAIL` | Workload, GPU or platform fault observed; root cause still needs investigation |
| `INCOMPLETE` | Interrupted, lost monitoring/coverage, rebooted, or stale run; never accept |
| `BLOCKED` | Setup, dependency, contention, capability or qualification requirement not met |

`production` and `rma` without `--pilot` are blocked until engineering has qualified the exact build/runtime/GPU-count/topology configuration. The shipped configuration has **no approved configurations**. Approving a configuration requires the separate checklist in [VALIDATION.md](VALIDATION.md); a pilot run never approves itself.

Xid events, newly changed ECC/repair counters, missing GPUs, PCIe replay changes, thermal slowdown/safety limits and hardware power braking stop the run. These are acceptance failures or investigation triggers, not automatic proof that the GPU is physically defective. Historical counters are preserved as baseline; pending repair/failure conditions block acceptance. Missing sensors/counters are reported as limitations. No universal bandwidth threshold is imposed without a matched healthy baseline.

HTML reports include per-card identity, active duration, coverage, telemetry summaries and faults. `result.json` provides machine-readable results; `status --json` reconciles reboot/stale-heartbeat interruptions. Use that command instead of reading a stale live report after a host crash. `export-rma` writes a short PDF and a ZIP of curated/redacted evidence with checksums. Serial numbers are retained for manufacturer identification. Review the export before sharing; automatic redaction is not a guarantee that all sensitive content was removed. No messages or RMA submissions are sent automatically.

## Development checks

```bash
python3 -m pip install -r gpu-qa/requirements-test.txt
python3 -m unittest discover -s gpu-qa/tests -v
python3 -m compileall -q gpu-qa/gpuqa
bash -n gpu-qa/install.sh gpu-qa/bizonhelp-gpu-qa.bash bashrc
```

These checks include simulated 1/2/4/8-card orchestration and actual CPU subprocess failure/timeout cleanup. They do not replace running the CUDA workers on physical cards.

Upstream tools: [GPU Burn](https://github.com/wilicc/gpu-burn), [NVIDIA nvbandwidth](https://github.com/NVIDIA/nvbandwidth), [PyTorch distributed](https://docs.pytorch.org/docs/stable/distributed.html), [Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B). Their respective licenses apply to downloaded components.
