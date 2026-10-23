"""Report sampled M1 Instruments GPU counters with their capture coverage."""

import argparse
import json
import math
from pathlib import Path


def _fraction(value, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"Invalid {label} in M1 GPU profile")
    return value


def _sampled_time(metric: dict, name: str) -> int:
    duration = metric.get("sampled_time_ns")
    if type(duration) is not int or duration <= 0:
        raise ValueError(f"Invalid sampled time for {name}")
    return duration


def report(profile: dict) -> dict:
    if (not isinstance(profile, dict) or profile.get("schema") != "metal-gpu-counter-intervals"
            or profile.get("counter_scope") != "device-wide"):
        raise ValueError("Expected an M1 Instruments device-wide GPU counter profile")
    capture = profile.get("capture")
    coverage = profile.get("probe_coverage")
    means = profile.get("sampled_counter_means")
    clean = profile.get("sampled_counter_means_without_observed_other_activity")
    if (not isinstance(capture, dict) or not isinstance(coverage, dict) or not isinstance(means, dict)
            or not means or not isinstance(clean, dict) or capture.get("checksum_verified") is not True
            or capture.get("target_exit_status") != 0 or coverage.get("status") != "sampled"):
        raise ValueError("M1 GPU profile lacks a completed, sampled, checksum-verified capture")
    passes = capture.get("passes")
    observed = coverage.get("passes_observed")
    sampled = coverage.get("passes_with_counter_samples")
    if (type(passes) is not int or passes <= 0 or type(observed) is not int
            or type(sampled) is not int or not 0 <= sampled <= observed <= passes):
        raise ValueError("Invalid M1 GPU pass coverage")
    sparse = coverage.get("passes_below_10_percent_sampled")
    if type(sparse) is not int or not 0 <= sparse <= observed:
        raise ValueError("Invalid M1 GPU sparse-pass count")
    sampled_fraction = _fraction(coverage.get("sampled_gpu_work_fraction"), "sampled GPU work fraction")
    minimum = _fraction(coverage.get("sampled_pass_fraction_min"), "minimum pass sample fraction")
    other = coverage.get("observed_other_activity_fraction_of_sampled_gpu_work")
    if other is not None:
        other = _fraction(other, "observed other GPU activity fraction")

    ranked = []
    for name, metric in means.items():
        if not name.endswith((" Limiter", " Utilization")) or name == "Top Performance Limiter":
            continue
        if not isinstance(metric, dict) or metric.get("unit") != "%":
            raise ValueError(f"Invalid M1 GPU percentage metric {name}")
        value = metric.get("value")
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid M1 GPU metric value for {name}")
        sampled_time = _sampled_time(metric, name)
        clean_metric = clean.get(name)
        if clean_metric is not None and (not isinstance(clean_metric, dict)
                                         or clean_metric.get("unit") != "%"):
            raise ValueError(f"Invalid clean M1 GPU percentage metric {name}")
        clean_value = clean_metric.get("value") if isinstance(clean_metric, dict) else None
        if clean_value is not None and (type(clean_value) not in (int, float)
                                        or not math.isfinite(clean_value) or clean_value < 0):
            raise ValueError(f"Invalid clean M1 GPU metric value for {name}")
        clean_time = _sampled_time(clean_metric, name) if clean_metric is not None else 0
        if clean_time > sampled_time:
            raise ValueError(f"Clean M1 GPU sampled time exceeds total for {name}")
        ranked.append({"name": name, "sampled_mean_percent": value,
                       "sampled_time_ns": sampled_time,
                       "mean_without_observed_other_activity_percent": clean_value,
                       "clean_sampled_time_fraction": clean_time / sampled_time})
    ranked.sort(key=lambda row: (-row["sampled_mean_percent"], row["name"]))

    bandwidth = {}
    for name in ("GPU Read Bandwidth", "GPU Write Bandwidth"):
        metric = means.get(name)
        if metric is None:
            continue
        if (not isinstance(metric, dict) or metric.get("unit") != "GB/s"
                or type(metric.get("value")) not in (int, float)
                or not math.isfinite(metric["value"]) or metric["value"] < 0):
            raise ValueError(f"Invalid M1 GPU bandwidth metric {name}")
        sampled_time = _sampled_time(metric, name)
        clean_metric = clean.get(name)
        if clean_metric is not None and (not isinstance(clean_metric, dict)
                                         or clean_metric.get("unit") != "GB/s"):
            raise ValueError(f"Invalid clean M1 GPU bandwidth metric {name}")
        clean_value = clean_metric.get("value") if isinstance(clean_metric, dict) else None
        if clean_value is not None and (type(clean_value) not in (int, float)
                                        or not math.isfinite(clean_value) or clean_value < 0):
            raise ValueError(f"Invalid clean M1 GPU bandwidth metric {name}")
        clean_time = _sampled_time(clean_metric, name) if clean_metric is not None else 0
        if clean_time > sampled_time:
            raise ValueError(f"Clean M1 GPU sampled time exceeds total for {name}")
        bandwidth[name] = {"sampled_mean_gb_s": metric["value"],
                           "sampled_time_ns": sampled_time,
                           "mean_without_observed_other_activity_gb_s": clean_value,
                           "clean_sampled_time_fraction": clean_time / sampled_time}

    categories = profile.get("top_limiter_categories", [])
    unparsed = profile.get("top_limiter_unparsed_sampled_time_ns")
    if (not isinstance(categories, list) or type(unparsed) is not int or unparsed < 0):
        raise ValueError("Invalid M1 GPU top-limiter coverage")
    for category in categories:
        if (not isinstance(category, dict) or not isinstance(category.get("name"), str)
                or not category["name"] or type(category.get("sampled_time_ns")) is not int
                or category["sampled_time_ns"] < 0
                or type(category.get("share_of_top_limiter_samples_percent")) not in (int, float)
                or not math.isfinite(category["share_of_top_limiter_samples_percent"])
                or not 0 <= category["share_of_top_limiter_samples_percent"] <= 100):
            raise ValueError("Invalid M1 GPU top-limiter category")

    return {
        "measurement_kind": "m1-instruments-device-wide-sampled-gpu",
        "capture": {key: capture.get(key) for key in
                    ("mode", "probe_source", "passes", "items", "counter_profile")},
        "coverage": {"passes_observed": observed, "passes_with_counter_samples": sampled,
                     "sampled_gpu_work_fraction": sampled_fraction,
                     "sampled_pass_fraction_min": minimum,
                     "passes_below_10_percent_sampled": sparse,
                     "observed_other_activity_fraction_of_sampled_gpu_work": other},
        "ranked_sampled_percent_metrics": ranked,
        "sampled_bandwidth": bandwidth,
        "top_limiter_categories": categories,
        "top_limiter_unparsed_sampled_time_ns": unparsed,
    }


def format_report(data: dict) -> str:
    capture, coverage = data["capture"], data["coverage"]
    lines = [f"M1 sampled GPU: {capture['probe_source']} {capture['mode']}, "
             f"{capture['passes']} passes, {capture['counter_profile']}",
             "Counter scope: device-wide Instruments samples",
             f"Sampled passes: {coverage['passes_with_counter_samples']}/{capture['passes']}; "
             f"sampled GPU work: {coverage['sampled_gpu_work_fraction']:.2%}; "
             f"least-sampled pass: {coverage['sampled_pass_fraction_min']:.2%}"]
    other = coverage["observed_other_activity_fraction_of_sampled_gpu_work"]
    if other is not None:
        lines.append(f"Observed other GPU activity within sampled work: {other:.2%}")
    if coverage["passes_below_10_percent_sampled"]:
        lines.append(f"Passes below 10% sampled: {coverage['passes_below_10_percent_sampled']}")
    for suffix, heading in ((" Limiter", "Highest sampled limiter means"),
                            (" Utilization", "Highest sampled utilization means")):
        metrics = [row for row in data["ranked_sampled_percent_metrics"] if row["name"].endswith(suffix)]
        if not metrics:
            continue
        lines.append(f"{heading} (clean excludes counter intervals with observed other activity):")
        for metric in metrics[:5]:
            clean = metric["mean_without_observed_other_activity_percent"]
            fraction = metric["clean_sampled_time_fraction"]
            clean_text = f"; clean {clean:.2f}% ({fraction:.1%} of sampled time)" if clean is not None else ""
            lines.append(f"  {metric['name']}: {metric['sampled_mean_percent']:.2f}%{clean_text}")
    for name, metric in data["sampled_bandwidth"].items():
        clean = metric["mean_without_observed_other_activity_gb_s"]
        fraction = metric["clean_sampled_time_fraction"]
        clean_text = f"; clean {clean:.2f} GB/s ({fraction:.1%} of sampled time)" if clean is not None else ""
        lines.append(f"  {name}: {metric['sampled_mean_gb_s']:.2f} GB/s{clean_text}")
    if data["top_limiter_categories"]:
        winner = data["top_limiter_categories"][0]
        lines.append(f"Most frequent parsed top-limiter label: {winner['name']} "
                     f"({winner['share_of_top_limiter_samples_percent']:.2f}% of parsed label sample time)")
    if data["top_limiter_unparsed_sampled_time_ns"]:
        lines.append(f"Unparsed top-limiter sample time: {data['top_limiter_unparsed_sampled_time_ns']} ns")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path, help="checked M1 runner profile.json")
    parser.add_argument("--json", action="store_true", help="print the compact report as JSON")
    args = parser.parse_args()
    try:
        data = report(json.loads(args.profile.read_text()))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(json.dumps(data, indent=2, allow_nan=False) if args.json else format_report(data))


if __name__ == "__main__":
    main()
