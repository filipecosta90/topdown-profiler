"""Native Apple Instruments capture and XML export, separate from slot TMA."""

import hashlib
import json
import platform
import shlex
import subprocess
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET

from topdown import __version__

TMA_ROOTS = ("Frontend_Bound", "Bad_Speculation", "Backend_Bound", "Retiring")
APPLE_BOTTLENECKS = ("delivery", "discarded", "processing", "useful")
MODES = {
    "counters": ("CPU Counters", "cpu-pmc-value", "native-pmu-values"),
    "cpu": ("CPU Profiler", "cpu-profile", "cycle-sampled-stacks"),
    "time": ("Time Profiler", "time-profile", "timer-sampled-stacks"),
    "fixed": (None, "counters-profile", "native-fixed-counter-samples"),
    "trace": ("Processor Trace", "processor-trace-intervals", "hardware-traced-execution-intervals"),
    "m1": (None, "counters-profile", "native-m1-event-samples"),
    "m1-map": (None, "counters-profile", "native-m1-map-state-samples"),
    "m1-fetch": (None, "counters-profile", "native-m1-fetch-samples"),
}
FIXED_EVENTS = ("FIXED_CYCLES", "FIXED_INSTRUCTIONS")
FIXED_PACKAGE = Path(__file__).with_name("apple_fixed.instrpkg")
M1_EVENTS = ("FIXED_CYCLES", "FIXED_INSTRUCTIONS", "RETIRE_UOP", "BRANCH_MISPRED_NONSPEC",
             "SCHEDULE_UOP", "CORE_ACTIVE_CYCLE", "MAP_DISPATCH_BUBBLE", "MAP_STALL_DISPATCH",
             "L1D_CACHE_MISS_LD", "L1I_CACHE_MISS_DEMAND")
M1_PACKAGE = Path(__file__).with_name("apple_m1.instrpkg")
M1_MAP_EVENTS = ("FIXED_CYCLES", "FIXED_INSTRUCTIONS", "RETIRE_UOP", "MAP_DISPATCH_BUBBLE",
                 "MAP_STALL", "MAP_REWIND", "MAP_STALL_DISPATCH", "MAP_INT_UOP",
                 "MAP_LDST_UOP", "MAP_SIMD_UOP")
M1_MAP_PACKAGE = Path(__file__).with_name("apple_m1_map.instrpkg")
M1_FETCH_EVENTS = ("FIXED_CYCLES", "FIXED_INSTRUCTIONS", "RETIRE_UOP", "CORE_ACTIVE_CYCLE",
                   "MAP_DISPATCH_BUBBLE", "SCHEDULE_EMPTY", "L1I_CACHE_MISS_DEMAND",
                   "L1I_TLB_MISS_DEMAND", "L2_TLB_MISS_INSTRUCTION", "FETCH_RESTART")
M1_FETCH_PACKAGE = Path(__file__).with_name("apple_m1_fetch.instrpkg")
M1_CPU_FAMILY = 0x1B588BB3
APPLE_COLLECTION_MESSAGE = (
    "Apple silicon uses native Instruments profiles, not Linux perf/toplev. "
    "Use 'topdown apple doctor', 'topdown apple stat --output <new-directory> -- /path/to/program [args]', "
    "or 'topdown apple record --mode fixed --output <new-directory> -- /path/to/program [args]'. "
    "Four-category slot TMA is not yet supported."
)


class AppleProfileError(RuntimeError):
    """A capture or export cannot supply the requested native measurement."""

    def __init__(self, message: str, *, code: str = "invalid_data"):
        super().__init__(message)
        self.code = code


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
        raise AppleProfileError(f"{name} timed out after {timeout}s; retained partial artifacts in {output}",
                                code="timeout") from exc
    except OSError as exc:
        code = "tool_unavailable" if isinstance(exc, FileNotFoundError) else "tool_error"
        raise AppleProfileError(f"Cannot execute {cmd[0]}: {exc}", code=code) from exc
    (output / f"{name}.stdout").write_text(result.stdout)
    (output / f"{name}.stderr").write_text(result.stderr)
    text = result.stdout + result.stderr
    if result.returncode or "[Error]" in text:
        detail = result.stderr.strip() or result.stdout.strip()
        denied = any(word in text.lower() for word in ("operation not permitted", "permission denied"))
        raise AppleProfileError(f"{name} failed (exit {result.returncode}): {detail}. Artifacts: {output}",
                                code="permission_denied" if denied else "tool_error")
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
                result[key] = list(dict.fromkeys(name for name, _, _ in MODES.values()
                                                if name and name in proc.stdout.splitlines()))
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


def parse_profile(path: Path, schema: str, pid: int, start_ns: int = 0, end_ns: int | None = None,
                  *, fixed_events: tuple[str, ...] | None = None) -> dict:
    """Parse one exported table; require explicit PID and optional half-open window.

    Native PMU values remain integers with their event names. They are not assumed
    to be cumulative counters, deltas, or additive samples, and are never summed.
    """
    _validate_scope(pid, start_ns, end_ns)
    if pid is None:
        raise AppleProfileError("Require an explicit PID for XML parsing")
    if schema not in {entry[1] for entry in MODES.values()}:
        raise AppleProfileError(f"Unsupported measurement schema {schema}")
    if schema == "processor-trace-intervals":
        raise AppleProfileError("Processor Trace requires its gap table; use export with trace mode")
    fixed = schema == "counters-profile"
    if fixed and fixed_events not in (FIXED_EVENTS, M1_EVENTS, M1_MAP_EVENTS, M1_FETCH_EVENTS):
        raise AppleProfileError("Require a validated event order from trace metadata")
    root = _xml(path)
    resolve = _resolve_ids(root)
    samples, summaries = [], Counter()
    excluded, unmeasured = 0, 0
    tables = [node for node in root.iter("node") if node.find("schema") is not None]
    if len(tables) != 1 or tables[0].find("schema").get("name") != schema:
        raise AppleProfileError(f"Expected exactly one {schema} table in {path}")
    table = tables[0]
    metadata = table.find("schema")
    columns = [col.findtext("mnemonic") for col in metadata.findall("col")]
    types = {col.findtext("mnemonic"): col.findtext("engineering-type") for col in metadata.findall("col")}
    required = {"time", "thread", "process", "core"}
    required |= {"name", "value", "sample-index"} if schema == "cpu-pmc-value" else {"weight", "stack"}
    if fixed:
        required |= {"counters-array", "thread-state"}
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
    if fixed and (types["counters-array"] != "pmc-events" or types["weight"] != "any"
                  or types["thread-state"] != "thread-state"):
        raise AppleProfileError("Unknown fixed-counter array, weight or thread-state type")

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
        if fixed and cells["counters-array"].tag == "sentinel":
            unmeasured += 1
            continue
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
            if fixed:
                values = cells["counters-array"]
                if values.tag != "pmc-events" or cells["weight"].tag != "weight":
                    raise AppleProfileError("Unknown fixed-counter array or native timer-weight representation")
                try:
                    native_values = [int(value) for value in (values.text or "").split()]
                except ValueError as exc:
                    raise AppleProfileError("Invalid fixed-counter integer") from exc
                if len(native_values) != len(fixed_events) or any(not 0 <= value < 2**64 for value in native_values):
                    raise AppleProfileError("Require one uint64 value per configured event")
                sample.update(native_weight_type=cells["weight"].tag,
                              thread_state=cells["thread-state"].get("fmt"))
                if fixed_events == FIXED_EVENTS:
                    sample["event_values"] = dict(zip(FIXED_EVENTS, native_values))
                else:
                    sample["native_event_values"] = native_values
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
        "sample_count": len(samples), "excluded_rows": excluded, "unmeasured_rows": unmeasured,
        "configured_events": list(fixed_events) if fixed else None,
        "event_value_order_verified": fixed_events == FIXED_EVENTS if fixed else None,
        "tma": dict.fromkeys(TMA_ROOTS),
        "tma_available": False,
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
            *(["M1 vector position-to-event mapping awaits calibration."]
              if fixed_events in (M1_EVENTS, M1_MAP_EVENTS, M1_FETCH_EVENTS) else []),
            "Leaf sample counts are observations, not exact instruction costs or pipeline-slot shares.",
        ],
    }


def parse_apple_bottlenecks(path: Path, pid: int, start_ns: int = 0, end_ns: int | None = None) -> dict:
    """Parse Xcode's guided CPU Counters intervals and cycle-weight its four native ratios."""
    _validate_scope(pid, start_ns, end_ns)
    if pid is None:
        raise AppleProfileError("Require an explicit PID for XML parsing")
    root = _xml(path)
    resolve = _resolve_ids(root)
    tables = [node for node in root.iter("node") if node.find("schema") is not None]
    if len(tables) != 1 or tables[0].find("schema").get("name") != "MetricTable":
        raise AppleProfileError(f"Expected exactly one MetricTable table in {path}")
    table = tables[0]
    types = {col.findtext("mnemonic"): col.findtext("engineering-type")
             for col in table.find("schema").findall("col")}
    expected = {"timestamp": "start-time", "duration": "duration", "pmi-event": "string",
                "thread": "thread", "process": "process", "metric-value": "fixed-decimal",
                "core": "core", "is-ratio": "boolean"}
    if any(types.get(name) != kind for name, kind in expected.items()):
        raise AppleProfileError("Unknown guided CPU Counters column types")
    columns = [col.findtext("mnemonic") for col in table.find("schema").findall("col")]
    if len(columns) != len(types):
        raise AppleProfileError("Duplicate guided CPU Counters columns")
    groups = {}
    excluded = 0
    for row in table.findall("row"):
        if len(row) != len(columns):
            raise AppleProfileError("Guided CPU Counters row does not match its schema")
        cells = {name: resolve(cell) for name, cell in zip(columns, row)}
        try:
            row_pid = int(resolve(cells["process"].find("pid")).text)
            timestamp = int(cells["timestamp"].text)
            duration = int(cells["duration"].text)
            tid = int(resolve(cells["thread"].find("tid")).text)
            core = int(cells["core"].text)
        except (AttributeError, TypeError, ValueError) as exc:
            raise AppleProfileError("Invalid guided CPU Counters scope") from exc
        if min(row_pid, timestamp, duration, tid, core) < 0 or duration == 0:
            raise AppleProfileError("Invalid guided CPU Counters scope")
        if row_pid != pid or timestamp < start_ns or (end_ns is not None and timestamp >= end_ns):
            excluded += 1
            continue
        thread_process = resolve(cells["thread"].find("process"))
        if thread_process is not None:
            try:
                thread_pid = int(resolve(thread_process.find("pid")).text)
            except (AttributeError, TypeError, ValueError) as exc:
                raise AppleProfileError("Invalid guided CPU Counters thread PID") from exc
            if thread_pid != pid:
                raise AppleProfileError("Thread and process PID disagree")
        event = cells["pmi-event"].text
        if event not in ("cycle", *APPLE_BOTTLENECKS):
            raise AppleProfileError(f"Unexpected guided CPU Counters metric {event}")
        try:
            value = Decimal(cells["metric-value"].text)
            ratio = int(cells["is-ratio"].text)
        except (AttributeError, TypeError, ValueError, InvalidOperation) as exc:
            raise AppleProfileError("Invalid guided CPU Counters value") from exc
        if (not value.is_finite() or value < 0 or ratio != (event != "cycle")
                or (event != "cycle" and value > 1)):
            raise AppleProfileError("Invalid guided CPU Counters value")
        if event == "cycle" and (value != value.to_integral_value() or value >= 2**64):
            raise AppleProfileError("Invalid guided CPU Counters cycle count")
        key = (timestamp, duration, tid, core)
        entry = groups.setdefault(key, {"time_ns": timestamp, "duration_ns": duration, "pid": pid,
                                        "tid": tid, "core": core, "core_label": cells["core"].get("fmt")})
        if event in entry:
            raise AppleProfileError(f"Duplicate guided CPU Counters metric {event}")
        entry[event] = int(value) if event == "cycle" else float(value)
    samples = []
    skipped = 0
    for entry in groups.values():
        if not all(event in entry for event in ("cycle", *APPLE_BOTTLENECKS)):
            skipped += 1
            continue
        total = sum(entry[event] for event in APPLE_BOTTLENECKS)
        if entry["cycle"] == 0 or abs(total - 1) > 0.01:
            skipped += 1
            continue
        entry["shares"] = {event: entry.pop(event) for event in APPLE_BOTTLENECKS}
        entry["cycles"] = entry.pop("cycle")
        samples.append(entry)
    if not samples:
        raise AppleProfileError(f"No valid guided CPU Counters intervals for PID {pid}")

    def summary(rows):
        cycles = sum(row["cycles"] for row in rows)
        return {"cycles": cycles, "sample_count": len(rows),
                "shares": {event: sum(row["cycles"] * row["shares"][event] for row in rows) / cycles
                           for event in APPLE_BOTTLENECKS}}

    by_core = {}
    for row in samples:
        by_core.setdefault(row["core"], []).append(row)
    return {
        "schema": "MetricTable", "column_types": types,
        "scope": {"pid": pid, "start_ns": start_ns, "end_ns": end_ns},
        "sample_count": len(samples), "excluded_rows": excluded, "invalid_intervals": skipped,
        "apple_bottlenecks_available": True,
        "apple_bottlenecks": summary(samples),
        "apple_bottlenecks_by_core": [
            {"core": core, "core_label": rows[0]["core_label"], **summary(rows)}
            for core, rows in sorted(by_core.items())
        ],
        "tma": dict.fromkeys(TMA_ROOTS), "tma_available": False,
        "tma_reason": "Xcode's guided categories are Apple metrics, not a validated Intel slot-TMA mapping.",
        "samples": samples, "leaf_sample_counts": [],
        "limitations": [
            "Guided CPU Counters ratios use Xcode's native, unpublished classification formulas.",
            "Shares are weighted by Xcode's cycle count for each valid interval.",
            "Intervals are selected by start time; a boundary interval can extend beyond the requested window.",
            "Core migration and counter loss can affect aggregate shares; inspect the per-core results.",
        ],
    }


def export_profile(trace: Path, output: Path, mode: str, pid: int | None = None, run: int = 1,
                   start_ns: int = 0, end_ns: int | None = None) -> dict:
    """Export a selected table; counterless traces fail rather than falling back."""
    if mode not in MODES or run < 1:
        raise AppleProfileError("Choose counters, fixed, m1, m1-map, m1-fetch, trace, cpu or time mode and a positive run number")
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
    if mode == "counters" and not tables:
        guided = selected.findall("./data/table[@schema='MetricTable']")
        if len(guided) == 1:
            schema, kind, tables = "MetricTable", "apple-guided-bottleneck-intervals", guided
    if len(tables) != 1:
        found = sorted({node.get("schema", "") for node in selected.findall("./data/table")})
        raise AppleProfileError(
            f"Expected one {schema} table; found {len(tables)}. Available schemas: {', '.join(found)}. "
            "A successful recording or timer samples do not establish PMU access. "
            "Inspect the retained trace and recording logs; no automatic retry, privilege change or fallback was performed.",
            code="counter_data_missing" if not tables and mode in {"counters", "fixed", "m1", "m1-map", "m1-fetch"}
            else "invalid_data",
        )
    fixed_events = None
    if mode in {"fixed", "m1", "m1-map", "m1-fetch"}:
        try:
            fixed_events = tuple(shlex.split(tables[0].get("pmc-events", "")))
        except ValueError as exc:
            raise AppleProfileError("Invalid configured event order") from exc
        expected = {"fixed": FIXED_EVENTS, "m1": M1_EVENTS, "m1-map": M1_MAP_EVENTS,
                    "m1-fetch": M1_FETCH_EVENTS}[mode]
        if fixed_events != expected or tables[0].get("trigger") != "time":
            raise AppleProfileError(f"{mode} mode requires its configured events in order with a timer trigger")
    if pid is None:
        target = selected.find("./info/target/process")
        if target is None or not target.get("pid", "").isdigit():
            raise AppleProfileError("Trace target PID is ambiguous; pass --pid explicitly")
        pid = int(target.get("pid"))
    xml_path = output / ("intervals.xml" if mode == "trace" else "samples.xml")
    xpath = f'/trace-toc/run[@number="{run}"]/data/table[@schema="{schema}"]'
    _run(["xcrun", "xctrace", "export", "--input", str(trace), "--xpath", xpath, "--output", str(xml_path)],
         output, "export", 30)
    if mode == "trace":
        from topdown.collector.apple_trace import parse_trace_profile

        if len(selected.findall("./data/table[@schema='processor-trace-gaps']")) != 1:
            raise AppleProfileError("Processor Trace requires exactly one gap table")
        gaps_path = output / "gaps.xml"
        gaps_xpath = f'/trace-toc/run[@number="{run}"]/data/table[@schema="processor-trace-gaps"]'
        _run(["xcrun", "xctrace", "export", "--input", str(trace), "--xpath", gaps_xpath,
              "--output", str(gaps_path)], output, "export-gaps", 30)
        profile = parse_trace_profile(xml_path, gaps_path, pid, start_ns, end_ns)
        profile["gaps_xml_sha256"] = _sha256(gaps_path)
    else:
        profile = (parse_apple_bottlenecks(xml_path, pid, start_ns, end_ns) if schema == "MetricTable"
                   else parse_profile(xml_path, schema, pid, start_ns, end_ns, fixed_events=fixed_events))
    target = selected.find("./info/target/process")
    profile.update(measurement_kind=kind, run_number=run, raw_xml_sha256=_sha256(xml_path),
                   toc_sha256=_sha256(output / "toc.xml"),
                   counter_configurations=[dict(node.attrib) for node in selected.findall("./data/table")
                                           if node.get("schema") == "counters-profile"],
                   target_exit_status=target.get("return-exit-status") if target is not None else None,
                   target_termination_reason=target.get("termination-reason") if target is not None else None)
    _write_json(output / "profile.json", profile)
    return profile


def _build_counter_instrument(output: Path, source_package: Path, name: str) -> Path:
    """Compile a local counter package without installing it."""
    try:
        _run(["xcrun", "--find", "instrumentbuilder"], output, "find-instrumentbuilder", 10)
    except AppleProfileError as exc:
        if exc.code == "tool_error":
            raise AppleProfileError(f"Xcode instrumentbuilder is required for {name} mode: {exc}",
                                    code="tool_unavailable") from exc
        raise
    source = output / f"{name}-counters.instrpkg"
    source.write_bytes(source_package.read_bytes())
    package = output / f"{name}-counters.instrdst"
    _run(["xcrun", "instrumentbuilder", "-i", str(source), "-o", str(package), "-l", "CPU Counters"],
         output, f"build-{name}-instrument", 30)
    if not (package / "Contents/pkginfo.plist").is_file():
        raise AppleProfileError(f"instrumentbuilder did not produce a valid {name} counter package")
    return package


def _build_fixed_instrument(output: Path) -> Path:
    return _build_counter_instrument(output, FIXED_PACKAGE, "fixed")


def _require_m1_host():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise AppleProfileError("M1 event mode requires an Apple M1 host")
    try:
        family = int(subprocess.run(["sysctl", "-n", "hw.cpufamily"], capture_output=True, text=True,
                                    timeout=10, check=True).stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise AppleProfileError(f"Cannot verify M1 CPU family: {exc}") from exc
    if family != M1_CPU_FAMILY:
        raise AppleProfileError(f"M1 event mode requires CPU family {M1_CPU_FAMILY:#x}; found {family:#x}")


def record_profile(output: Path, command: list[str], duration: int = 5, mode: str = "counters",
                   pid: int | None = None, start_ns: int = 0, end_ns: int | None = None) -> dict:
    """Launch one explicit target, retain evidence, then export the requested data."""
    if mode not in MODES or duration <= 0:
        raise AppleProfileError("Choose counters, fixed, m1, m1-map, m1-fetch, trace, cpu or time mode and a positive duration")
    _validate_scope(pid, start_ns, end_ns)
    if not command or not Path(command[0]).is_file():
        raise AppleProfileError("Supply an executable path after '--'")
    if mode in {"m1", "m1-map", "m1-fetch"}:
        _require_m1_host()
    output.mkdir(parents=True, exist_ok=False)
    metadata = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode, "command": command, "executable_sha256": _sha256(Path(command[0])),
        "topdown_version": __version__, "collector_sha256": _sha256(Path(__file__)),
        "duration_limit_seconds": duration, "environment": doctor(), "status": "recording",
    }
    _write_json(output / "capture.json", metadata)
    cmd = ["xcrun", "xctrace", "record", "--no-prompt",
           "--time-limit", f"{duration}s", "--output", str(output / "recording.trace"),
           "--target-stdout", str(output / "target.stdout"), "--launch", "--", *command]
    try:
        if mode == "fixed":
            metadata["fixed_package_sha256"] = _sha256(FIXED_PACKAGE)
            package = _build_fixed_instrument(output)
            cmd[3:3] = ["--package", str(package), "--instrument", "Topdown Fixed Counters"]
        elif mode == "m1":
            metadata["m1_package_sha256"] = _sha256(M1_PACKAGE)
            package = _build_counter_instrument(output, M1_PACKAGE, "m1")
            cmd[3:3] = ["--package", str(package), "--instrument", "Topdown M1 Event Controls"]
        elif mode == "m1-map":
            metadata["m1_map_package_sha256"] = _sha256(M1_MAP_PACKAGE)
            package = _build_counter_instrument(output, M1_MAP_PACKAGE, "m1-map")
            cmd[3:3] = ["--package", str(package), "--instrument", "Topdown M1 Map State"]
        elif mode == "m1-fetch":
            metadata["m1_fetch_package_sha256"] = _sha256(M1_FETCH_PACKAGE)
            package = _build_counter_instrument(output, M1_FETCH_PACKAGE, "m1-fetch")
            cmd[3:3] = ["--package", str(package), "--instrument", "Topdown M1 Fetch Diagnostics"]
        else:
            cmd[3:3] = ["--template", MODES[mode][0]]
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
