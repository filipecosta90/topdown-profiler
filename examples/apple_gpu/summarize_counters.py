"""Summarize an xctrace metal-gpu-counter-intervals export without raw samples."""

import argparse
from bisect import bisect_left
from collections import Counter
import json
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET


PROBE_LABEL = re.compile(r"^topdown GPU probe pass (\d+):(burn|stream) compute pass \1(?:\s|$)")


def _table(source: bytes, expected_schema: str):
    root = ET.fromstring(source)
    schema = root.find(".//schema")
    if schema is None or schema.get("name") != expected_schema:
        raise ValueError(f"expected {expected_schema} schema")
    columns = [column.findtext("mnemonic") for column in schema.findall("col")]
    references = {node.get("id"): node for node in root.iter() if node.get("id")}
    for row in root.iter("row"):
        cells = []
        for cell in row:
            if cell.get("ref"):
                try:
                    cell = references[cell.get("ref")]
                except KeyError as error:
                    raise ValueError(f"unresolved XML reference {error.args[0]}") from error
            cells.append(cell)
        yield columns, cells


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


def summarize(source: bytes, gpu_intervals: bytes | None = None) -> dict:
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
        starts.add(start)
        samples.add((start, start + duration))
        names[row[3].text] += 1
        if {"name", "value", "label", "is-percentage"}.issubset(columns):
            name = row[columns.index("name")].get("fmt") or row[columns.index("name")].text
            label = row[columns.index("label")].get("fmt") or ""
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
            values.append((start, start + duration, name, value, unit))
    result = {
        "schema": "metal-gpu-counter-intervals",
        "counter_rows": sum(names.values()),
        "sample_intervals": len(starts),
        "counter_names": dict(sorted(names.items())),
    }
    if gpu_intervals is None:
        return result

    passes = {}
    other_active = []
    state_available = False
    for columns, row in _table(gpu_intervals, "metal-gpu-intervals"):
        try:
            start_cell = row[columns.index("start")]
            duration_cell = row[columns.index("duration")]
            label_cell = row[columns.index("event-label")]
        except (ValueError, IndexError) as error:
            raise ValueError("GPU interval table lacks probe fields") from error
        match = PROBE_LABEL.match(label_cell.get("fmt") or label_cell.text or "")
        if match:
            start = _integer(start_cell, "probe start")
            duration = _integer(duration_cell, "probe duration")
            passes.setdefault((match.group(2), int(match.group(1))), []).append((start, start + duration))
        if "state" in columns:
            state_available = True
            state_cell = row[columns.index("state")]
            if not match and (state_cell.get("fmt") or state_cell.text) == "Active":
                start = _integer(start_cell, "other GPU start")
                duration = _integer(duration_cell, "other GPU duration")
                other_active.append((start, start + duration))

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
    for start, end, name, value, unit in values:
        overlap = _overlap(start, end, windows)
        if not overlap:
            continue
        metric = means.setdefault(name, {"weighted_sum": 0.0, "sampled_time_ns": 0, "counter_intervals": 0, "unit": unit})
        if metric["unit"] != unit:
            raise ValueError(f"mixed units for {name}")
        metric["weighted_sum"] += overlap * value
        metric["sampled_time_ns"] += overlap
        metric["counter_intervals"] += 1
    result["sampled_counter_means"] = {
        name: {
            "value": round(metric["weighted_sum"] / metric["sampled_time_ns"], 6),
            "unit": metric["unit"],
            "sampled_time_ns": metric["sampled_time_ns"],
            "counter_intervals": metric["counter_intervals"],
        }
        for name, metric in sorted(means.items())
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--gpu-intervals", type=Path, help="exported metal-gpu-intervals XML for probe coverage")
    args = parser.parse_args()
    try:
        intervals = args.gpu_intervals.read_bytes() if args.gpu_intervals else None
        result = summarize(args.source.read_bytes(), intervals)
    except (ET.ParseError, ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
