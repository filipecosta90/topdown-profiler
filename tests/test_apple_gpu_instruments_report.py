"""The M1 GPU report keeps sampled values and their measurement limits together."""

from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "apple_gpu" / "report_instruments.py"
SPEC = importlib.util.spec_from_file_location("report_instruments", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture
def profile():
    return {
        "schema": "metal-gpu-counter-intervals", "counter_scope": "device-wide",
        "capture": {"mode": "compute", "probe_source": "packaged", "passes": 2, "items": 65536,
                    "counter_profile": "Performance Limiters", "checksum_verified": True,
                    "target_exit_status": 0},
        "probe_coverage": {"status": "sampled", "passes_observed": 2,
                           "passes_with_counter_samples": 2, "sampled_gpu_work_fraction": 0.25,
                           "sampled_pass_fraction_min": 0.1, "passes_below_10_percent_sampled": 0,
                           "observed_other_activity_fraction_of_sampled_gpu_work": 0.2},
        "sampled_counter_means": {
            "ALU Limiter": {"value": 80, "unit": "%"},
            "Buffer Write Limiter": {"value": 10, "unit": "%"},
            "GPU Read Bandwidth": {"value": 2.5, "unit": "GB/s"},
            "Top Performance Limiter": {"value": 80, "unit": "%"},
        },
        "sampled_counter_means_without_observed_other_activity": {
            "ALU Limiter": {"value": 90, "unit": "%"},
            "GPU Read Bandwidth": {"value": 1.5, "unit": "GB/s"},
        },
        "top_limiter_categories": [
            {"name": "ALU Limiter", "sampled_time_ns": 950,
             "share_of_top_limiter_samples_percent": 95}],
        "top_limiter_unparsed_sampled_time_ns": 0,
    }


def test_m1_report_preserves_scope_coverage_and_clean_counter_means(profile):
    report = MODULE.report(profile)
    assert report["measurement_kind"] == "m1-instruments-device-wide-sampled-gpu"
    assert report["coverage"]["sampled_gpu_work_fraction"] == 0.25
    assert report["coverage"]["observed_other_activity_fraction_of_sampled_gpu_work"] == 0.2
    assert [row["name"] for row in report["ranked_sampled_percent_metrics"]] == [
        "ALU Limiter", "Buffer Write Limiter"]
    assert report["ranked_sampled_percent_metrics"][0]["mean_without_observed_other_activity_percent"] == 90
    assert report["sampled_bandwidth"]["GPU Read Bandwidth"]["sampled_mean_gb_s"] == 2.5
    text = MODULE.format_report(report)
    assert "device-wide Instruments samples" in text
    assert "Sampled passes: 2/2; sampled GPU work: 25.00%; least-sampled pass: 10.00%" in text
    assert "Observed other GPU activity within sampled work: 20.00%" in text
    assert "ALU Limiter: 80.00%; clean 90.00%" in text


def test_utilization_only_report_has_no_empty_limiter_section(profile):
    profile["sampled_counter_means"].pop("ALU Limiter")
    profile["sampled_counter_means"].pop("Buffer Write Limiter")
    profile["sampled_counter_means"]["F32 Utilization"] = {"value": 60, "unit": "%"}
    text = MODULE.format_report(MODULE.report(profile))
    assert "Highest sampled limiter means" not in text
    assert "F32 Utilization: 60.00%" in text


@pytest.mark.parametrize("change,match", [
    ({"counter_scope": "per-encoder"}, "device-wide"),
    ({"capture": {"checksum_verified": False}}, "checksum-verified"),
    ({"probe_coverage": {"passes_with_counter_samples": 3}}, "pass coverage"),
    ({"probe_coverage": {"passes_below_10_percent_sampled": 3}}, "sparse-pass"),
    ({"probe_coverage": {"sampled_gpu_work_fraction": float("nan")}}, "sampled GPU work fraction"),
    ({"sampled_counter_means": {"ALU Limiter": {"value": float("inf"), "unit": "%"}}},
     "metric value"),
    ({"sampled_counter_means_without_observed_other_activity": {
        "GPU Read Bandwidth": {"value": float("nan"), "unit": "GB/s"}}}, "clean M1 GPU bandwidth"),
    ({"top_limiter_categories": [{"name": "ALU Limiter", "sampled_time_ns": 950,
                                  "share_of_top_limiter_samples_percent": 105}]}, "top-limiter category"),
])
def test_m1_report_rejects_invalid_provenance_or_sampled_values(profile, change, match):
    altered = deepcopy(profile)
    for section, updates in change.items():
        if isinstance(updates, dict):
            altered[section].update(updates)
        else:
            altered[section] = updates
    with pytest.raises(ValueError, match=match):
        MODULE.report(altered)
