import csv
import io
import re
import xml.etree.ElementTree as ET
from .common import QAError, UUID_RE, command


def number(value):
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)", value or "")
    return float(m[1]) if m else None


def parse_snapshot(xml):
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise QAError("Invalid GPU telemetry", "INCOMPLETE", "monitoring") from e
    out = []
    for index, g in enumerate(root.findall("gpu")):
        paths = {
            "temperature_c": "temperature/gpu_temp",
            "thermal_margin_c": "temperature/gpu_temp_tlimit",
            "max_operating_c": "temperature/gpu_temp_max_threshold",
            "memory_temperature_c": "temperature/memory_temp",
            "power_w": "gpu_power_readings/average_power_draw",
            "fan_percent": "fan_speed",
            "utilization_percent": "utilization/gpu_util",
            "memory_used_mib": "fb_memory_usage/used",
            "memory_total_mib": "fb_memory_usage/total",
            "memory_free_mib": "fb_memory_usage/free",
            "sm_mhz": "clocks/sm_clock",
            "pcie_generation": "pci/pci_gpu_link_info/pcie_gen/current_link_gen",
            "pcie_width": "pci/pci_gpu_link_info/link_widths/current_link_width",
            "pcie_replays": "pci/replay_counter",
            "sw_thermal_us": "clocks_event_reasons_counters/clocks_event_reasons_counters_sw_therm_slowdown",
            "hw_thermal_us": "clocks_event_reasons_counters/clocks_event_reasons_counters_hw_therm_slowdown",
            "power_brake_us": "clocks_event_reasons_counters/clocks_event_reasons_counters_hw_power_brake",
        }
        row = {k: number(g.findtext(v)) for k, v in paths.items()}
        row.update(
            index=index,
            uuid=g.findtext("uuid"),
            name=g.findtext("product_name"),
            serial=g.findtext("serial"),
            pci=g.get("id", "").lower(),
            driver=root.findtext("driver_version"),
            ecc=g.findtext("ecc_mode/current_ecc"),
            display=g.findtext("display_active"),
            power_limit_w=number(
                g.findtext(
                    "gpu_power_readings/gpu_ceiling_power_limit/current_power_limit"
                )
                or g.findtext("gpu_power_readings/power_limit")
            ),
        )
        if row["power_w"] is None:
            row["power_w"] = number(g.findtext("gpu_power_readings/power_draw"))
        row["counters"] = {}
        for parent in (
            "ecc_errors/volatile",
            "ecc_errors/aggregate",
            "retired_pages",
            "remapped_rows",
        ):
            element = g.find(parent)
            if element is not None:

                def visit(el, path):
                    if len(el):
                        for child in el:
                            visit(child, path + "/" + child.tag)
                    else:
                        value = number(el.text)
                        if value is not None and not any(
                            x in path for x in ("availability", "histogram")
                        ):
                            row["counters"][path] = value
                        elif (el.text or "").strip().lower() in (
                            "yes",
                            "true",
                            "no",
                            "false",
                        ):
                            row["counters"][path] = int(
                                (el.text or "").strip().lower() in ("yes", "true")
                            )

                visit(element, parent)
        row["thermal_flags"] = [
            e.tag
            for p in ("clocks_event_reasons", "clocks_throttle_reasons")
            for e in g.findall(p + "/*")
            if "thermal" in e.tag and (e.text or "").strip() == "Active"
        ]
        if not row["uuid"] or not UUID_RE.fullmatch(row["uuid"]):
            raise QAError("Missing or invalid physical GPU identity")
        if g.findtext("mig_mode/current_mig") == "Enabled":
            raise QAError(
                "MIG mode is not supported by this physical-GPU acceptance suite"
            )
        out.append(row)
    if not out:
        raise QAError("No NVIDIA GPUs discovered")
    return out


def snapshot():
    raw = command(["nvidia-smi", "-q", "-x"], 15)
    return parse_snapshot(raw), raw


def select_gpus(inventory, requested):
    tokens = [str(x).strip() for x in requested.split(",")]
    if requested == "all":
        chosen = list(inventory)
    else:
        chosen = []
        for token in tokens:
            matches = [
                g for g in inventory if token == g["uuid"] or token == str(g["index"])
            ]
            if len(matches) != 1:
                raise QAError(f"Unknown GPU selector: {token}")
            if matches[0] in chosen:
                raise QAError("Duplicate GPU selection")
            chosen.append(matches[0])
    if not 1 <= len(chosen) <= 8:
        raise QAError("Select between 1 and 8 physical GPUs")
    return sorted(chosen, key=lambda g: g["index"])


def busy_processes(uuids):
    raw = command(
        [
            "nvidia-smi",
            "--query-compute-apps=gpu_uuid,pid,process_name",
            "--format=csv,noheader,nounits",
        ]
    )
    rows = []
    for row in csv.reader(io.StringIO(raw)):
        if len(row) >= 3 and row[0].strip() in uuids:
            rows.append(
                {"uuid": row[0].strip(), "pid": row[1].strip(), "name": row[2].strip()}
            )
    return rows


def health_faults(baseline, current, fallback_limit=None):
    expected = {g["uuid"]: g for g in baseline}
    now = {g["uuid"]: g for g in current}
    faults = []
    for uuid, b in expected.items():
        if uuid not in now:
            faults.append(("FAIL", "device_lost", uuid + ": GPU disappeared"))
            continue
        g = now[uuid]
        if any(
            g.get(k) != b.get(k)
            for k in ("name", "pci", "driver", "ecc", "power_limit_w")
        ):
            faults.append(
                (
                    "INCOMPLETE",
                    "configuration_changed",
                    uuid + ": tested configuration changed",
                )
            )
        temp, margin = g["temperature_c"], g["thermal_margin_c"]
        limit = g.get("max_operating_c") or fallback_limit
        if temp is None or (margin is None and limit is None):
            faults.append(
                (
                    "INCOMPLETE",
                    "monitoring",
                    uuid + ": thermal safety limit unavailable",
                )
            )
        elif (margin is not None and margin <= 4) or (
            limit is not None and temp >= limit - 4
        ):
            faults.append(("FAIL", "thermal", uuid + ": thermal safety margin reached"))
        if g["thermal_flags"]:
            faults.append(("FAIL", "thermal", uuid + ": thermal slowdown active"))
        for key in ("pcie_replays", "sw_thermal_us", "hw_thermal_us", "power_brake_us"):
            old, new = b.get(key), g.get(key)
            if old is not None and new is None:
                faults.append(
                    ("INCOMPLETE", "monitoring", f"{uuid}: lost counter {key}")
                )
            elif old is not None and new is not None and new != old:
                verdict = "FAIL" if new > old else "INCOMPLETE"
                faults.append(
                    (verdict, "counter_change", f"{uuid}: {key} changed {old} -> {new}")
                )
        for key, old in b.get("counters", {}).items():
            new = g.get("counters", {}).get(key)
            if new is None:
                faults.append(
                    ("INCOMPLETE", "monitoring", f"{uuid}: lost health counter {key}")
                )
            elif new != old:
                faults.append(
                    (
                        "FAIL" if new > old else "INCOMPLETE",
                        "memory_health",
                        f"{uuid}: {key} changed {old} -> {new}",
                    )
                )
    return faults


def kernel_faults(message, selected):
    # Only attribute GPU faults to selected GPUs when a PCI identity is supplied.
    m = re.search(r"NVRM: Xid \(PCI:([0-9a-fA-F:]+)\):\s*(\d+)", message)
    if m:
        pci = m[1].lower().split(":")[-2:]
        for gpu in selected:
            if gpu["pci"].split(":")[-2:] == pci or gpu["pci"].split(".")[0].endswith(
                ":".join(pci)
            ):
                return {
                    "kind": "xid",
                    "uuid": gpu["uuid"],
                    "xid": int(m[2]),
                    "message": message,
                }
        return None
    if re.search(
        r"GPU has fallen off|Hardware Error|AER:.*(?:fatal|error)|Out of memory: Killed process",
        message,
        re.I,
    ):
        return {"kind": "platform", "message": message}
    return None
