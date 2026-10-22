"""Measured M6 trace intervals, scope boundaries and explicit decoder gaps."""

import json
import platform
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest
from typer.testing import CliRunner

from topdown.cli import app
from topdown.collector.apple import AppleProfileError, TMA_ROOTS, export_profile, parse_profile, record_profile
from topdown.collector.apple_probe import probe
from topdown.collector.apple_trace import parse_trace_profile

INTERVALS = Path(__file__).parent / "test_data/apple_processor_intervals_m6.xml"
GAPS = Path(__file__).parent / "test_data/apple_processor_gaps_m6.xml"


def parse(intervals=INTERVALS, gaps=GAPS, **kwargs):
    return parse_trace_profile(intervals, gaps, 42, **kwargs)


def write_root(path, root):
    ET.ElementTree(root).write(path)
    return path


def test_measured_instruction_count_matches_disassembled_loop():
    profile = parse()
    entry, loop = profile["intervals"][1:]
    assert entry["self_instructions"] + loop["self_instructions"] == 5 * 100000 + 4
    assert loop["self_cycles"] == 600006
    assert loop["binary_offset"] == 0x46c
    assert loop["frames"][0]["binary"] == "microbench"
    assert profile["interval_count"] == 3
    assert profile["gap_count"] == 1
    assert profile["coverage_complete"] is False
    assert profile["tma_available"] is False
    assert profile["tma"] == dict.fromkeys(TMA_ROOTS)
    assert "samples" not in profile and "totals" not in profile


def test_missing_self_duration_stays_null_and_overlap_is_retained():
    main, entry, loop = parse()["intervals"]
    assert entry["self_duration_ns"] is None
    assert main["start_ns"] <= loop["start_ns"] < main["start_ns"] + main["duration_ns"]


def test_scope_keeps_only_wholly_contained_intervals():
    loop = parse()["intervals"][2]
    result = parse(start_ns=loop["start_ns"], end_ns=loop["start_ns"] + loop["duration_ns"])
    # Native timestamp rounding puts the one-nanosecond entry at the loop's start too.
    assert result["intervals"] == parse()["intervals"][1:]
    assert result["boundary_rows_excluded"] == 1
    assert result["excluded_rows"] == 0
    assert result["gap_count"] == 0
    assert result["target_gap_count_in_run"] == 1


def test_partial_interval_is_never_prorated():
    loop = parse()["intervals"][2]
    with pytest.raises(AppleProfileError, match="No Processor Trace intervals"):
        parse(start_ns=loop["start_ns"] + 10, end_ns=loop["start_ns"] + loop["duration_ns"] - 10)


@pytest.mark.parametrize("change", ["pid", "tid", "thread-pid"])
def test_identity_mismatch_or_absence_fails(tmp_path, change):
    root = ET.parse(INTERVALS).getroot()
    row = root.find("./node/row")
    if change == "pid":
        row.find("./process/pid").text = "0"
    elif change == "tid":
        row.find("./thread/tid").text = "0"
    else:
        row.find("./thread/process/pid").text = "999"
    with pytest.raises(AppleProfileError, match="identity|PID disagree"):
        parse(write_root(tmp_path / "bad.xml", root))


def test_other_pids_and_other_thread_gaps_are_not_associated(tmp_path):
    root = ET.parse(INTERVALS).getroot()
    other = deepcopy(root.find("./node/row"))
    for pid in other.iter("pid"):
        pid.text = "999"
    root.find("node").append(other)
    gaps = ET.parse(GAPS).getroot()
    marker = gaps.find("./node/row/start-time")
    loop = parse()["intervals"][2]
    marker.text = str(loop["start_ns"])
    for tid in gaps.iter("tid"):
        tid.text = "999"
    result = parse(write_root(tmp_path / "rows.xml", root), write_root(tmp_path / "gaps.xml", gaps))
    assert result["excluded_rows"] == 1
    assert result["gap_count"] == 1
    assert result["intervals"][2]["gap_markers_inside"] == 0


def test_gap_markers_have_half_open_per_thread_scope(tmp_path):
    root = ET.parse(GAPS).getroot()
    table = root.find("node")
    row = table.find("row")
    loop = parse()["intervals"][2]
    for time in [loop["start_ns"], loop["start_ns"] + 10, loop["start_ns"] + loop["duration_ns"]]:
        copy = deepcopy(row)
        copy.find("start-time").text = str(time)
        table.append(copy)
    result = parse(gaps=write_root(tmp_path / "gaps.xml", root))
    assert result["gap_count"] == 4
    assert result["intervals"][2]["gap_markers_inside"] == 2
    assert result["coverage_complete"] is False


def test_empty_gap_table_does_not_establish_complete_coverage(tmp_path):
    root = ET.parse(GAPS).getroot()
    root.find("node").remove(root.find("./node/row"))
    result = parse(gaps=write_root(tmp_path / "gaps.xml", root))
    assert result["gap_count"] == 0
    assert result["coverage_complete"] is False


def test_other_pid_gap_is_excluded(tmp_path):
    root = ET.parse(GAPS).getroot()
    for pid in root.iter("pid"):
        pid.text = "999"
    result = parse(gaps=write_root(tmp_path / "gaps.xml", root))
    assert result["gap_count"] == result["target_gap_count_in_run"] == 0


@pytest.mark.parametrize("change", ["gap-description", "process", "location", "backtrace"])
def test_unknown_native_representations_fail(tmp_path, change):
    is_gap = change == "gap-description"
    root = ET.parse(GAPS if is_gap else INTERVALS).getroot()
    row = root.find("./node/row")
    tag = {"gap-description": "string", "process": "process", "location": "text-location",
           "backtrace": "text-backtrace"}[change]
    row.find(tag).tag = "unsupported"
    path = write_root(tmp_path / "bad.xml", root)
    with pytest.raises(AppleProfileError):
        parse(gaps=path) if is_gap else parse(path)


@pytest.mark.parametrize("value", ["-1", "NaN", "1.5", str(2**64), ""])
def test_invalid_native_count_fails(tmp_path, value):
    root = ET.parse(INTERVALS).getroot()
    root.find("./node/row/uint64").text = value
    with pytest.raises(AppleProfileError, match="uint64"):
        parse(write_root(tmp_path / "bad.xml", root))


def test_uint64_count_precision_is_preserved(tmp_path):
    root = ET.parse(INTERVALS).getroot()
    root.find("./node/row/uint64").text = str(2**64 - 1)
    assert parse(write_root(tmp_path / "large.xml", root))["intervals"][0]["self_instructions"] == 2**64 - 1


@pytest.mark.parametrize("change", ["missing-col", "duplicate-col", "unknown-type", "row-width", "unknown-tag",
                                   "self-duration", "overflow", "bad-reference"])
def test_malformed_trace_is_rejected(tmp_path, change):
    root = ET.parse(INTERVALS).getroot()
    schema, row = root.find("./node/schema"), root.find("./node/row")
    if change == "missing-col":
        schema.remove(schema.find("col"))
    elif change == "duplicate-col":
        schema.append(deepcopy(schema.find("col")))
    elif change == "unknown-type":
        schema.find("./col/engineering-type").text = "weight"
    elif change == "row-width":
        row.remove(row[0])
    elif change == "unknown-tag":
        row.find("uint64").tag = "float"
    elif change == "self-duration":
        row.findall("duration")[-1].text = str(2**63)
    elif change == "overflow":
        row.find("start-time").text = str(2**64 - 1)
    elif change == "bad-reference":
        row[0].set("ref", "absent")
    with pytest.raises(AppleProfileError):
        parse(write_root(tmp_path / "bad.xml", root))


@pytest.mark.parametrize("schema", ["cpu-profile", "counters-profile"])
def test_timer_or_fixed_data_cannot_be_relabelled_as_processor_trace(tmp_path, schema):
    root = ET.parse(INTERVALS).getroot()
    root.find("./node/schema").set("name", schema)
    with pytest.raises(AppleProfileError, match="exactly one processor-trace-intervals"):
        parse(write_root(tmp_path / "wrong.xml", root))


def test_parser_requires_explicit_pid_and_gap_table(tmp_path):
    with pytest.raises(AppleProfileError, match="explicit PID"):
        parse_trace_profile(INTERVALS, GAPS, None)
    with pytest.raises(AppleProfileError, match="Cannot read Instruments XML"):
        parse(gaps=tmp_path / "absent")
    with pytest.raises(AppleProfileError, match="requires its gap table"):
        parse_profile(INTERVALS, "processor-trace-intervals", 42)


def fake_export(cmd, **kwargs):
    out = Path(cmd[cmd.index("--output") + 1])
    if "--toc" in cmd:
        out.write_text('<trace-toc><run number="1"><info><target><process pid="42" return-exit-status="0"/>'
                       '</target></info><data><table schema="processor-trace-intervals"/>'
                       '<table schema="processor-trace-gaps"/></data></run></trace-toc>')
    elif "processor-trace-gaps" in cmd[cmd.index("--xpath") + 1]:
        out.write_bytes(GAPS.read_bytes())
    else:
        out.write_bytes(INTERVALS.read_bytes())
    return subprocess.CompletedProcess(cmd, 0, "", "")


def test_export_retains_both_native_tables_hashes_and_exit(tmp_path):
    with patch("subprocess.run", side_effect=fake_export) as run:
        result = export_profile(tmp_path / "recording.trace", tmp_path / "out", "trace")
    assert run.call_count == 3
    assert result["interval_count"] == 3
    assert result["measurement_kind"] == "hardware-traced-execution-intervals"
    assert len(result["raw_xml_sha256"]) == len(result["gaps_xml_sha256"]) == 64
    assert result["target_exit_status"] == "0"
    assert (tmp_path / "out/intervals.xml").exists()
    assert not (tmp_path / "out/samples.xml").exists()


def test_export_refuses_a_missing_gap_table(tmp_path):
    def missing(cmd, **kwargs):
        result = fake_export(cmd, **kwargs)
        if "--toc" in cmd:
            out = Path(cmd[cmd.index("--output") + 1])
            out.write_text(out.read_text().replace('<table schema="processor-trace-gaps"/>', ''))
        return result
    with patch("subprocess.run", side_effect=missing):
        with pytest.raises(AppleProfileError, match="exactly one gap table"):
            export_profile(tmp_path / "recording.trace", tmp_path / "out", "trace")


def test_record_selects_only_explicit_processor_trace_template(tmp_path):
    profile = {"interval_count": 3}
    with patch("topdown.collector.apple.doctor", return_value={}), \
         patch("topdown.collector.apple._run") as run, \
         patch("topdown.collector.apple.export_profile", return_value=profile):
        assert record_profile(tmp_path / "out", [__file__], mode="trace") == profile
    cmd = run.call_args.args[0]
    assert cmd[cmd.index("--template") + 1] == "Processor Trace"
    assert "--no-prompt" in cmd and "--all-processes" not in cmd


@pytest.mark.parametrize("operation", ["record", "export"])
def test_cli_reports_intervals_and_gaps_instead_of_samples(tmp_path, operation):
    profile = parse()
    profile["measurement_kind"] = "hardware-traced-execution-intervals"
    args = ["apple", operation]
    if operation == "export":
        trace = tmp_path / "input.trace"
        trace.mkdir()
        args.append(str(trace))
    args += ["--mode", "trace", "--output", str(tmp_path / "out")]
    if operation == "record":
        args += ["--", "/bin/true"]
    with patch(f"topdown.apple_cli.{'record_profile' if operation == 'record' else 'export_profile'}",
               return_value=profile):
        result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    value = json.loads(result.stdout)
    assert value["interval_count"] == 3 and value["gap_count"] == 1
    assert value["coverage_complete"] is False
    assert "sample_count" not in value


def test_large_default_probe_is_not_silently_used_for_processor_trace(tmp_path):
    result = probe(tmp_path / "out", "trace")
    assert result["status"] == "probe_failed"
    assert "use record --mode trace" in result["reason"]


@pytest.mark.skipif(platform.system() != "Darwin" or platform.machine() != "arm64" or not shutil.which("xcrun"),
                    reason="Native Apple example requires macOS arm64 and Xcode")
def test_bounded_trace_example_checksums_and_limits(tmp_path):
    source = Path(__file__).parent.parent / "examples/apple-trace-probe.c"
    executable = tmp_path / "probe"
    subprocess.run(["xcrun", "clang", "-O2", str(source), "-o", str(executable)], check=True,
                   capture_output=True, timeout=30)
    expected = {"0": "1", "100000": "2700328192655336907", "200000": ""}
    # Use a separate recurrence to check the longer control independently of the compiler.
    x = 1
    for _ in range(200000):
        x ^= (x << 13) & (2**64 - 1)
        x ^= x >> 7
        x ^= (x << 17) & (2**64 - 1)
    expected["200000"] = str(x)
    for steps, checksum in expected.items():
        result = subprocess.run([str(executable), steps], capture_output=True, text=True, timeout=6)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["checksum"] == checksum
        assert data["process_accounted_instructions"] > 0 and data["process_accounted_cycles"] > 0
    for invalid in ["1000001", "bad", "", "-1"]:
        assert subprocess.run([str(executable), invalid], timeout=6).returncode == 2
