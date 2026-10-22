"""Summarize an xctrace metal-gpu-counter-intervals export without raw samples."""

import argparse
from collections import Counter
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def summarize(source: bytes) -> dict:
    root = ET.fromstring(source)
    schema = root.find(".//schema")
    if schema is None or schema.get("name") != "metal-gpu-counter-intervals":
        raise ValueError("expected metal-gpu-counter-intervals schema")
    references = {}
    names = Counter()
    starts = set()
    for row in root.iter("row"):
        if len(row) < 4:
            raise ValueError("counter row has fewer than four columns")
        values = []
        for cell in row:
            if "id" in cell.attrib:
                references[cell.attrib["id"]] = cell.text
            value = references.get(cell.get("ref"), cell.text)
            values.append(value)
        if not values[0] or not values[3]:
            raise ValueError("counter row lacks start time or counter name")
        starts.add(values[0])
        names[values[3]] += 1
    return {
        "schema": "metal-gpu-counter-intervals",
        "counter_rows": sum(names.values()),
        "sample_intervals": len(starts),
        "counter_names": dict(sorted(names.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    try:
        result = summarize(args.source.read_bytes())
    except (ET.ParseError, ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
