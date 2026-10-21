"""Time-only scheduling correlation without inventing counter attribution."""

import json
import subprocess
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest

from topdown.collector.apple import AppleProfileError, export_profile
from topdown.collector.apple_schedule import annotate_scheduling
from topdown.collector.apple_trace import parse_trace_profile

DATA = Path(__file__).parent / "test_data"
STATES = DATA / "apple_thread_states_m6.xml"
INTERVALS = DATA / "apple_processor_intervals_m6.xml"
GAPS = DATA / "apple_processor_gaps_m6.xml"


def profile():
    return parse_trace_profile(INTERVALS, GAPS, 42)


def save(root, path):
    ET.ElementTree(root).write(path)
    return path


def write_states(path, entries):
    root = ET.parse(STATES).getroot()
    table = root.find("node")
    template = next(row for row in table.findall("row") if row.find("thread-state").text == "Running")
    for row in list(table.findall("row")):
        table.remove(row)
    for start, duration, state, core, tid in entries:
        row = deepcopy(template)
        row.find("start-time").text = str(start)
        row.find("duration").text = str(duration)
        row.find("thread-state").text = state
        row.find("./thread/tid").text = str(tid)
        cell = row.find("core")
        if core is None:
            cell.tag = "sentinel"
            cell.text = None
            cell.attrib.clear()
        else:
            cell.text = str(core)
            cell.set("fmt", f"CPU {core}")
        table.append(row)
    return save(root, path)


def single_interval(start=100, duration=100, tid=420):
    p = profile()
    p["intervals"] = [{"start_ns": start, "duration_ns": duration, "tid": tid,
                       "self_instructions": 2**53 + 1, "self_cycles": 1000}]
    return p


def test_native_loop_observed_on_super_core_without_changing_counts():
    p = profile()
    counters = [(r["self_instructions"], r["self_cycles"]) for r in p["intervals"]]
    annotate_scheduling(p, STATES)
    loop = p["intervals"][2]
    assert loop["scheduling"]["single_running_core"] == {"core": 6, "core_label": "CPU 6 (S Core)"}
    assert loop["scheduling"]["states_ns"] == {"Running": loop["duration_ns"]}
    assert p["coverage_complete"] is False and p["tma_available"] is False
    assert [(r["self_instructions"], r["self_cycles"]) for r in p["intervals"]] == counters
    assert p["scheduling_available"] is True and len(p["thread_state_xml_sha256"]) == 64


def test_blocked_and_running_spans_are_clipped_without_prorating_counters(tmp_path):
    p = single_interval()
    path = write_states(tmp_path / "states.xml", [(0, 125, "Blocked", None, 420), (125, 100, "Running", 0, 420)])
    annotate_scheduling(p, path)
    result = p["intervals"][0]
    assert result["scheduling"]["states_ns"] == {"Blocked": 25, "Running": 75}
    assert result["scheduling"]["coverage_complete"] is True
    assert result["scheduling"]["single_running_core"] is None
    assert result["self_instructions"] == 2**53 + 1
    assert "cycles_by_state" not in result


def test_core_migration_does_not_assign_a_single_core(tmp_path):
    p = single_interval()
    path = write_states(tmp_path / "states.xml", [(100, 50, "Running", 6, 420), (150, 50, "Running", 0, 420)])
    annotate_scheduling(p, path)
    result = p["intervals"][0]["scheduling"]
    assert result["coverage_complete"] is True and result["single_running_core"] is None
    assert [c["core"] for c in result["running_cores"]] == [0, 6]


def test_timeline_gaps_remain_uncovered_and_other_threads_are_separate(tmp_path):
    p = single_interval()
    path = write_states(tmp_path / "states.xml", [(100, 25, "Running", 6, 420), (150, 50, "Running", 6, 420),
                                                 (125, 25, "Running", 6, 999)])
    annotate_scheduling(p, path)
    result = p["intervals"][0]["scheduling"]
    assert result["uncovered_ns"] == 25 and result["covered_ns"] == 75
    assert result["coverage_complete"] is False and result["single_running_core"] is None


def test_zero_duration_and_unobserved_threads_have_no_core(tmp_path):
    p = single_interval(duration=0)
    path = write_states(tmp_path / "states.xml", [(100, 100, "Running", 6, 420), (300, 0, "Terminated", None, 420)])
    annotate_scheduling(p, path)
    assert p["intervals"][0]["scheduling"]["coverage_complete"] is False
    p = single_interval(tid=999)
    annotate_scheduling(p, path)
    assert p["intervals"][0]["scheduling"]["uncovered_ns"] == 100


def test_running_without_core_is_retained_as_missing_context(tmp_path):
    p = single_interval()
    annotate_scheduling(p, write_states(tmp_path / "states.xml", [(100, 100, "Running", None, 420)]))
    result = p["intervals"][0]["scheduling"]
    assert result["states_ns"] == {"Running": 100}
    assert result["running_cores"] == [] and result["single_running_core"] is None


def test_native_unknown_state_is_not_relabelled_as_running(tmp_path):
    p = single_interval()
    annotate_scheduling(p, write_states(tmp_path / "states.xml", [(100, 100, "NewNativeState", 6, 420)]))
    result = p["intervals"][0]["scheduling"]
    assert result["states_ns"] == {"NewNativeState": 100} and result["single_running_core"] is None


def test_half_open_boundary_and_unsorted_contiguous_running_spans(tmp_path):
    p = single_interval()
    path = write_states(tmp_path / "states.xml", [(150, 50, "Running", 6, 420), (50, 50, "Blocked", None, 420),
                                                 (100, 50, "Running", 6, 420), (200, 25, "Interrupted", 6, 420)])
    annotate_scheduling(p, path)
    assert p["intervals"][0]["scheduling"]["states_ns"] == {"Running": 100}
    assert p["intervals"][0]["scheduling"]["single_running_core"]["core"] == 6


def test_overlapping_observations_cannot_double_count_time(tmp_path):
    path = write_states(tmp_path / "states.xml", [(100, 100, "Running", 6, 420), (150, 100, "Blocked", None, 420)])
    with pytest.raises(AppleProfileError, match="Overlapping"):
        annotate_scheduling(single_interval(), path)


@pytest.mark.parametrize("change", ["process", "state", "empty-state", "overflow", "core", "thread-pid"])
def test_malformed_native_state_cannot_be_used_for_attribution(tmp_path, change):
    root = ET.parse(STATES).getroot()
    row = root.find("./node/row")
    if change == "process":
        row.find("process").tag = "sentinel"
    elif change == "state":
        row.find("thread-state").tag = "string"
    elif change == "empty-state":
        row.find("thread-state").text = ""
    elif change == "overflow":
        row.find("duration").text = str(2**64 - 1)
    elif change == "core":
        core = row.find("core")
        if core is None:
            core = row.find("sentinel")
        core.tag = "string"
    else:
        row.find("./thread/process/pid").text = "999"
    with pytest.raises(AppleProfileError):
        annotate_scheduling(profile(), save(root, tmp_path / "bad.xml"))


def test_other_processes_including_kernel_are_excluded(tmp_path):
    root = ET.parse(STATES).getroot()
    for pid in root.iter("pid"):
        pid.text = "0"
    p = profile()
    annotate_scheduling(p, save(root, tmp_path / "kernel.xml"))
    assert p["thread_state_rows"] == 0 and p["thread_state_other_pid_rows"] == 3
    assert all(r["scheduling"]["single_running_core"] is None for r in p["intervals"])


@pytest.mark.parametrize("tables", [1, 2])
def test_trace_export_requires_unambiguous_scheduler_data_and_keeps_hashes(tmp_path, tables):
    def export(cmd, **kwargs):
        path = Path(cmd[cmd.index("--output") + 1])
        if "--toc" in cmd:
            path.write_text('<trace-toc><run number="1"><info><target><process pid="42" return-exit-status="0"/>'
                            '</target></info><data><table schema="processor-trace-intervals"/>'
                            '<table schema="processor-trace-gaps"/>' + '<table schema="thread-state"/>' * tables
                            + '</data></run></trace-toc>')
        else:
            xpath = cmd[cmd.index("--xpath") + 1]
            fixture = STATES if 'schema="thread-state"' in xpath else GAPS if 'schema="processor-trace-gaps"' in xpath else INTERVALS
            path.write_bytes(fixture.read_bytes())
        return subprocess.CompletedProcess(cmd, 0, "", "")
    with patch("subprocess.run", side_effect=export) as run:
        if tables == 2:
            with pytest.raises(AppleProfileError, match="Ambiguous"):
                export_profile(tmp_path / "input.trace", tmp_path / "out", "trace")
        else:
            result = export_profile(tmp_path / "input.trace", tmp_path / "out", "trace")
            assert run.call_count == 4
            assert result["scheduling_available"] is True
            assert json.loads((tmp_path / "out/profile.json").read_text())["single_running_core_intervals"] > 0
