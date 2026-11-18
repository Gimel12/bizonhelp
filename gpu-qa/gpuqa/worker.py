"""GPU workers. Each single-GPU process sees exactly one UUID; NCCL sees the selected set."""

import argparse
import datetime
import json
import os
import platform
import time


def emit(**data):
    print(json.dumps(dict(time=time.time(), **data), allow_nan=False), flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "stage",
        choices=[
            "probe",
            "numeric",
            "bandwidth",
            "inference",
            "training",
            "vision",
            "collective",
            "peer",
        ],
    )
    p.add_argument("--seconds", type=float, default=10)
    p.add_argument("--config", required=True)
    a = p.parse_args()
    cfg = json.load(open(a.config))
    import torch

    torch.set_num_threads(2)
    torch.manual_seed(1234)
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA runtime unavailable")
    if a.stage == "collective":
        import torch.multiprocessing as mp

        mp.spawn(
            collective_rank,
            args=(torch.cuda.device_count(), a.seconds, cfg["rendezvous"]),
            nprocs=torch.cuda.device_count(),
            join=True,
        )
        return
    if a.stage == "peer":
        world = torch.cuda.device_count()
        if world < 2:
            raise RuntimeError("Peer test needs at least two GPUs")
        start = time.monotonic()
        checks = 0
        pairs = []
        while checks < world * (world - 1) or time.monotonic() - start < a.seconds:
            for src in range(world):
                for dst in range(world):
                    if src == dst:
                        continue
                    with torch.cuda.device(src):
                        x = torch.arange(1024 * 1024, dtype=torch.int32, device="cuda")
                    with torch.cuda.device(dst):
                        expected = torch.arange(
                            1024 * 1024, dtype=torch.int32, device="cuda"
                        )
                    torch.cuda.synchronize(src)
                    torch.cuda.synchronize(dst)
                    t = time.perf_counter()
                    y = x.to("cuda:" + str(dst))
                    torch.cuda.synchronize(dst)
                    elapsed = time.perf_counter() - t
                    if not torch.equal(y, expected):
                        raise RuntimeError("GPU-to-GPU copy mismatch")
                    if checks < world * (world - 1):
                        pairs.append([src, dst])
                    emit(
                        event="peer_copy",
                        src=src,
                        dst=dst,
                        direct_peer_supported=torch.cuda.can_device_access_peer(
                            src, dst
                        ),
                        gbps=x.numel() * x.element_size() / elapsed / 1e9,
                    )
                    checks += 1
                    del x, y, expected
        emit(
            event="complete",
            stage="peer",
            active_seconds=time.monotonic() - start,
            checks=checks,
            pairs=pairs,
            world_size=world,
        )
        return
    if torch.cuda.device_count() != 1:
        raise RuntimeError("Single-device worker requires exactly one visible GPU")
    torch.cuda.set_device(0)
    torch.backends.cuda.matmul.allow_tf32 = False
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    if a.stage == "probe":
        import transformers

        prop = torch.cuda.get_device_properties(0)
        x = torch.ones(8, device="cuda")
        assert x.sum().item() == 8
        emit(
            event="probe",
            name=prop.name,
            memory_bytes=prop.total_memory,
            capability=list(torch.cuda.get_device_capability()),
            python=platform.python_version(),
            cudnn=torch.backends.cudnn.version(),
            torch=torch.__version__,
            cuda=torch.version.cuda,
            transformers=transformers.__version__,
            precision=str(dtype),
            model_revision=cfg["model_revision"],
        )
        return
    if a.stage == "numeric":
        start = time.monotonic()
        checks = 0
        for d in (torch.float64, torch.float32, torch.float16, torch.bfloat16):
            if d == torch.bfloat16 and not torch.cuda.is_bf16_supported():
                continue
            x = torch.randn(256, 256, dtype=torch.float64)
            y = torch.randn(256, 256, dtype=torch.float64)
            reference = x.to(d).double() @ y.to(d).double()
            actual = (
                (x.to(device="cuda", dtype=d) @ y.to(device="cuda", dtype=d))
                .double()
                .cpu()
            )
            error = float((actual - reference).norm() / reference.norm())
            limit = {
                torch.float64: 1e-12,
                torch.float32: 1e-5,
                torch.float16: 0.002,
                torch.bfloat16: 0.01,
            }[d]
            if not torch.isfinite(actual).all() or error > limit:
                raise RuntimeError("Numerical reference mismatch")
            emit(
                event="numeric", precision=str(d), relative_error=error, tolerance=limit
            )
            checks += 1
        emit(
            event="complete",
            stage=a.stage,
            active_seconds=time.monotonic() - start,
            checks=checks,
        )
        return
    if a.stage == "bandwidth":
        # Round-trip integrity supplements nvbandwidth, using small pinned-host buffers.
        start = time.monotonic()
        count = 0
        n = 16 * 1024 * 1024
        host = torch.arange(n, dtype=torch.int32, pin_memory=True)
        target = torch.empty_like(host, device="cuda")
        back = torch.empty_like(host, pin_memory=True)
        while count < 3 or time.monotonic() - start < a.seconds:
            torch.cuda.synchronize()
            t = time.perf_counter()
            target.copy_(host, non_blocking=True)
            torch.cuda.synchronize()
            h2d = time.perf_counter() - t
            t = time.perf_counter()
            back.copy_(target, non_blocking=True)
            torch.cuda.synchronize()
            d2h = time.perf_counter() - t
            if not torch.equal(host, back):
                raise RuntimeError("PCIe round-trip data mismatch")
            emit(
                event="roundtrip",
                h2d_gbps=n * 4 / h2d / 1e9,
                d2h_gbps=n * 4 / d2h / 1e9,
            )
            count += 1
        emit(
            event="complete",
            stage=a.stage,
            active_seconds=time.monotonic() - start,
            checks=count,
        )
        return
    if a.stage == "vision":
        # Actual cuDNN convolution forward/backward/update, synthetic reproducible images.
        model = torch.nn.Sequential(
            torch.nn.Conv2d(3, 64, 3, padding=1),
            torch.nn.GELU(),
            torch.nn.Conv2d(64, 64, 3, padding=1),
            torch.nn.AdaptiveAvgPool2d(1),
            torch.nn.Flatten(),
            torch.nn.Linear(64, 10),
        ).cuda()
        opt = torch.optim.AdamW(model.parameters(), lr=0.001)
        x = torch.randn(8, 3, 128, 128, device="cuda")
        labels = torch.arange(8, device="cuda") % 10
        start = time.monotonic()
        steps = 0
        while steps < 3 or time.monotonic() - start < a.seconds:
            opt.zero_grad(set_to_none=True)
            loss = torch.nn.functional.cross_entropy(model(x), labels)
            if not torch.isfinite(loss):
                raise RuntimeError("Nonfinite convolution loss")
            loss.backward()
            grad = torch.nn.utils.clip_grad_norm_(
                model.parameters(), 1, error_if_nonfinite=True
            )
            opt.step()
            torch.cuda.synchronize()
            steps += 1
            if steps % 10 == 0:
                emit(
                    event="vision_step",
                    step=steps,
                    loss=loss.item(),
                    grad_norm=float(grad),
                )
        emit(
            event="complete",
            stage=a.stage,
            active_seconds=time.monotonic() - start,
            checks=steps,
            cudnn=torch.backends.cudnn.version(),
        )
        return
    from transformers import AutoTokenizer, AutoModelForCausalLM

    tokenizer = AutoTokenizer.from_pretrained(
        cfg["model_path"], local_files_only=True, trust_remote_code=False
    )
    model = AutoModelForCausalLM.from_pretrained(
        cfg["model_path"],
        local_files_only=True,
        trust_remote_code=False,
        dtype=dtype,
        attn_implementation="sdpa",
    ).cuda()
    emit(
        event="model",
        revision=cfg["model_revision"],
        precision=str(dtype),
        parameters=sum(x.numel() for x in model.parameters()),
    )
    if a.stage == "inference":
        model.eval()
        references = {}
        count = 0
        total = torch.cuda.get_device_properties(0).total_memory
        max_batch = min(16, max(1, int(total / (8 * 1024**3))))
        cases = [(1, 128), (min(4, max_batch), 1024), (max_batch, 2048)]
        with torch.inference_mode():
            prompt = tokenizer.apply_chat_template(
                [{"role": "user", "content": "What is 2 + 2? Answer briefly."}],
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
            inputs = tokenizer(prompt, return_tensors="pt").to("cuda")
            o = model.generate(**inputs, max_new_tokens=24, do_sample=False)
            emit(
                event="sample",
                response=tokenizer.decode(
                    o[0, inputs.input_ids.shape[1] :], skip_special_tokens=True
                ),
            )
            del o, inputs
            start = time.monotonic()
            while count < 6 or time.monotonic() - start < a.seconds:
                batch, seq = cases[count % len(cases)]
                ids = torch.full((batch, seq), 42, dtype=torch.long, device="cuda")
                torch.cuda.reset_peak_memory_stats()
                torch.cuda.synchronize()
                t = time.perf_counter()
                out = model(ids, use_cache=True, logits_to_keep=1)
                torch.cuda.synchronize()
                prefill = time.perf_counter() - t
                if not torch.isfinite(out.logits).all():
                    raise RuntimeError("Nonfinite inference logits")
                reference = out.logits[:, -1, :].float().clone()
                key = (batch, seq)
                if key in references and not torch.allclose(
                    reference, references[key], rtol=0.02, atol=0.02
                ):
                    raise RuntimeError(
                        "Repeated inference logits exceed precision tolerance"
                    )
                references[key] = reference
                cache = out.past_key_values
                token = out.logits[:, -1, :].argmax(-1, keepdim=True)
                del out
                t = time.perf_counter()
                for _ in range(63):
                    out = model(
                        token, past_key_values=cache, use_cache=True, logits_to_keep=1
                    )
                    cache = out.past_key_values
                    if not torch.isfinite(out.logits).all():
                        raise RuntimeError("Nonfinite decode logits")
                    token = out.logits[:, -1, :].argmax(-1, keepdim=True)
                torch.cuda.synchronize()
                elapsed = time.perf_counter() - t
                emit(
                    event="inference",
                    batch=batch,
                    context=seq,
                    decode_tokens_s=batch * 63 / elapsed,
                    prefill_s=prefill,
                    peak_bytes=torch.cuda.max_memory_allocated(),
                )
                del ids, out, cache, token
                torch.cuda.empty_cache()
                count += 1
            emit(
                event="complete",
                stage=a.stage,
                active_seconds=time.monotonic() - start,
                checks=count,
            )
    else:
        model.train()
        model.config.use_cache = False
        # Keep optimizer in FP32; autocast enables tensor cores and stable updates.
        model = model.float()
        opt = torch.optim.AdamW(model.parameters(), lr=1e-5, foreach=False)
        seq = 256
        batch = 1
        free, _ = torch.cuda.mem_get_info()
        estimated_state = sum(x.numel() for x in model.parameters()) * 12
        if free < estimated_state + 512 * 1024**2:
            raise RuntimeError("Insufficient VRAM for FP32 optimizer states")
        batch = max(1, min(16, int((free - estimated_state) / (2 * 1024**3))))
        ids = (
            tokenizer(
                ("GPU computation needs reliable memory and finite gradients. " * 100),
                return_tensors="pt",
                truncation=True,
                max_length=seq,
            )
            .input_ids.cuda()
            .repeat(batch, 1)
        )
        scaler = torch.amp.GradScaler(
            "cuda", enabled=dtype == torch.float16, init_scale=128
        )
        start = time.monotonic()
        steps = 0
        initial = None
        while steps < 3 or time.monotonic() - start < a.seconds:
            t = time.perf_counter()
            opt.zero_grad(set_to_none=True)
            with torch.autocast("cuda", dtype=dtype):
                out = model(ids, labels=ids, use_cache=False)
                loss = out.loss
            if not torch.isfinite(loss):
                raise RuntimeError("Nonfinite training loss")
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            grad = torch.nn.utils.clip_grad_norm_(
                model.parameters(), 1, error_if_nonfinite=True
            )
            # Track a small trainable parameter to verify that optimizer updates occur.
            param = next(
                p
                for p in model.parameters()
                if p.requires_grad and p.numel() < 65536 and p.grad is not None
            )
            before = param.detach().clone()
            scaler.step(opt)
            scaler.update()
            torch.cuda.synchronize()
            if steps == 0:
                initial = loss.item()
            changed = not torch.equal(before, param)
            if steps == 0 and not changed:
                raise RuntimeError("Optimizer did not update the monitored parameter")
            emit(
                event="training",
                step=steps,
                loss=loss.item(),
                grad_norm=float(grad),
                step_s=time.perf_counter() - t,
                batch=batch,
                context=ids.shape[1],
                updated=changed,
                peak_bytes=torch.cuda.max_memory_allocated(),
            )
            steps += 1
            last = loss.item()
            del out, loss, before
        emit(
            event="complete",
            stage=a.stage,
            active_seconds=time.monotonic() - start,
            checks=steps,
            first_loss=initial,
            last_loss=last,
        )


def collective_rank(rank, world, seconds, rendezvous):
    import torch
    import torch.distributed as dist
    from torch.nn.parallel import DistributedDataParallel

    torch.set_num_threads(2)
    torch.cuda.set_device(rank)
    torch.manual_seed(4321)
    dist.init_process_group(
        "nccl",
        init_method="file://" + rendezvous,
        rank=rank,
        world_size=world,
        timeout=datetime.timedelta(seconds=90),
    )
    try:
        start = time.monotonic()
        checks = 0
        for n in (1, 1024, 1024 * 1024):
            x = torch.full((n,), float(rank + 1), device="cuda")
            dist.all_reduce(x)
            if not torch.all(x == world * (world + 1) / 2):
                raise RuntimeError("NCCL all-reduce mismatch")
            y = torch.full((n,), float(rank), device="cuda")
            gather = [torch.empty_like(y) for _ in range(world)]
            dist.all_gather(gather, y)
            if any(not torch.all(t == i) for i, t in enumerate(gather)):
                raise RuntimeError("NCCL all-gather mismatch")
            checks += 2
        model = DistributedDataParallel(
            torch.nn.Sequential(
                torch.nn.Linear(1024, 2048), torch.nn.GELU(), torch.nn.Linear(2048, 128)
            ).cuda(),
            device_ids=[rank],
        )
        opt = torch.optim.SGD(model.parameters(), lr=0.01)
        while True:
            # Every rank must execute the same number of DDP steps, even across slow/mixed cards.
            more = torch.tensor(
                (
                    int(checks < 9 or time.monotonic() - start < seconds)
                    if rank == 0
                    else 0
                ),
                device="cuda",
            )
            dist.broadcast(more, 0)
            if not more.item():
                break
            x = torch.randn(64, 1024, device="cuda")
            target = torch.randn(64, 128, device="cuda")
            opt.zero_grad(set_to_none=True)
            loss = (model(x) - target).square().mean()
            if not torch.isfinite(loss):
                raise RuntimeError("Nonfinite distributed loss")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(
                model.parameters(), 1, error_if_nonfinite=True
            )
            opt.step()
            checks += 1
        # DDP must leave every rank's trained parameters identical within FP32 tolerance.
        for param in model.parameters():
            reference = param.detach().clone()
            dist.broadcast(reference, 0)
            if not torch.allclose(param, reference, rtol=1e-6, atol=1e-7):
                raise RuntimeError("Distributed parameter mismatch")
        torch.cuda.synchronize()
        dist.barrier()
        emit(
            event="complete",
            stage="collective",
            rank=rank,
            world_size=world,
            active_seconds=time.monotonic() - start,
            checks=checks,
        )
    finally:
        dist.destroy_process_group()


if __name__ == "__main__":
    main()
