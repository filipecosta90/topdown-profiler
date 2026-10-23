"""Report GPU workload costs and overlapping native limiters from a replay profile."""

import argparse
import json
import math
from pathlib import Path
import re

from topdown.collector.apple_files import read_regular_bytes


def percent(value, allow_above_100=False):
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:[.,]\d+)?%", value):
        raise ValueError(f"Unexpected native percentage: {value!r}")
    number = float(value[:-1].replace(",", "."))
    if not math.isfinite(number) or number < 0 or (number > 100 and not allow_above_100):
        raise ValueError(f"Native percentage outside the expected range: {value!r}")
    return number


def _duration(value):
    return isinstance(value, str) and re.fullmatch(r"\d+(?:[.,]\d+)?\s*(?:ns|µs|μs|us|ms|s)", value) is not None


def summarize(profile, limit=None, offset=0):
    if (not isinstance(profile, dict) or profile.get("measurement_kind") != "apple-gpu-replay-profile"
            or profile.get("tma_available") is not False or not isinstance(profile.get("timeline"), dict)
            or not _duration(profile["timeline"].get("GPU time"))
            or not isinstance(profile.get("encoders"), list) or not profile["encoders"]):
        raise ValueError("Require a native GPU replay profile.json with measured encoders")
    if (type(offset) is not int or offset < 0
            or (limit is not None and (type(limit) is not int or not 1 <= limit <= 1000))):
        raise ValueError("Require offset >= 0 and limit 1–1000")
    settings, scope = profile.get("settings"), profile.get("scope")
    if (not isinstance(profile.get("device"), str) or not profile["device"].strip()
            or not isinstance(settings, dict) or settings.get("execution") not in ("overlapping", "serial")
            or settings.get("gpu_state") not in ("default", "low", "medium", "high") or settings.get("replayed") is not True
            or not isinstance(scope, dict) or scope.get("kind") != "captured-metal-workload"
            or not isinstance(scope.get("trace_name"), str) or not scope["trace_name"].endswith(".gputrace")
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in profile["timeline"].items())):
        raise ValueError("Require native GPU device, replay settings, capture scope and timeline strings")
    rows = []
    identities = set()
    for row in profile["encoders"]:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or not isinstance(row.get("counters"), dict) or not isinstance(row.get("listing"), dict)):
            raise ValueError("Missing native encoder counters")
        counters = row["counters"]
        if (not re.fullmatch(r"[a-z]+\d+", row["id"]) or row["id"] in identities
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in counters.items())
                or not _duration(counters.get("Duration"))):
            raise ValueError("Require unique native encoder identities, string counters and measured durations")
        identities.add(row["id"])
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
            "encoders": rows[offset:offset + limit] if limit is not None else rows[offset:],
            "total_encoders": len(rows), "offset": offset, "limit": limit,
            "tma_available": False, "limitations": profile.get("limitations", [])}


def render(report):
    lines = [f"Apple GPU replay — {report['device']}",
             f"GPU time: {report['timeline'].get('GPU time', 'unavailable')} | "
             f"Execution: {(report.get('settings') or {}).get('execution', 'unavailable')}",
             f"Cores: {report['timeline'].get('Cores', 'unavailable')} | "
             f"Profiled state: {report['timeline'].get('Profiled state', 'unavailable')}"]
    lines.append(f"Encoders: {len(report['encoders'])} of {report['total_encoders']} (offset {report['offset']})")
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
        report = summarize(json.loads(read_regular_bytes(args.profile)))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, allow_nan=False) if args.as_json else render(report))


if __name__ == "__main__":
    main()
