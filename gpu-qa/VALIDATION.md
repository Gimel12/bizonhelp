# Validation record and hardware release checklist

Status: **CANDIDATE — HARDWARE QUALIFICATION PENDING**

## Completed software checks

- CPU-only automated tests cover 1–8 selection/planning, simulated full 1/2/4/8-card runs, isolated UUID subprocess environments, per-GPU locking, intentional worker exits, deadlines and process cleanup.
- Missing stage/rank/peer-pair completion, invalid completion values, stale heartbeat, reboot, failed startup and bad setup cannot become a pass.
- Tests cover GPU-loss/thermal/counter/Xid classification, exact-configuration qualification keys, changed binary/model hashes, escaped HTML, PDF/ZIP export, evidence hashes and basic redaction.
- Existing `bizonhelp` aliases and functions survive installation of the help extension; repeated sourcing is idempotent.
- Python compilation, shell syntax and CLI help checked.
- The new VRAM checker compiled with CUDA 12.9, PTX target compute_75, and host compiler `-Wall -Wextra -Werror`. Compilation does not execute CUDA kernels.

## Not yet validated

- Installer from a clean Linux workstation through systemd execution, operator permissions, journal access, log-out/disconnect behavior, stop/timeout cleanup and reboot recovery.
- Actual memory-checker corruption detection and compute-sanitizer execution.
- Actual model inference/training, numerical tolerances, optimizer behavior, memory sizing and performance on each supported architecture.
- Actual 1-, 2-, 4- and 8-GPU concurrent, peer-copy, NCCL and DDP runs, mixed-card topologies and thermal/power behavior.
- Known-good vs independently diagnosed faulty hardware discrimination; comparison against vendor diagnostics and healthy bandwidth baselines.

No healthy multi-GPU bench is currently available. None of the items above should be recorded as passed based on mocks, compilation, a returned customer's GPU, or a pilot's clean process exit.

## Bench acceptance procedure

1. Use an independently known-good workstation. Record board/BIOS, CPU/RAM, PSU, cooling, OS/kernel, driver/toolkit and the exact GPU models/count/slots/topology. Keep clocks/power/ECC at the intended shipping configuration. Ensure no customer workloads or scheduler jobs can start during QA.
2. Install on a clean bench and retain installer output. Verify the manifest and `gpu-qa doctor`; log in as the ordinary operator and check `bizonhelp`.
3. Run `gpu-qa validate --gpus all`. This includes a deliberate single-word VRAM corruption (expected checker exit 3 and exactly one error), a clean compute-sanitizer run, normal workloads and every required multi-GPU rank/pair. A successful result is only `PILOT_PASS`. Inspect all logs, not only the final label.
4. Exercise service behavior: start a quick pilot, disconnect SSH, reconnect and confirm heartbeat/stage progress; stop it and confirm only owned workloads are gone and result is `INCOMPLETE`. On this disposable bench, perform a reboot-interruption test and confirm reconciled status is `INCOMPLETE`. Ensure a second overlapping run is blocked, while disjoint-card selections can run independently.
5. Run `gpu-qa quick --gpus all`, then `gpu-qa production --gpus all --pilot`, followed by `gpu-qa rma --gpus all --pilot` for initial release qualification. Require all cards' workloads, both NVIDIA bandwidth passes and all ranks/pairs. Review temperatures, utilization, bandwidth, training updates, repeated outputs and kernel evidence; investigate outliers against the same known-good configuration.
6. Repeat on physical 1/2/4/8-card systems, including the actual GPU families and mixed configurations the factory will support. Do not extrapolate 2-card evidence to 8 cards. Other counts require their own configuration approval. Record unsupported paths explicitly.
7. Compare with an independently reproduced faulty case and applicable manufacturer diagnostics. A test failure may reflect the software, cooling, PSU, platform, interconnect or driver; only assign GPU hardware fault when evidence isolates it. Calibrate the test if healthy hardware fails it.
8. Engineering reviews the evidence, exact source/dependency fingerprints, scope/limitations and all outstanding issues. Retain the signed decision with the run artifacts. Only then may an administrator add the reviewed result's exact `qualification_key` string to `qualified_configurations` in `/etc/bizon-gpu-qa/config.json`. Do not edit `result.json` or the key to manufacture an approval. There is deliberately no automatic promotion command.
9. Run a non-pilot production profile after approval and verify `PASS`, `production_qualified: true`, complete per-card hours, all stage groups and the report/export. Changes to source, binaries/model, dependencies, GPU count/model, driver, power/ECC/display or topology require renewed review. Rebuilds must not inherit an approval blindly.

A release approval is limited to the tested envelope and duration. Large customer models, quantized kernels, multi-node collectives or special production frameworks need additional representative workload profiles before making claims about those workloads.
