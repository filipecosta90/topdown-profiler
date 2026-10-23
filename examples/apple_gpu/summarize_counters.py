"""Summarize an xctrace metal-gpu-counter-intervals export without raw samples."""

import argparse
from bisect import bisect_left
from collections import Counter
import io
import json
import math
from pathlib import Path
import re
from typing import NamedTuple
import xml.etree.ElementTree as ET


PROBE_LABEL = re.compile(r"^topdown GPU probe pass (\d+):(burn|stream) compute pass \1(?:\s|$)")


class _Cell(NamedTuple):
    text: str | None
    fmt: str | None
    pid: str | None = None


def _table(source: bytes | Path, expected_schema: str):
    stream = source.open("rb") if isinstance(source, Path) else io.BytesIO(source)
    references = {}
    columns = None
    schema_count = 0
    stack = []
    with stream:
        for event, element in ET.iterparse(stream, events=("start", "end")):
            if event == "start":
                stack.append(element)
                continue
            if element.tag == "schema":
                schema_count += 1
                if schema_count != 1 or element.get("name") != expected_schema:
                    raise ValueError(f"expected one {expected_schema} schema")
                columns = [column.findtext("mnemonic") for column in element.findall("col")]
                if len(columns) != len(set(columns)):
                    raise ValueError("duplicate table columns")
            elif element.tag == "row":
                if columns is None:
                    raise ValueError("row appears before its schema")
                for item in element.iter():
                    if item.get("id"):
                        if item.get("id") in references:
                            raise ValueError(f"duplicate XML id {item.get('id')}")
                        references[item.get("id")] = _Cell(item.text, item.get("fmt"))
                for item in element.iter("process"):
                    if item.get("id"):
                        child = item.find("pid")
                        if child is not None:
                            value = references.get(child.get("ref")) if child.get("ref") else _Cell(child.text, None)
                            references[item.get("id")] = _Cell(item.text, item.get("fmt"), value.text if value else None)
                cells = []
                for item in element:
                    if item.get("ref"):
                        try:
                            cells.append(references[item.get("ref")])
                        except KeyError as error:
                            raise ValueError(f"unresolved XML reference {error.args[0]}") from error
                    elif item.tag == "process":
                        child = item.find("pid")
                        value = references.get(child.get("ref")) if child is not None and child.get("ref") else None
                        cells.append(_Cell(item.text, item.get("fmt"), value.text if value else child.text if child is not None else None))
                    else:
                        cells.append(_Cell(item.text, item.get("fmt")))
                if columns and len(cells) != len(columns):
                    raise ValueError("table row does not match its schema")
                stack[-2].remove(element)
                element.clear()
                yield columns, cells
            stack.pop()
    if schema_count != 1:
        raise ValueError(f"expected one {expected_schema} schema")


def _integer(cell, field: str) -> int:
    try:
        value = int(cell.text)
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid {field}") from error
    if value < 0:
        raise ValueError(f"negative {field}")
    return value


def _overlap(start: int, end: int, windows: list[tuple[int, int]]) -> int:
    if not windows or end <= start:
        return 0
    index = max(0, bisect_left(windows, (start, -1)) - 1)
    total = 0
    while index < len(windows) and windows[index][0] < end:
        begin, stop = windows[index]
        total += max(0, min(end, stop) - max(start, begin))
        index += 1
    return total


def _merge(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged = []
    for begin, end in sorted(windows):
        if merged and begin <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((begin, end))
    return merged


def _process_pid(cell) -> int | None:
    return _integer(_Cell(cell.pid, None), "probe PID") if cell.pid is not None else None


def summarize(source: bytes, gpu_intervals: bytes | None = None, pid: int | None = None) -> dict:
    if pid is not None and (type(pid) is not int or pid <= 0):
        raise ValueError("probe PID must be a positive integer")
    if pid is not None and gpu_intervals is None:
        raise ValueError("probe PID requires GPU intervals")
    names = Counter()
    starts = set()
    samples = set()
    values = []
    for columns, row in _table(source, "metal-gpu-counter-intervals"):
        if len(row) < 4:
            raise ValueError("counter row has fewer than four columns")
        if not row[0].text or not row[3].text:
            raise ValueError("counter row lacks start time or counter name")
        start = _integer(row[0], "counter start")
        duration = _integer(row[1], "counter duration")
        if duration == 0:
            raise ValueError("counter duration must be positive")
        starts.add(start)
        samples.add((start, start + duration))
        names[row[3].text] += 1
        if {"name", "value", "label", "is-percentage"}.issubset(columns):
            name = row[columns.index("name")].fmt or row[columns.index("name")].text
            label = row[columns.index("label")].fmt or ""
            try:
                value = float(row[columns.index("value")].text)
            except (TypeError, ValueError) as error:
                raise ValueError(f"invalid counter value for {name}") from error
            if not math.isfinite(value):
                raise ValueError(f"non-finite counter value for {name}")
            percentage = row[columns.index("is-percentage")].text
            if percentage not in {"0", "1"}:
                raise ValueError(f"invalid percentage flag for {name}")
            unit = "%" if percentage == "1" else "GB/s" if " GB/s" in label else "native"
            values.append((start, start + duration, name, value, unit, label))
    result = {
        "schema": "metal-gpu-counter-intervals",
        "counter_rows": sum(names.values()),
        "sample_intervals": len(starts),
        "counter_names": dict(sorted(names.items())),
    }
    if gpu_intervals is None:
        return result

    labeled = []
    active = []
    state_available = False
    for columns, row in _table(gpu_intervals, "metal-gpu-intervals"):
        try:
            start_cell = row[columns.index("start")]
            duration_cell = row[columns.index("duration")]
            label_cell = row[columns.index("event-label")]
        except (ValueError, IndexError) as error:
            raise ValueError("GPU interval table lacks probe fields") from error
        match = PROBE_LABEL.match(label_cell.fmt or label_cell.text or "")
        process_pid = None
        if "process" in columns:
            process_pid = _process_pid(row[columns.index("process")])
            if match and process_pid is None:
                raise ValueError("labeled probe GPU interval lacks a process PID")
        if match:
            start = _integer(start_cell, "probe start")
            duration = _integer(duration_cell, "probe duration")
            if duration == 0:
                raise ValueError("probe duration must be positive")
            labeled.append((process_pid, match.group(2), int(match.group(1)), start, start + duration))
        if "state" in columns:
            state_available = True
            state_cell = row[columns.index("state")]
            if (state_cell.fmt or state_cell.text) == "Active":
                start = _integer(start_cell, "other GPU start")
                duration = _integer(duration_cell, "other GPU duration")
                active.append((process_pid, bool(match), start, start + duration))

    probe_pids = {process_pid for process_pid, _, _, _, _ in labeled if process_pid is not None}
    if pid is None and len(probe_pids) > 1:
        raise ValueError("multiple labeled probe PIDs; pass --pid to select one")
    selected_pid = pid if pid is not None else next(iter(probe_pids), None)
    if pid is not None and pid not in probe_pids:
        raise ValueError(f"no labeled probe intervals for PID {pid}")
    passes = {}
    for process_pid, workload, index, start, end in labeled:
        if selected_pid is None or process_pid == selected_pid:
            passes.setdefault((workload, index), []).append((start, end))
    other_active = [(start, end) for process_pid, probe, start, end in active
                    if not probe or (selected_pid is not None and process_pid != selected_pid)]

    pass_windows = {key: _merge(pieces) for key, pieces in passes.items()}
    windows = _merge([window for pieces in pass_windows.values() for window in pieces])
    sampled = _merge([sample for sample in samples if _overlap(*sample, windows)])
    covered = sum(any(_overlap(*sample, pieces) for sample in sampled) for pieces in pass_windows.values())
    result["probe_coverage"] = {
        "status": "no_probe_intervals" if not passes else "sampled" if covered else "no_counter_overlap",
        "workloads_observed": sorted({workload for workload, _ in passes}),
        "passes_observed": len(passes),
        "passes_with_counter_samples": covered,
        "gpu_work_time_ns": sum(end - start for start, end in windows),
        "sampled_gpu_work_time_ns": sum(_overlap(start, end, windows) for start, end in sampled),
        "counter_sample_intervals_overlapping": sum(bool(_overlap(*sample, windows)) for sample in samples),
        "other_active_gpu_time_overlapping_ns": (
            sum(_overlap(start, end, windows) for start, end in _merge(other_active)) if state_available else None
        ),
        "last_counter_end_minus_last_probe_end_ns": (
            max(end for _, end in samples) - max(end for _, end in windows) if samples and windows else None
        ),
    }
    means = {}
    top_categories = Counter()
    for start, end, name, value, unit, label in values:
        overlap = _overlap(start, end, windows)
        if not overlap:
            continue
        metric = means.setdefault(name, {"weighted_sum": 0.0, "sampled_time_ns": 0, "counter_intervals": 0, "unit": unit})
        if metric["unit"] != unit:
            raise ValueError(f"mixed units for {name}")
        metric["weighted_sum"] += overlap * value
        metric["sampled_time_ns"] += overlap
        metric["counter_intervals"] += 1
        if name == "Top Performance Limiter":
            category = re.search(r"\((-?\d+\.([^()]*)\))$", label)
            if category:
                top_categories[category.group(2)] += overlap
    result["sampled_counter_means"] = {
        name: {
            "value": round(metric["weighted_sum"] / metric["sampled_time_ns"], 6),
            "unit": metric["unit"],
            "sampled_time_ns": metric["sampled_time_ns"],
            "counter_intervals": metric["counter_intervals"],
        }
        for name, metric in sorted(means.items())
    }
    top_total = sum(top_categories.values())
    result["top_limiter_categories"] = [
        {
            "name": name,
            "sampled_time_ns": duration,
            "share_of_top_limiter_samples_percent": round(100 * duration / top_total, 4),
        }
        for name, duration in sorted(top_categories.items(), key=lambda item: (-item[1], item[0]))
    ]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--gpu-intervals", type=Path, help="exported metal-gpu-intervals XML for probe coverage")
    parser.add_argument("--pid", type=int, help="select a labeled probe PID when the trace contains multiple probes")
    args = parser.parse_args()
    try:
        result = summarize(args.source, args.gpu_intervals, args.pid)
    except (ET.ParseError, ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
