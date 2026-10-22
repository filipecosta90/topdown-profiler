"""Store native Apple artifacts without converting their values into Linux TMA samples."""

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from topdown.collector.apple import APPLE_BOTTLENECKS, MODES, TMA_ROOTS, AppleProfileError
from topdown.storage.models import Run

PROFILE_SCHEMAS = {kind: schema for _, schema, kind in MODES.values()}
PROFILE_SCHEMAS["apple-guided-bottleneck-intervals"] = "MetricTable"
STAT_KIND = "process-accounted-fixed-counters"
SOC_KIND = "native-soc-firmware-state-observations"
BULK_FIELDS = {"samples", "intervals", "gaps", "channels", "leaf_sample_counts"}


def _uint(value):
    return type(value) is int and 0 <= value < 2**64


def validate_result(result: dict) -> None:
    if not isinstance(result, dict) or not isinstance(result.get("measurement_kind"), str):
        raise AppleProfileError("Require a native Apple profile.json, stat.json or soc.json result")
    if result.get("tma_available") is not False or result.get("tma") != dict.fromkeys(TMA_ROOTS):
        raise AppleProfileError("Apple results must retain unavailable Intel TMA fields as null")
    kind = result["measurement_kind"]
    if kind == STAT_KIND:
        counters = result.get("counters")
        if (not isinstance(counters, dict) or not {"instructions", "cycles"}.issubset(counters)
                or not all(_uint(value) for value in counters.values())):
            raise AppleProfileError("Require observed unsigned process counters")
        if not isinstance(result.get("scope"), dict) or not _uint(result["scope"].get("pid")) or not result["scope"]["pid"]:
            raise AppleProfileError("Process counters require an explicit positive PID")
    elif kind == SOC_KIND:
        channels = result.get("channels")
        if not isinstance(channels, list) or not channels or not all(isinstance(c, dict) for c in channels):
            raise AppleProfileError("Require native SoC channel observations")
        for channel in channels:
            states = channel.get("states")
            if (not isinstance(states, list) or not states
                    or not all(isinstance(s, dict) and _uint(s.get("value")) for s in states)):
                raise AppleProfileError("Require native unsigned SoC state values")
    elif kind in PROFILE_SCHEMAS:
        if result.get("schema") != PROFILE_SCHEMAS[kind]:
            raise AppleProfileError("Apple measurement kind and schema disagree")
        trace = result["schema"] == "processor-trace-intervals"
        field, count = ("intervals", "interval_count") if trace else ("samples", "sample_count")
        rows = result.get(field)
        if (not isinstance(rows, list) or not rows or type(result.get(count)) is not int
                or result[count] != len(rows) or not all(isinstance(row, dict) for row in rows)):
            raise AppleProfileError(f"Require native {field} and a matching {count}")
        if trace:
            if (not isinstance(result.get("gaps"), list) or type(result.get("gap_count")) is not int
                    or result["gap_count"] != len(result["gaps"]) or result.get("coverage_complete") is not False):
                raise AppleProfileError("Trace results must retain their gaps and incomplete decoding status")
        if result["schema"] == "MetricTable":
            summary = result.get("apple_bottlenecks", {})
            shares = summary.get("shares", {}) if isinstance(summary, dict) else {}
            if (not isinstance(shares, dict) or set(shares) != set(APPLE_BOTTLENECKS)
                    or not all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in shares.values())):
                raise AppleProfileError("Require Apple's four native bottleneck shares")
    else:
        raise AppleProfileError(f"Unsupported Apple measurement kind: {kind}")
    try:
        json.dumps(result, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise AppleProfileError(f"Invalid native Apple JSON: {exc}") from exc


def _read_json(path):
    try:
        data = path.read_bytes()
        value = json.loads(data)
    except (OSError, ValueError) as exc:
        raise AppleProfileError(f"Cannot read Apple artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AppleProfileError(f"Require a JSON object in {path}")
    return value, hashlib.sha256(data).hexdigest()


def _date(value):
    try:
        date = datetime.fromisoformat(value)
        if date.tzinfo is None:
            raise ValueError("timezone required")
        return date
    except (ValueError, TypeError) as exc:
        raise AppleProfileError("Apple artifact timestamps must be timezone-aware ISO dates") from exc


def validate_run_options(process_name: str | None, labels: dict | None) -> None:
    if labels is not None and (not isinstance(labels, dict)
                              or not all(isinstance(k, str) and isinstance(v, str) for k, v in labels.items())):
        raise AppleProfileError("Labels must map strings to strings")
    if process_name is not None and (not isinstance(process_name, str) or not process_name.strip()):
        raise AppleProfileError("Require a nonempty process name")


def store_apple_result(backend, path: Path, process_name: str | None = None, labels: dict | None = None) -> Run:
    """Import one artifact as a labeled run, retaining its complete native document."""
    validate_run_options(process_name, labels)
    if path.is_dir():
        candidates = [p for name in ["profile.json", "stat.json", "soc.json"] if (p := path / name).is_file()]
        if len(candidates) != 1:
            raise AppleProfileError("Choose one profile.json, stat.json or soc.json file explicitly")
        path = candidates[0]
    result, source_sha256 = _read_json(path)
    validate_result(result)
    capture_path = path.parent / "capture.json"
    capture = _read_json(capture_path)[0] if path.name == "profile.json" and capture_path.is_file() else {}
    imported = datetime.now(timezone.utc)
    times = result if result.get("started_at") else capture
    started = _date(times["started_at"]) if times.get("started_at") else imported
    ended = _date(times["ended_at"]) if times.get("started_at") and times.get("ended_at") else None
    if ended is not None and ended < started:
        raise AppleProfileError("Apple artifact ends before it starts")
    time_basis = "result_timestamps" if times is result else "capture_metadata"
    if not times.get("started_at"):
        time_basis = "import_time"
    command = result.get("command") or capture.get("command")
    scope = result.get("scope", {})
    pid = scope.get("pid") if isinstance(scope, dict) else None
    system_wide = result["measurement_kind"] == SOC_KIND
    if not process_name:
        if isinstance(command, list) and command and isinstance(command[0], str):
            process_name = Path(command[0]).name
        else:
            process_name = "system" if system_wide else f"pid:{pid}" if pid else "apple-profile"
    environment = result.get("environment") or capture.get("environment") or {}
    all_labels = dict(labels or {})
    if isinstance(environment, dict):
        for key in ["cpu", "arch", "os_version", "os_build", "kernel_uuid", "xctrace"]:
            if isinstance(environment.get(key), str):
                all_labels.setdefault(key, environment[key])
    all_labels.update(collector="apple", measurement_kind=result["measurement_kind"], time_basis=time_basis)
    run = Run(started_at=started, ended_at=ended, duration_seconds=(ended - started).total_seconds() if ended else 0,
              process_name=process_name, level=0, system_wide=system_wide, labels=all_labels)
    metadata = {"imported_at": imported.isoformat(), "source_sha256": source_sha256, "time_basis": time_basis,
                "source_filename": path.name, "capture": capture}
    backend.insert_apple_result(run, {"schema_version": 1, "result": result, "metadata": metadata})
    return run


def query_apple_result(backend, run_id: str, section: str = "summary", offset: int = 0, limit: int = 100) -> dict:
    if section not in {"summary", *BULK_FIELDS} or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
        raise AppleProfileError("Choose summary/samples/intervals/gaps/channels/leaf_sample_counts, offset >= 0 and limit 1–1000")
    run = backend.get_run(run_id)
    document = backend.get_apple_result(run_id)
    if run is None or document is None:
        raise AppleProfileError(f"No stored Apple result for run {run_id}")
    result = document["result"]
    response = {"run_id": run_id, "process_name": run.process_name, "labels": run.labels,
                "metadata": document["metadata"], "result": {k: v for k, v in result.items() if k not in BULK_FIELDS}}
    if section != "summary":
        rows = result.get(section)
        if not isinstance(rows, list):
            raise AppleProfileError(f"This Apple result has no {section} section")
        response.update(section=section, offset=offset, limit=limit, total=len(rows), rows=rows[offset:offset + limit])
    return response


def stored_apple_hotspots(backend, run_id: str, limit: int = 20) -> dict:
    from topdown.apple_report import summarize_hotspots

    document = backend.get_apple_result(run_id)
    if document is None:
        raise AppleProfileError(f"No stored Apple result for run {run_id}")
    return {"run_id": run_id, **summarize_hotspots(document["result"], limit)}


def collect_apple_result(output: Path, mode: str, command: list[str] | None, pid: int | None,
                         duration_seconds: float) -> Path:
    """Reuse bounded native collectors; retain artifacts even when capture or DB storage fails."""
    if type(duration_seconds) not in (int, float) or not math.isfinite(duration_seconds) or not 0 < duration_seconds <= 300:
        raise AppleProfileError("Require a finite duration greater than zero and at most 300 seconds")
    if command is not None and (not isinstance(command, list) or not command or not all(isinstance(c, str) for c in command)):
        raise AppleProfileError("Command must be a nonempty executable argv list")
    if pid is not None and (type(pid) is not int or pid <= 0):
        raise AppleProfileError("Require a positive PID")
    if mode == "soc":
        if command is not None or pid is not None:
            raise AppleProfileError("SoC observations cover the system/cluster and do not accept a command or PID")
        from topdown.collector.apple_soc import sample_soc

        result = sample_soc(output, duration_seconds)
        name = "soc.json"
    elif mode == "stat":
        if (command is None) == (pid is None):
            raise AppleProfileError("Stat requires either an executable command or an existing PID")
        from topdown.collector.apple_stat import stat_pid, stat_process

        result = stat_pid(output, pid, duration_seconds) if pid is not None else stat_process(output, command, duration_seconds)
        name = "stat.json"
    elif mode in MODES:
        if pid is not None or command is None:
            raise AppleProfileError("Stack/trace recording requires an executable command; use stat for an existing PID")
        if duration_seconds < 1 or int(duration_seconds) != duration_seconds:
            raise AppleProfileError("Instruments recording requires a whole number of seconds, at least one")
        from topdown.collector.apple import record_profile

        record_profile(output, command, int(duration_seconds), mode)
        return output / "profile.json"
    else:
        raise AppleProfileError("Choose stat, soc, fixed, counters, cpu, time, trace, m1, m1-map or m1-fetch")
    if result.get("exit_code") != 0:
        raise AppleProfileError(f"Apple capture {result.get('status', 'failed')}: {result.get('error', '')}. Artifacts: {output}")
    return output / name
