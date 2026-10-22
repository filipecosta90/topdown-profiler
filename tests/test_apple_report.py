"""Observed function frequency must stay distinct from counter sums and CPU time."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from typer.testing import CliRunner

from topdown.apple_report import load_hotspots, summarize_hotspots
from topdown.cli import app
from topdown.collector.apple import AppleProfileError, FIXED_EVENTS, parse_profile


def stack_profile():
    def frame(name, binary="program", address="0x100"):
        return {"name": name, "binary": binary, "address": address}

    stacks = [
        [frame("hot", address="0x101"), frame("hot"), frame("main")],
        [frame("hot", address="0x102"), frame("main")],
        [frame("cold"), frame("main")],
        [],
    ]
    return {
        "schema": "counters-profile", "scope": {"pid": 42, "start_ns": 0, "end_ns": None},
        "sample_count": 4, "unmeasured_rows": 2, "excluded_rows": 3,
        "samples": [{"pid": 42, "time_ns": i, "frames": frames, "weight": 10**i,
                     "event_values": {"FIXED_CYCLES": 2**63 + i, "FIXED_INSTRUCTIONS": 2**64 - 1}}
                    for i, frames in enumerate(stacks)],
    }


def test_function_grouping_recursion_and_missing_stacks_do_not_inflate_shares():
    profile = stack_profile()
    original = deepcopy(profile)
    report = summarize_hotspots(profile)
    assert report["hotspots"] == [
        {"function": "hot", "binary": "program", "leaf_samples": 2, "leaf_share": 0.5, "stack_samples": 2},
        {"function": "cold", "binary": "program", "leaf_samples": 1, "leaf_share": 0.25, "stack_samples": 1},
        {"function": "main", "binary": "program", "leaf_samples": 0, "leaf_share": 0, "stack_samples": 3},
    ]
    assert report["unresolved_stack_samples"] == 1
    assert report["unmeasured_rows"] == 2 and report["excluded_rows"] == 3
    assert report["scope"] == profile["scope"]
    assert report["tma_available"] is False and set(report["tma"].values()) == {None}
    assert profile == original
    assert "FIXED_CYCLES" not in json.dumps(report)


def test_limit_preserves_denominator_and_total_function_count():
    report = summarize_hotspots(stack_profile(), limit=1)
    assert report["hotspot_count"] == 3 and len(report["hotspots"]) == 1
    assert report["hotspots"][0]["leaf_share"] == 0.5


def test_binary_identity_and_unknown_addresses_remain_distinct():
    profile = stack_profile()
    for sample, frame in zip(profile["samples"], [
        {"name": "same", "binary": "one"}, {"name": "same", "binary": "two"},
        {"address": "0x101"}, {},
    ]):
        sample["frames"] = [frame]
    rows = summarize_hotspots(profile)["hotspots"]
    assert {(row["function"], row["binary"]) for row in rows} == {
        ("same", "one"), ("same", "two"), ("0x101", "[unknown binary]"), ("[unresolved]", "[unknown binary]"),
    }
    assert all(row["leaf_share"] == 0.25 for row in rows)


@pytest.mark.parametrize("schema", ["counters-profile", "cpu-profile", "time-profile"])
def test_stack_schemas_use_sample_observations(schema):
    profile = stack_profile()
    profile["schema"] = schema
    assert summarize_hotspots(profile)["hotspots"][0]["leaf_samples"] == 2


@pytest.mark.parametrize("profile", [None, [], {}, {"schema": []}, {"schema": "MetricTable"},
                                    {"schema": "processor-trace-intervals"}, {"schema": "cpu-pmc-value"}])
def test_counter_totals_and_trace_intervals_cannot_be_mislabeled_as_hotspots(profile):
    with pytest.raises(AppleProfileError, match="stack profile"):
        summarize_hotspots(profile)


@pytest.mark.parametrize("field,value,message", [
    ("samples", None, "matching sample_count"), ("samples", [], "matching sample_count"),
    ("sample_count", 1, "matching sample_count"), ("sample_count", True, "matching sample_count"),
    ("scope", None, "valid time scope"), ("scope", {"pid": 0, "start_ns": 0}, "positive PID"),
    ("scope", {"pid": 42, "start_ns": "0"}, "valid time scope"),
    ("scope", {"pid": 42, "start_ns": 0, "end_ns": "1"}, "valid time scope"),
    ("scope", {"pid": 42, "start_ns": 2, "end_ns": 1}, "nonempty"),
    ("unmeasured_rows", -1, "Invalid unmeasured_rows"), ("excluded_rows", True, "Invalid excluded_rows"),
])
def test_bad_profile_metadata_is_actionable(field, value, message):
    profile = stack_profile()
    profile[field] = value
    with pytest.raises(AppleProfileError, match=message):
        summarize_hotspots(profile)


@pytest.mark.parametrize("sample", [None, {"pid": 99, "frames": []}, {"pid": 42, "frames": None},
                                   {"pid": 42, "time_ns": 0, "frames": [None]},
                                   {"pid": 42, "time_ns": 0, "frames": [{"name": 1}]}])
def test_invalid_or_other_pid_samples_cannot_be_included(sample):
    profile = stack_profile()
    profile["samples"][0] = sample
    with pytest.raises(AppleProfileError, match="Invalid stack"):
        summarize_hotspots(profile)


@pytest.mark.parametrize("time_ns", [-1, 4, None, True])
def test_samples_outside_the_reported_window_are_rejected(time_ns):
    profile = stack_profile()
    profile["scope"]["end_ns"] = 4
    profile["samples"][0]["time_ns"] = time_ns
    with pytest.raises(AppleProfileError, match="PID/time scope"):
        summarize_hotspots(profile)


def test_profile_without_any_stacks_cannot_report_hotspots():
    profile = stack_profile()
    for sample in profile["samples"]:
        sample["frames"] = []
    with pytest.raises(AppleProfileError, match="No stack frames"):
        summarize_hotspots(profile)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_limits(limit):
    with pytest.raises(AppleProfileError, match="positive hotspot limit"):
        summarize_hotspots(stack_profile(), limit)


def test_native_fixed_excerpt_produces_real_function_observations(tmp_path):
    fixture = Path(__file__).parent / "test_data/apple_fixed_profile_m6.xml"
    profile = parse_profile(fixture, "counters-profile", 3362, fixed_events=FIXED_EVENTS)
    (tmp_path / "profile.json").write_text(json.dumps(profile))
    report = load_hotspots(tmp_path)
    assert report["sample_count"] == 3 and report["unmeasured_rows"] == 1
    assert report["hotspots"] == [
        {"function": "alu_work", "binary": "probe-target", "leaf_samples": 3, "leaf_share": 1, "stack_samples": 3},
    ]


@pytest.mark.parametrize("content", [None, "not json", "\"not an exported profile\""])
def test_missing_or_unreadable_profile_fails_cleanly(tmp_path, content):
    path = tmp_path / "profile.json"
    if content is not None:
        path.write_text(content)
    with pytest.raises(AppleProfileError):
        load_hotspots(tmp_path)


def test_cli_human_report_preserves_literal_symbols_and_labels_sample_shares(tmp_path):
    profile = stack_profile()
    profile["samples"][0]["frames"][0]["name"] = "[red]hot"
    (tmp_path / "profile.json").write_text(json.dumps(profile))
    result = CliRunner().invoke(app, ["apple", "report", str(tmp_path), "--limit", "1"])
    assert result.exit_code == 0, result.output
    assert "Leaf share" in result.output and "Missing stacks: 1" in result.output
    assert "Showing 1 of" in result.output and "not summed" in result.output
    assert "CPU-time percentages and TMA shares are unavailable" in " ".join(result.output.split())
    result = CliRunner().invoke(app, ["apple", "report", str(tmp_path)])
    assert "[red]hot" in result.output


def test_cli_json_report_and_input_error(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(stack_profile()))
    result = CliRunner().invoke(app, ["apple", "report", str(path), "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["hotspots"][0]["leaf_share"] == 0.5
    path.write_text("{}")
    result = CliRunner().invoke(app, ["apple", "report", str(path)])
    assert result.exit_code == 1 and "stack profile" in result.output


def test_human_report_displays_bounded_scope(tmp_path):
    profile = stack_profile()
    profile["scope"]["end_ns"] = 100
    (tmp_path / "profile.json").write_text(json.dumps(profile))
    result = CliRunner().invoke(app, ["apple", "report", str(tmp_path)])
    assert result.exit_code == 0 and "0 ns to 100 ns" in result.output
