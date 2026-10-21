"""Measured fixed-PMU rows and explicit Instruments package/configuration handling."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest

from topdown.collector.apple import (
    AppleProfileError,
    FIXED_EVENTS,
    FIXED_PACKAGE,
    _build_fixed_instrument,
    _sha256,
    export_profile,
    parse_profile,
    record_profile,
)

FIXTURE = Path(__file__).parent / "test_data/apple_fixed_profile_m6.xml"


def parse_fixed(path=FIXTURE, **kwargs):
    return parse_profile(path, "counters-profile", 3362, fixed_events=FIXED_EVENTS, **kwargs)


def test_measured_m6_fixed_rows_keep_exact_values_stacks_and_scope():
    profile = parse_fixed()
    assert profile["sample_count"] == 3
    assert profile["unmeasured_rows"] == 1
    assert profile["excluded_rows"] == 1
    assert profile["samples"][0]["event_values"] == {"FIXED_CYCLES": 556168, "FIXED_INSTRUCTIONS": 914334}
    assert all(row["pid"] == 3362 and row["frames"][0]["name"] == "alu_work" for row in profile["samples"])
    assert all(row["native_weight_type"] == "weight" and row["weight_type"] == "any" for row in profile["samples"])
    assert all(row["core_label"] == "CPU 0 (E Core)" for row in profile["samples"])
    assert profile["tma_available"] is False
    assert set(profile["tma"].values()) == {None}
    assert sum(row["samples"] for row in profile["leaf_sample_counts"]) == 3


@pytest.mark.parametrize("events", [None, (), tuple(reversed(FIXED_EVENTS)), ("INST_ALL",)])
def test_fixed_vector_cannot_be_named_without_validated_event_order(events):
    with pytest.raises(AppleProfileError, match="validated event order"):
        parse_profile(FIXTURE, "counters-profile", 3362, fixed_events=events)


def test_fixed_half_open_window_does_not_change_scope():
    rows = parse_fixed()["samples"]
    profile = parse_fixed(start_ns=rows[0]["time_ns"], end_ns=rows[1]["time_ns"])
    assert profile["sample_count"] == 1
    assert profile["samples"][0] == rows[0]


@pytest.mark.parametrize("value", [str(2**64), "-1", "NaN", "1.5", ""])
def test_fixed_malformed_and_out_of_range_vectors_fail(tmp_path, value):
    path = tmp_path / "bad.xml"
    path.write_text(FIXTURE.read_text().replace("556168 914334", "556168 " + value))
    with pytest.raises(AppleProfileError, match="fixed-counter integer|uint64 value"):
        parse_fixed(path)


def test_fixed_uint64_precision_is_not_lost_or_aggregated(tmp_path):
    path = tmp_path / "large.xml"
    path.write_text(FIXTURE.read_text().replace("556168 914334", "9007199254740993 18446744073709551615"))
    profile = parse_fixed(path)
    assert profile["samples"][0]["event_values"] == dict(zip(FIXED_EVENTS, (9007199254740993, 2**64-1)))
    assert "totals" not in profile


@pytest.mark.parametrize("old,new", [
    ("<engineering-type>pmc-events</engineering-type>", "<engineering-type>string</engineering-type>"),
    ("<engineering-type>any</engineering-type>", "<engineering-type>cycle-weight</engineering-type>"),
    ("<pmc-events", "<uint64"),
    ("<weight ", "<cycle-weight "),
])
def test_fixed_unknown_native_types_are_rejected(tmp_path, old, new):
    path = tmp_path / "bad.xml"
    text = FIXTURE.read_text().replace(old, new)
    if old == "<pmc-events":
        text = text.replace("</pmc-events>", "</uint64>")
    if old == "<weight ":
        text = text.replace("</weight>", "</cycle-weight>")
    path.write_text(text)
    with pytest.raises(AppleProfileError, match="Unknown fixed-counter"):
        parse_fixed(path)


def test_unmeasured_fixed_rows_are_not_zero_counters(tmp_path):
    root = ET.parse(FIXTURE).getroot()
    table = root.find("node")
    for row in list(table.findall("row")):
        if row.find("pmc-events") is not None:
            table.remove(row)
    path = tmp_path / "unmeasured.xml"
    ET.ElementTree(root).write(path)
    with pytest.raises(AppleProfileError, match="No counters-profile samples"):
        parse_fixed(path)


def fake_fixed_export(cmd, **kwargs):
    output = Path(cmd[cmd.index("--output") + 1])
    if "--toc" in cmd:
        output.write_text('<trace-toc><run number="1"><info><target><process pid="3362" return-exit-status="0"/>'
                          '</target></info><data><table schema="counters-profile" trigger="time" '
                          'pmc-events="&quot;FIXED_CYCLES&quot; &quot;FIXED_INSTRUCTIONS&quot;"/></data></run></trace-toc>')
    else:
        output.write_text(FIXTURE.read_text())
    return subprocess.CompletedProcess(cmd, 0, "", "")


def test_fixed_export_retains_native_event_configuration(tmp_path):
    with patch("subprocess.run", side_effect=fake_fixed_export):
        result = export_profile(tmp_path / "trace", tmp_path / "out", "fixed")
    assert result["measurement_kind"] == "native-fixed-counter-samples"
    assert result["sample_count"] == 3
    assert result["counter_configurations"][0]["pmc-events"] == '"FIXED_CYCLES" "FIXED_INSTRUCTIONS"'


@pytest.mark.parametrize("old,new", [
    ("FIXED_CYCLES&quot; &quot;FIXED_INSTRUCTIONS", "FIXED_INSTRUCTIONS&quot; &quot;FIXED_CYCLES"),
    ('trigger="time"', 'trigger="pmi"'),
    ("FIXED_INSTRUCTIONS", "INST_ALL"),
    ("&quot;FIXED_CYCLES&quot; &quot;FIXED_INSTRUCTIONS&quot;", "&quot;FIXED_CYCLES"),
])
def test_fixed_export_rejects_unverified_event_semantics(tmp_path, old, new):
    def bad_config(cmd, **kwargs):
        result = fake_fixed_export(cmd, **kwargs)
        if "--toc" in cmd:
            path = Path(cmd[cmd.index("--output") + 1])
            path.write_text(path.read_text().replace(old, new))
        return result

    with patch("subprocess.run", side_effect=bad_config):
        with pytest.raises(AppleProfileError, match="fixed mode requires|Invalid configured event"):
            export_profile(tmp_path / "trace", tmp_path / "out", "fixed")


def test_fixed_profile_is_not_a_silent_fallback_for_generic_counters(tmp_path):
    with patch("subprocess.run", side_effect=fake_fixed_export):
        with pytest.raises(AppleProfileError, match="Expected one cpu-pmc-value"):
            export_profile(tmp_path / "trace", tmp_path / "out", "counters")


def test_fixed_package_compiles_locally_and_records_only_explicit_target(tmp_path):
    def fake_tools(cmd, **kwargs):
        if "instrumentbuilder" in cmd and "-o" in cmd:
            package = Path(cmd[cmd.index("-o") + 1]) / "Contents"
            package.mkdir(parents=True)
            (package / "pkginfo.plist").write_text("compiled")
        elif "export" in cmd:
            return fake_fixed_export(cmd, **kwargs)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with patch("topdown.collector.apple.doctor", return_value={}), \
         patch("subprocess.run", side_effect=fake_tools) as run:
        result = record_profile(tmp_path / "capture", [__file__, "literal; $(not-a-shell)"], mode="fixed")
    commands = [call.args[0] for call in run.call_args_list]
    record = next(cmd for cmd in commands if "record" in cmd)
    assert "--package" in record and "--instrument" in record and "--no-prompt" in record
    assert "--template" not in record
    assert record[record.index("--launch"):] == ["--launch", "--", __file__, "literal; $(not-a-shell)"]
    assert all("--all-processes" not in cmd and "sudo" not in cmd for cmd in commands)
    assert result["measurement_kind"] == "native-fixed-counter-samples"
    metadata = json.loads((tmp_path / "capture/capture.json").read_text())
    assert metadata["status"] == "exported"
    assert metadata["fixed_package_sha256"] == _sha256(FIXED_PACKAGE)
    assert (tmp_path / "capture/fixed-counters.instrpkg").read_bytes() == FIXED_PACKAGE.read_bytes()


@pytest.mark.parametrize("code,expected", [("tool_error", "tool_unavailable"), ("permission_denied", "permission_denied"),
                                         ("timeout", "timeout")])
def test_fixed_builder_lookup_keeps_actionable_failure_codes(tmp_path, code, expected):
    with patch("topdown.collector.apple._run", side_effect=AppleProfileError("lookup failed", code=code)):
        with pytest.raises(AppleProfileError) as caught:
            _build_fixed_instrument(tmp_path)
    assert caught.value.code == expected


def test_fixed_builder_success_without_package_is_not_a_successful_capture(tmp_path):
    with patch("topdown.collector.apple.doctor", return_value={}), \
         patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
        with pytest.raises(AppleProfileError, match="did not produce a valid"):
            record_profile(tmp_path / "capture", [__file__], mode="fixed")
    assert all("record" not in call.args[0] for call in run.call_args_list)
    assert json.loads((tmp_path / "capture/capture.json").read_text())["status"] == "failed"
