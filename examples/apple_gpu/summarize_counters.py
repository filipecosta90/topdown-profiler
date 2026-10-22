"""Summarize an xctrace metal-gpu-counter-intervals export without raw samples."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


PROBE_LABEL = re.compile(r"^topdown GPU probe pass (\d+):burn compute pass \1(?:\s|$)")


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
    return sum(max(0, min(end, stop) - max(start, begin)) for begin, stop in windows)


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
    for _, row in _table(source, "metal-gpu-counter-intervals"):
        if len(row) < 4:
            raise ValueError("counter row has fewer than four columns")
        if not row[0].text or not row[3].text:
            raise ValueError("counter row lacks start time or counter name")
        start = _integer(row[0], "counter start")
        duration = _integer(row[1], "counter duration")
        starts.add(start)
        samples.add((start, start + duration))
        names[row[3].text] += 1
    result = {
        "schema": "metal-gpu-counter-intervals",
        "counter_rows": sum(names.values()),
        "sample_intervals": len(starts),
        "counter_names": dict(sorted(names.items())),
    }
    if gpu_intervals is None:
        return result

    passes = {}
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
            passes.setdefault(int(match.group(1)), []).append((start, start + duration))

    windows = _merge([window for pieces in passes.values() for window in pieces])
    sampled = _merge([sample for sample in samples if _overlap(*sample, windows)])
    covered = sum(any(_overlap(*sample, pieces) for sample in sampled) for pieces in passes.values())
    result["probe_coverage"] = {
        "passes_observed": len(passes),
        "passes_with_counter_samples": covered,
        "gpu_work_time_ns": sum(end - start for start, end in windows),
        "sampled_gpu_work_time_ns": sum(_overlap(start, end, windows) for start, end in sampled),
        "counter_sample_intervals_overlapping": sum(bool(_overlap(*sample, windows)) for sample in samples),
        "last_counter_end_minus_last_probe_end_ns": (
            max(end for _, end in samples) - max(end for _, end in windows) if samples and windows else None
        ),
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
