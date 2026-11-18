import hashlib
import html
import json
import math
import pathlib
import re
import zipfile
from .common import read_json


def table(headers, rows):
    esc = lambda s: html.escape(str(s))
    return (
        "<table><tr>"
        + "".join("<th>" + esc(h) + "</th>" for h in headers)
        + "</tr>"
        + "".join(
            "<tr>" + "".join("<td>" + esc(x) + "</td>" for x in r) + "</tr>"
            for r in rows
        )
        + "</table>"
    )


def workload_metrics(run_dir, state):
    metrics = {}
    wanted = {
        "decode_tokens_s",
        "prefill_s",
        "step_s",
        "loss",
        "grad_norm",
        "h2d_gbps",
        "d2h_gbps",
        "gbps",
        "relative_error",
        "peak_bytes",
        "bytes_checked",
    }
    for stage in state.get("stages", []):
        for name in stage.get("logs", []):
            path = run_dir / name
            if path.parent != run_dir or not path.is_file():
                continue
            identity = next((u for u in stage["uuids"] if u in name), "selected group")
            with path.open(errors="replace") as f:
                for line in f:
                    if not line.startswith("{"):
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    detail = ""
                    if r.get("event") == "inference":
                        detail = f"batch={r.get('batch')}, context={r.get('context')}"
                    if r.get("event") == "peer_copy":
                        detail = f"{r.get('src')} to {r.get('dst')}, peer capable={r.get('direct_peer_supported')}"
                    if r.get("event") == "numeric":
                        detail = str(r.get("precision"))
                    for key in wanted.intersection(r):
                        value = r[key]
                        if not isinstance(value, (float, int)) or not math.isfinite(
                            value
                        ):
                            continue
                        group = (identity, stage["stage"], detail, key)
                        a = metrics.setdefault(group, [0, 0.0, value, value])
                        a[0] += 1
                        a[1] += value
                        a[2] = min(a[2], value)
                        a[3] = max(a[3], value)
    return [
        [*k, v[0], f"{v[2]:.5g}", f"{v[1]/v[0]:.5g}", f"{v[3]:.5g}"]
        for k, v in sorted(metrics.items())
    ]


def build_report(run_dir, state=None):
    p = pathlib.Path(run_dir)
    s = state or read_json(p / "result.json")
    label = s["status"]
    qualified = s.get("production_qualified", False)
    detail = (
        "Validated configuration completed its required profile."
        if qualified
        else "This result is not a production release approval."
    )
    body = (
        "<h1>BIZON GPU QA</h1><h2>" + html.escape(label) + "</h2><p>" + detail + "</p>"
    )
    body += table(
        ["Run", "Profile", "Elapsed hours", "Production qualified"],
        [
            [
                s["run_id"],
                s["profile"],
                round(s.get("elapsed_seconds", 0) / 3600, 3),
                qualified,
            ]
        ],
    )
    if s.get("error"):
        body += '<p class="error">' + html.escape(s["error"]) + "</p>"
    body += "<h2>GPUs</h2>" + table(
        ["GPU", "Serial", "UUID", "Driver", "ECC", "Workload hours"],
        [
            [
                g["name"],
                g["serial"],
                g["uuid"],
                g["driver"],
                g["ecc"],
                round(s.get("per_gpu_active_seconds", {}).get(g["uuid"], 0) / 3600, 3),
            ]
            for g in s.get("devices", [])
        ],
    )
    body += "<h2>Coverage</h2>" + table(
        ["Scope", "Stage", "GPU count", "Status", "Active seconds", "Wall seconds"],
        [
            [
                x["scope"],
                x["stage"],
                len(x["uuids"]),
                x["status"],
                round(x.get("active_seconds", 0), 2),
                round(x.get("wall_seconds", 0), 2),
            ]
            for x in s.get("stages", [])
        ],
    )
    body += "<h2>Telemetry</h2>"
    stats = {}
    path = p / "telemetry.jsonl"
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            for g in r["gpus"]:
                d = stats.setdefault(g["uuid"], {})
                for k in (
                    "temperature_c",
                    "power_w",
                    "utilization_percent",
                    "memory_used_mib",
                ):
                    if g.get(k) is not None:
                        d.setdefault(k, []).append(g[k])
    body += table(
        ["UUID", "Metric", "Min", "Mean", "Max"],
        [
            [u, k, round(min(v), 2), round(sum(v) / len(v), 2), round(max(v), 2)]
            for u, d in stats.items()
            for k, v in d.items()
        ],
    )
    body += "<h2>Workload measurements</h2><p>Observed measurements, not universal performance thresholds. Batch/context and peer capabilities matter when comparing systems.</p>"
    body += table(
        ["GPU/group", "Stage", "Case", "Metric", "Samples", "Min", "Mean", "Max"],
        workload_metrics(p, s),
    )
    bandwidth = []
    for item in s.get("bandwidth_references", []):
        path = p / item["log"]
        if path.parent == p and path.is_file():
            for name, value in re.findall(
                r"^SUM (\S+)\s+([0-9.eE+-]+)", path.read_text(errors="replace"), re.M
            ):
                bandwidth.append([item["name"], name, value])
    body += (
        "<h2>NVIDIA bandwidth reference</h2><p>GB/s sums reported by the tool across its selected-device result matrix; see logs for individual cells.</p>"
        + table(["Phase", "Test", "Reported sum (GB/s)"], bandwidth)
    )
    body += "<h2>Reproducibility</h2>" + table(
        ["Item", "Value"],
        [
            ["Source SHA-256", s.get("source_sha256", "Unavailable")],
            ["Tool validation", s.get("tool_validation", "Unqualified")],
        ],
    )
    body += table(
        ["Runtime GPU", "PyTorch", "CUDA runtime", "Transformers", "Model revision"],
        [
            [
                i,
                r.get("torch"),
                r.get("cuda"),
                r.get("transformers"),
                r.get("model_revision"),
            ]
            for i, r in enumerate(s.get("runtime", []))
        ],
    )
    body += "<h2>Fault events</h2>" + table(
        ["Time", "Kind", "Evidence"],
        [[x.get("time"), x.get("kind"), x.get("message")] for x in s.get("events", [])],
    )
    body += (
        "<h2>Limits</h2><ul>"
        + "".join("<li>" + html.escape(x) + "</li>" for x in s.get("warnings", []))
        + "</ul>"
    )
    body += "<p>Raw logs, telemetry, result.json and environment identity are retained in this run directory. Screen and pilot passes do not qualify production. An interrupted run is never a pass. A workload failure does not alone isolate hardware from software.</p>"
    doc = (
        '<!doctype html><html><meta charset="utf-8"><title>BIZON GPU QA '
        + html.escape(s["run_id"])
        + "</title><style>body{font:15px/1.5 system-ui;max-width:1250px;margin:35px auto;padding:0 20px;color:#17263f}table{border-collapse:collapse;width:100%;font-size:12px}td,th{padding:8px;border:1px solid #d3dce8;text-align:left;overflow-wrap:anywhere}th{background:#edf2f9}.error{color:#ac1d2c;font-weight:bold}h2{margin-top:28px}</style>"
        + body
        + "</html>"
    )
    (p / "report.html").write_text(doc)
    return p / "report.html"


def redact(text):
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9_.\-]+", r"\1[REDACTED]", text)
    text = re.sub(
        r"(?i)((?:api[_-]?key|token|password|secret)\s*[=:]\s*)[^\s,;]+",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(r"/(?:home|Users)/[^/\s]+", "/<user>", text)
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[IP REDACTED]", text)
    return text


def redact_value(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_value(x) for x in value]
    if isinstance(value, dict):
        return {
            k: (
                "[REDACTED]"
                if re.search(r"(?i)^(?:token|password|secret|api[_-]?key)$", k)
                else redact_value(v)
            )
            for k, v in value.items()
        }
    return value


def write_pdf(path, state):
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

    st = getSampleStyleSheet()
    story = [Paragraph("BIZON GPU QA - failure / validation summary", st["Title"])]

    def p(s):
        story.append(Paragraph(html.escape(str(s)), st["BodyText"]))
        story.append(Spacer(1, 6))

    p("Run: " + state["run_id"])
    p("Result: " + state["status"] + " | Profile: " + state["profile"])
    p("Production qualified: " + str(state.get("production_qualified", False)))
    p("Elapsed: %.2f hours" % (state.get("elapsed_seconds", 0) / 3600))
    for g in state.get("devices", []):
        p(f"{g['name']} | Serial {g['serial']} | {g['uuid']}")
    p(state.get("error") or "No disqualifying event recorded in the completed scope.")
    for e in state.get("events", [])[:6]:
        p(redact(e.get("message", "")))
    passed = sum(x["status"] == "PASS" for x in state.get("stages", []))
    p(f"Passed stage groups: {passed}/{len(state.get('stages',[]))}")
    p(
        "A screen or pilot pass is not a factory qualification. A stopped or incomplete test cannot establish production readiness. Fault evidence does not by itself isolate physical GPU damage from driver/application causes."
    )
    p(
        "Manufacturer submission: add proof of purchase/warranty, serial-label photos and customer case reference. Detailed stage results and sanitized evidence accompany this summary."
    )
    SimpleDocTemplate(
        str(path), rightMargin=45, leftMargin=45, topMargin=40, bottomMargin=40
    ).build(story)


def export_rma(run_dir, state=None):
    p = pathlib.Path(run_dir)
    s = state or read_json(p / "result.json")
    # Reconcile interrupted status first; exported summaries must match the CLI verdict.
    safe = redact_value(s)
    pdf = p / "rma-summary.pdf"
    write_pdf(pdf, safe)
    build_report(p, safe)
    archive = p / "rma-evidence.zip"
    manifest = {}
    payload = {
        "result.json": json.dumps(safe, indent=2).encode(),
        "rma-summary.pdf": pdf.read_bytes(),
        "report.html": (p / "report.html").read_bytes(),
    }
    allowed = {
        "telemetry.jsonl",
        "events.jsonl",
        "software-manifest.json",
        "topology.txt",
    }
    for stage in s.get("stages", []):
        allowed.update(stage.get("logs", []))
    for item in s.get("bandwidth_references", []):
        allowed.add(item["log"])
    for name in sorted(allowed):
        source = p / name
        if source.parent != p or not source.is_file():
            continue
        raw = source.read_bytes()
        sanitized = redact(raw.decode("utf-8", errors="replace")).encode()
        payload["evidence/" + name] = sanitized
        manifest[name] = {
            "original_sha256": hashlib.sha256(raw).hexdigest(),
            "export_sha256": hashlib.sha256(sanitized).hexdigest(),
            "sanitized": raw != sanitized,
        }
    payload["redaction-manifest.json"] = json.dumps(manifest, indent=2).encode()
    payload["README.txt"] = (
        b"Sanitized RMA export. Original full records remain on the workstation. Kernel event export includes relevant fault events, not the unrestricted kernel journal. Add invoice, serial photos and customer reference. No RMA has been submitted automatically.\n"
    )
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in payload.items():
            z.writestr(name, data)
        z.writestr(
            "SHA256SUMS.txt",
            "".join(
                hashlib.sha256(data).hexdigest() + "  " + name + "\n"
                for name, data in sorted(payload.items())
            ),
        )
    build_report(p, s)  # Restore the private report after exporting its sanitized copy.
    return archive, pdf
