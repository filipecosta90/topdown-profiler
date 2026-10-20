"""Native Apple Instruments capture and XML export, separate from slot TMA."""

import hashlib
import json
import platform
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from topdown import __version__

TMA_ROOTS = ("Frontend_Bound", "Bad_Speculation", "Backend_Bound", "Retiring")
MODES = {
    "counters": ("CPU Counters", "cpu-pmc-value", "native-pmu-values"),
    "cpu": ("CPU Profiler", "cpu-profile", "cycle-sampled-stacks"),
    "time": ("Time Profiler", "time-profile", "timer-sampled-stacks"),
}
APPLE_COLLECTION_MESSAGE = (
    "Apple silicon uses native Instruments profiles, not Linux perf/toplev. "
    "Use 'topdown apple doctor', then 'topdown apple record --output <new-directory> "
    "-- /path/to/program [args]'. Four-category slot TMA is not yet supported."
)


class AppleProfileError(RuntimeError):
    """A capture or export cannot supply the requested native measurement."""


def _write_json(path: Path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def _sha256(path: Path) -> str:
    with path.open("rb") as f:
        digest = hashlib.sha256()
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _xml(path: Path):
    try:
        return ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise AppleProfileError(f"Cannot read Instruments XML {path}: {exc}") from exc


def _validate_scope(pid, start_ns, end_ns):
    if (pid is not None and pid <= 0) or start_ns < 0 or (end_ns is not None and end_ns <= start_ns):
        raise AppleProfileError("Require positive PID and a nonempty, nonnegative time window.")


def _run(cmd: list[str], output: Path, name: str, timeout: int):
    _write_json(output / f"{name}.command.json", cmd)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        for suffix, value in (("stdout", exc.stdout), ("stderr", exc.stderr)):
            if isinstance(value, bytes):
                value = value.decode(errors="replace")
            (output / f"{name}.{suffix}").write_text(value or "")
        raise AppleProfileError(f"{name} timed out after {timeout}s; retained partial artifacts in {output}") from exc
    except OSError as exc:
        raise AppleProfileError(f"Cannot execute {cmd[0]}: {exc}") from exc
    (output / f"{name}.stdout").write_text(result.stdout)
    (output / f"{name}.stderr").write_text(result.stderr)
    text = result.stdout + result.stderr
    if result.returncode or "[Error]" in text:
        detail = result.stderr.strip() or result.stdout.strip()
        raise AppleProfileError(f"{name} failed (exit {result.returncode}): {detail}. Artifacts: {output}")
    return result


def doctor() -> dict:
    """Read tool metadata without launching a target or changing permissions."""
    result = {
        "system": platform.system(),
        "arch": platform.machine(),
        "os_version": platform.mac_ver()[0],
        "xctrace": None,
        "templates": [],
        "hardware_access": "unverified",
        "tma": dict.fromkeys(TMA_ROOTS),
    }
    if result["system"] != "Darwin" or result["arch"] != "arm64":
        result["error"] = "Native Apple collection requires macOS on Apple silicon (arm64)."
        return result
    commands = {
        "cpu": ["sysctl", "-n", "machdep.cpu.brand_string"],
        "xctrace": ["xcrun", "xctrace", "version"],
        "templates": ["xcrun", "xctrace", "list", "templates"],
        "developer_tools_security": ["/usr/sbin/DevToolsSecurity", "-status"],
    }
    for key, cmd in commands.items():
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
            if proc.returncode:
                result[key] = None
                result.setdefault("errors", {})[key] = proc.stderr.strip() or proc.stdout.strip()
            elif key == "templates":
                result[key] = [name for name, _, _ in MODES.values() if name in proc.stdout.splitlines()]
            else:
                result[key] = (proc.stdout or proc.stderr).strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            result.setdefault("errors", {})[key] = str(exc)
    result["note"] = "Installed templates do not establish PMU access. No permission settings were changed."
    return result


def _resolve_ids(root):
    ids = {}
    for node in root.iter():
        if "id" in node.attrib:
            key = node.attrib["id"]
            if key in ids:
                raise AppleProfileError(f"Duplicate XML id {key}")
            ids[key] = node

    def resolve(node):
        seen = set()
        while node is not None and "ref" in node.attrib:
            key = node.attrib["ref"]
            if key in seen or key not in ids:
                raise AppleProfileError(f"Cyclic or missing XML reference {key}")
            seen.add(key)
            node = ids[key]
        return node

    return resolve


def parse_profile(path: Path, schema: str, pid: int, start_ns: int = 0, end_ns: int | None = None) -> dict:
    """Parse one exported table; require explicit PID and optional half-open window.

    Native PMU values remain integers with their event names. They are not assumed
    to be cumulative counters, deltas, or additive samples, and are never summed.
    """
    _validate_scope(pid, start_ns, end_ns)
    if pid is None:
        raise AppleProfileError("Require an explicit PID for XML parsing")
    if schema not in {entry[1] for entry in MODES.values()}:
        raise AppleProfileError(f"Unsupported measurement schema {schema}")
    root = _xml(path)
    resolve = _resolve_ids(root)
    samples, summaries = [], Counter()
    excluded = 0
    tables = [node for node in root.iter("node") if node.find("schema") is not None]
    if len(tables) != 1 or tables[0].find("schema").get("name") != schema:
        raise AppleProfileError(f"Expected exactly one {schema} table in {path}")
    table = tables[0]
    metadata = table.find("schema")
    columns = [col.findtext("mnemonic") for col in metadata.findall("col")]
    types = {col.findtext("mnemonic"): col.findtext("engineering-type") for col in metadata.findall("col")}
    required = {"time", "thread", "process", "core"}
    required |= {"name", "value", "sample-index"} if schema == "cpu-pmc-value" else {"weight", "stack"}
    if not required.issubset(columns) or len(set(columns)) != len(columns):
        raise AppleProfileError(f"Missing or duplicate {schema} columns: {columns}")
    if types["time"] != "sample-time":
        raise AppleProfileError("Unknown time unit; expected Instruments sample-time (nanoseconds).")
    if schema == "cpu-profile" and types["weight"] != "cycle-weight":
        raise AppleProfileError("Unknown CPU Profiler weight; expected cycle-weight.")
    if schema == "time-profile" and types["weight"] != "weight":
        raise AppleProfileError("Unknown Time Profiler weight; expected native weight.")
    if schema == "cpu-pmc-value" and (types["value"] != "uint64" or types["name"] != "pmc-event-name"):
        raise AppleProfileError("Unknown PMC value or event-name type.")

    def integer(node, field):
        node = resolve(node)
        try:
            value = int(node.text)
        except (AttributeError, TypeError, ValueError) as exc:
            raise AppleProfileError(f"Missing or invalid integer {field}") from exc
        if value < 0:
            raise AppleProfileError(f"Negative {field}")
        return value

    for row in table.findall("row"):
        if len(row) != len(columns):
            raise AppleProfileError("Row does not match exported schema")
        cells = {name: resolve(cell) for name, cell in zip(columns, row)}
        process = cells["process"]
        row_pid = integer(process.find("pid"), "pid")
        timestamp = integer(cells["time"], "time")
        if row_pid != pid or timestamp < start_ns or (end_ns is not None and timestamp >= end_ns):
            excluded += 1
            continue
        thread = cells["thread"]
        tid = integer(thread.find("tid"), "tid")
        thread_process = resolve(thread.find("process"))
        if thread_process is not None and integer(thread_process.find("pid"), "thread pid") != pid:
            raise AppleProfileError("Thread and process PID disagree")
        core = integer(cells["core"], "core")
        sample = {"time_ns": timestamp, "pid": pid, "tid": tid, "core": core,
                  "core_label": cells["core"].get("fmt")}
        if schema == "cpu-pmc-value":
            event = cells["name"].get("fmt") or cells["name"].text
            if not event or not event.strip():
                raise AppleProfileError("Missing PMC event name")
            sample.update(event=event, value=integer(cells["value"], "PMC value"),
                          native_event_id=cells["name"].text,
                          sample_index=integer(cells["sample-index"], "sample index"))
            if sample["value"] >= 2**64:
                raise AppleProfileError("PMC value exceeds uint64")
        else:
            frames = []
            for item in cells["stack"]:
                frame = resolve(item)
                if frame.tag != "frame":
                    continue
                binary = resolve(frame.find("binary"))
                frames.append({"name": frame.get("name"), "address": frame.get("addr"),
                               "binary": binary.get("name") if binary is not None else None})
            sample.update(weight=integer(cells["weight"], "weight"), weight_type=types["weight"], frames=frames)
            if frames:
                leaf = frames[0]
                summaries[(leaf["binary"], leaf["name"], leaf["address"])] += 1
        samples.append(sample)
    if not samples:
        raise AppleProfileError(f"No {schema} samples for PID {pid} in the requested window")
    return {
        "schema": schema,
        "column_types": types,
        "scope": {"pid": pid, "start_ns": start_ns, "end_ns": end_ns},
        "sample_count": len(samples), "excluded_rows": excluded,
        "tma": dict.fromkeys(TMA_ROOTS),
        "tma_reason": "Native Apple measurements; validated slot-classification formulas are not implemented.",
        "samples": samples,
        "leaf_sample_counts": [
            {"binary": key[0], "name": key[1], "address": key[2], "samples": count}
            for key, count in summaries.most_common()
        ],
        "limitations": [
            "Samples are scoped by PID and time, not by a function or automatic signpost selection.",
            "Loss, multiplexing and effective sampling rate are unverified; no frequency/residency inference.",
            "PMC values retain native semantics; no event sums, IPC or stall percentages are derived.",
            "Leaf sample counts are observations, not exact instruction costs or pipeline-slot shares.",
        ],
    }


def export_profile(trace: Path, output: Path, mode: str, pid: int | None = None, run: int = 1,
                   start_ns: int = 0, end_ns: int | None = None) -> dict:
    """Export a selected table; counterless traces fail rather than falling back."""
    if mode not in MODES or run < 1:
        raise AppleProfileError("Choose counters, cpu or time mode and a positive run number")
    _validate_scope(pid, start_ns, end_ns)
    if (output / "toc.xml").exists() or (output / "profile.json").exists():
        raise AppleProfileError(f"Export artifacts already exist in {output}; choose a new directory")
    output.mkdir(parents=True, exist_ok=True)
    trace = trace.resolve()
    _run(["xcrun", "xctrace", "export", "--input", str(trace), "--toc", "--output", str(output / "toc.xml")],
         output, "toc", 30)
    toc = _xml(output / "toc.xml")
    selected = toc.find(f"./run[@number='{run}']")
    if selected is None:
        raise AppleProfileError(f"Run {run} is absent from trace")
    _, schema, kind = MODES[mode]
    tables = selected.findall(f"./data/table[@schema='{schema}']")
    if len(tables) != 1:
        found = sorted({node.get("schema", "") for node in selected.findall("./data/table")})
        raise AppleProfileError(
            f"Expected one {schema} table; found {len(tables)}. Available schemas: {', '.join(found)}. "
            "A successful recording or timer samples do not establish PMU access. "
            "Check the trace in Instruments; no automatic retry, privilege change or fallback was performed."
        )
    if pid is None:
        target = selected.find("./info/target/process")
        if target is None or not target.get("pid", "").isdigit():
            raise AppleProfileError("Trace target PID is ambiguous; pass --pid explicitly")
        pid = int(target.get("pid"))
    xml_path = output / "samples.xml"
    xpath = f'/trace-toc/run[@number="{run}"]/data/table[@schema="{schema}"]'
    _run(["xcrun", "xctrace", "export", "--input", str(trace), "--xpath", xpath, "--output", str(xml_path)],
         output, "export", 30)
    profile = parse_profile(xml_path, schema, pid, start_ns, end_ns)
    target = selected.find("./info/target/process")
    profile.update(measurement_kind=kind, run_number=run, raw_xml_sha256=_sha256(xml_path),
                   toc_sha256=_sha256(output / "toc.xml"),
                   counter_configurations=[dict(node.attrib) for node in selected.findall("./data/table")
                                           if node.get("schema") == "counters-profile"],
                   target_exit_status=target.get("return-exit-status") if target is not None else None,
                   target_termination_reason=target.get("termination-reason") if target is not None else None)
    _write_json(output / "profile.json", profile)
    return profile


def record_profile(output: Path, command: list[str], duration: int = 5, mode: str = "counters",
                   pid: int | None = None, start_ns: int = 0, end_ns: int | None = None) -> dict:
    """Launch one explicit target, retain evidence, then export the requested data."""
    if mode not in MODES or duration <= 0:
        raise AppleProfileError("Choose counters, cpu or time mode and a positive duration")
    _validate_scope(pid, start_ns, end_ns)
    if not command or not Path(command[0]).is_file():
        raise AppleProfileError("Supply an executable path after '--'")
    output.mkdir(parents=True, exist_ok=False)
    metadata = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode, "command": command, "executable_sha256": _sha256(Path(command[0])),
        "topdown_version": __version__, "collector_sha256": _sha256(Path(__file__)),
        "duration_limit_seconds": duration, "environment": doctor(), "status": "recording",
    }
    _write_json(output / "capture.json", metadata)
    cmd = ["xcrun", "xctrace", "record", "--template", MODES[mode][0], "--no-prompt",
           "--time-limit", f"{duration}s", "--output", str(output / "recording.trace"),
           "--target-stdout", str(output / "target.stdout"), "--launch", "--", *command]
    try:
        _run(cmd, output, "record", duration + 30)
        profile = export_profile(output / "recording.trace", output, mode, pid, start_ns=start_ns, end_ns=end_ns)
        metadata["status"] = "exported"
        return profile
    except (AppleProfileError, OSError) as exc:
        metadata.update(status="failed", error=str(exc))
        raise
    except KeyboardInterrupt:
        metadata.update(status="interrupted", error="Recording interrupted; target exit is unverified.")
        raise
    finally:
        metadata["ended_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(output / "capture.json", metadata)
