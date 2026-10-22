"""Report GPU workload costs and overlapping native limiters from a replay profile."""

import argparse
import json
import math
from pathlib import Path
import re


def percent(value, allow_above_100=False):
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:[.,]\d+)?%", value):
        raise ValueError(f"Unexpected native percentage: {value!r}")
    number = float(value[:-1].replace(",", "."))
    if not math.isfinite(number) or number < 0 or (number > 100 and not allow_above_100):
        raise ValueError(f"Native percentage outside the expected range: {value!r}")
    return number


def summarize(profile):
    if (not isinstance(profile, dict) or profile.get("measurement_kind") != "apple-gpu-replay-profile"
            or profile.get("tma_available") is not False or not isinstance(profile.get("timeline"), dict)
            or not isinstance(profile.get("encoders"), list) or not profile["encoders"]):
        raise ValueError("Require a native GPU replay profile.json with measured encoders")
    rows = []
    for row in profile["encoders"]:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or not isinstance(row.get("counters"), dict) or not isinstance(row.get("listing"), dict)):
            raise ValueError("Missing native encoder counters")
        counters = row["counters"]
        listing = row["listing"].get("values")
        if (not isinstance(listing, list) or not listing or not isinstance(listing[0], dict)
                or not isinstance(listing[0].get("value"), str)):
            raise ValueError("Missing native encoder label")
        limiters = [{"name": key, "percent": percent(value, allow_above_100=True), "native_value": value,
                     "exceeds_100": percent(value, allow_above_100=True) > 100}
                    for key, value in counters.items() if key.endswith(" Limiter")]
        limiters.sort(key=lambda entry: (-entry["percent"], entry["name"]))
        label = listing[0]["value"]
        if len(label) >= 2 and label[0] == label[-1] == '"':
            label = label[1:-1]
        rows.append({"id": row["id"], "label": label,
                     "cost_percent": percent(counters.get("Cost")), "duration": counters.get("Duration"),
                     "limiters": limiters, "native_counters": counters})
    rows.sort(key=lambda row: (-row["cost_percent"], row["id"]))
    return {"measurement_kind": "apple-gpu-replay-bottleneck-report", "device": profile.get("device"),
            "settings": profile.get("settings"), "scope": profile.get("scope"), "timeline": profile["timeline"],
            "encoders": rows, "tma_available": False, "limitations": profile.get("limitations", [])}


def render(report):
    lines = [f"Apple GPU replay — {report['device']}",
             f"GPU time: {report['timeline'].get('GPU time', 'unavailable')} | "
             f"Execution: {(report.get('settings') or {}).get('execution', 'unavailable')}",
             f"Cores: {report['timeline'].get('Cores', 'unavailable')} | "
             f"Profiled state: {report['timeline'].get('Profiled state', 'unavailable')}"]
    for row in report["encoders"]:
        lines.append(f"  {row['label']} | cost {row['cost_percent']:.2f}% | {row['duration']}")
        for limiter in row["limiters"]:
            flag = " (native value exceeds 100%)" if limiter["exceeds_100"] else ""
            lines.append(f"    {limiter['name']:<38} {limiter['percent']:6.2f}%{flag}")
        if not row["limiters"]:
            lines.append("    Native limiter data unavailable")
    lines.extend(["Limiters overlap; they do not sum to 100%.",
                  "Cost and duration describe GPU replay, not application wall time or CPU slot TMA."])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    try:
        report = summarize(json.loads(args.profile.read_text()))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, allow_nan=False) if args.as_json else render(report))


if __name__ == "__main__":
    main()
