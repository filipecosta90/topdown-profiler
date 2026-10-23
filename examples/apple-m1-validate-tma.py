"""Compare an M1 calibration trace's raw counters with Xcode guided ratios."""

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from topdown.collector.apple import _resolve_ids, parse_apple_bottlenecks


CATEGORIES = ("useful", "processing", "discarded", "delivery")


def validate(counters: Path, metrics: Path, pid: int) -> dict:
    guided = parse_apple_bottlenecks(metrics, pid)
    by_end = {(row["time_ns"] + row["duration_ns"], row["tid"], row["core"], row["cycles"]): row
              for row in guided["samples"]}
    if len(by_end) != len(guided["samples"]):
        raise ValueError("Guided intervals have duplicate end-time, thread, core, and cycle keys")

    root = ET.parse(counters).getroot()
    tables = [node for node in root.iter("node") if node.find("schema") is not None]
    if len(tables) != 1 or tables[0].find("schema").get("name") != "counters-profile":
        raise ValueError("Expected one counters-profile table")
    columns = [col.findtext("mnemonic") for col in tables[0].find("schema").findall("col")]
    required = {"time", "thread", "process", "core", "counters-array"}
    if not required.issubset(columns) or len(set(columns)) != len(columns):
        raise ValueError("Invalid counters-profile columns")
    resolve = _resolve_ids(root)
    raw_count = matched_count = matched_cycles = 0
    seen_raw = set()
    maximum = dict.fromkeys(CATEGORIES, 0.0)
    for row in tables[0].findall("row"):
        if len(row) != len(columns):
            raise ValueError("Counter row does not match its schema")
        cells = {name: resolve(cell) for name, cell in zip(columns, row)}
        process = cells["process"]
        if process.tag != "process" or int(resolve(process.find("pid")).text) != pid:
            continue
        vector = cells["counters-array"]
        if vector.tag == "sentinel":
            continue
        values = [int(value) for value in vector.text.split()]
        if len(values) != 12 or values[10:] != [0, 0]:
            raise ValueError("Unexpected combined guided/custom vector layout")
        active = values[2]
        if active == 0:
            continue
        raw_count += 1
        core = int(cells["core"].text)
        if not 0 <= core <= 7:
            raise ValueError("Expected M1 core number 0 through 7")
        key = (int(cells["time"].text), int(resolve(cells["thread"].find("tid")).text), core, active)
        if key in seen_raw:
            raise ValueError("Raw counter samples have duplicate end-time, thread, core, and cycle keys")
        seen_raw.add(key)
        interval = by_end.get(key)
        if interval is None:
            continue
        matched_count += 1
        matched_cycles += interval["cycles"]
        width = 4 if core < 4 else 8
        retired, rewind, stall = values[7], values[3], values[4]
        mapped = values[5] + values[6] + values[8]
        estimate = {
            "useful": retired / (width * active),
            "processing": stall / active,
            "discarded": max(0, mapped - retired + width * rewind) / (width * active),
        }
        estimate["delivery"] = max(values[9] / active, 1 - sum(estimate.values()))
        for category in CATEGORIES:
            difference = abs(estimate[category] - interval["shares"][category]) * 100
            maximum[category] = max(maximum[category], difference)
    if not matched_count:
        raise ValueError("No guided interval matches a raw counter sample")
    guided_cycles = sum(row["cycles"] for row in guided["samples"])
    return {"raw_samples_with_active_cycles": raw_count, "matched_intervals": matched_count,
            "unmatched_raw_samples": raw_count - matched_count,
            "guided_intervals": len(guided["samples"]),
            "unmatched_guided_intervals": len(guided["samples"]) - matched_count,
            "matched_guided_cycle_fraction": matched_cycles / guided_cycles,
            "max_absolute_difference_percentage_points": maximum,
            "matches_exported_rounding": max(maximum.values()) < 1e-7}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--counters", type=Path, required=True)
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--pid", type=int, required=True)
    args = parser.parse_args()
    result = validate(args.counters, args.metrics, args.pid)
    print(json.dumps(result, indent=2))
    if not result["matches_exported_rounding"]:
        raise SystemExit(1)
