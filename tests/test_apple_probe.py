"""Automation outcomes, using synthetic PMU rows rather than invented measurements."""

import json
import subprocess
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from topdown.cli import app
from topdown.collector.apple import AppleProfileError, _run
from topdown.collector.apple_probe import EXPECTED_CHECKSUM, probe

ENVIRONMENT = {"system": "Darwin", "arch": "arm64", "xctrace": "27.0", "templates": ["CPU Counters", "CPU Profiler", "Time Profiler"]}


def fake_capture(output, command, duration, mode):
    output.mkdir()
    (output / "target.stdout").write_text(f"checksum={EXPECTED_CHECKSUM}\n")
    (output / "toc.xml").write_text('<trace-toc><run number="1"><info><target><process pid="42" '
                                    'return-exit-status="0"/></target></info></run></trace-toc>')
    return {"sample_count": 1, "measurement_kind": "native-test", "samples": [
        {"value": 123, "event_values": {"FIXED_CYCLES": 100, "FIXED_INSTRUCTIONS": 150},
         "frames": [{"name": "alu_work"}]}]}


@pytest.mark.parametrize("mode,status", [("counters", "counters_present"), ("fixed", "counters_present"),
                                      ("time", "profile_present"), ("cpu", "profile_present")])
def test_validated_probe_success(tmp_path, mode, status):
    with patch("topdown.collector.apple_probe.doctor", return_value=ENVIRONMENT), \
         patch("topdown.collector.apple_probe._run") as compile_mock, \
         patch("topdown.collector.apple_probe.record_profile", side_effect=fake_capture):
        result = probe(tmp_path / "new", mode)
    assert result["status"] == status
    assert result["exit_code"] == 0
    assert result["target_completed"] is True
    assert result["tma_available"] is False
    assert set(result["tma"].values()) == {None}
    assert json.loads((tmp_path / "new/probe.json").read_text()) == result
    assert compile_mock.call_args.args[0][:3] == ["xcrun", "clang", "-O3"]


@pytest.mark.parametrize("code,exit_code", [("permission_denied", 2), ("counter_data_missing", 3),
                                          ("tool_unavailable", 4), ("timeout", 5), ("invalid_data", 1)])
def test_machine_readable_failures_do_not_retry(tmp_path, code, exit_code):
    with patch("topdown.collector.apple_probe.doctor", return_value=ENVIRONMENT), \
         patch("topdown.collector.apple_probe._run"), \
         patch("topdown.collector.apple_probe.record_profile", side_effect=AppleProfileError("failure", code=code)) as mock:
        result = probe(tmp_path / "new")
    assert result["exit_code"] == exit_code
    assert result["target_completed"] is False
    assert result["sample_count"] == 0
    assert result["tma_available"] is False
    assert mock.call_count == 1


def test_wrong_checksum_does_not_establish_capability(tmp_path):
    def bad(*args, **kwargs):
        result = fake_capture(*args, **kwargs)
        (args[0] / "target.stdout").write_text("checksum=wrong")
        return result
    with patch("topdown.collector.apple_probe.doctor", return_value=ENVIRONMENT), \
         patch("topdown.collector.apple_probe._run"), \
         patch("topdown.collector.apple_probe.record_profile", side_effect=bad):
        result = probe(tmp_path / "new")
    assert result["status"] == "probe_failed"
    assert "checksum" in result["reason"]


def test_zero_pmu_values_remain_unqualified(tmp_path):
    def zero(*args, **kwargs):
        result = fake_capture(*args, **kwargs)
        result["samples"][0]["value"] = 0
        return result
    with patch("topdown.collector.apple_probe.doctor", return_value=ENVIRONMENT), \
         patch("topdown.collector.apple_probe._run"), \
         patch("topdown.collector.apple_probe.record_profile", side_effect=zero):
        result = probe(tmp_path / "new")
    assert result["status"] == "probe_failed"
    assert "no positive values" in result["reason"]


def test_stack_profile_must_observe_probe_work(tmp_path):
    def wrong(*args, **kwargs):
        result = fake_capture(*args, **kwargs)
        result["samples"][0]["frames"][0]["name"] = "unrelated"
        return result
    with patch("topdown.collector.apple_probe.doctor", return_value=ENVIRONMENT), \
         patch("topdown.collector.apple_probe._run"), \
         patch("topdown.collector.apple_probe.record_profile", side_effect=wrong):
        result = probe(tmp_path / "new", "time")
    assert result["status"] == "probe_failed"
    assert "No observed probe work" in result["reason"]


@pytest.mark.parametrize("change", ["zero", "missing", "wrong-work"])
def test_fixed_probe_requires_both_counters_and_observed_probe_work(tmp_path, change):
    def bad(*args, **kwargs):
        profile = fake_capture(*args, **kwargs)
        sample = profile["samples"][0]
        if change == "zero":
            sample["event_values"]["FIXED_CYCLES"] = 0
        elif change == "missing":
            sample["event_values"] = {}
        else:
            sample["frames"] = [{"name": "unrelated"}]
        return profile

    with patch("topdown.collector.apple_probe.doctor", return_value=ENVIRONMENT), \
         patch("topdown.collector.apple_probe._run"), \
         patch("topdown.collector.apple_probe.record_profile", side_effect=bad):
        result = probe(tmp_path / "fixed", "fixed")
    assert result["status"] == "probe_failed"
    assert result["tma_available"] is False


@pytest.mark.parametrize("env,status", [({"error": "requires macOS"}, "unsupported_host"),
                                      ({"xctrace": None}, "tool_unavailable"),
                                      ({"xctrace": "27.0", "templates": []}, "tool_unavailable")])
def test_preflight_failure_never_compiles_or_records(tmp_path, env, status):
    with patch("topdown.collector.apple_probe.doctor", return_value=env), \
         patch("topdown.collector.apple_probe._run") as compile_mock, \
         patch("topdown.collector.apple_probe.record_profile") as record_mock:
        result = probe(tmp_path / "new")
    assert result["status"] == status
    compile_mock.assert_not_called()
    record_mock.assert_not_called()


def test_existing_output_is_not_overwritten(tmp_path):
    sentinel = tmp_path / "probe.json"
    sentinel.write_text("existing evidence")
    with patch("topdown.collector.apple_probe.doctor") as mock:
        result = probe(tmp_path)
    assert result["exit_code"] == 1
    assert sentinel.read_text() == "existing evidence"
    mock.assert_not_called()


def test_interrupt_produces_structured_result(tmp_path):
    with patch("topdown.collector.apple_probe.doctor", return_value=ENVIRONMENT), \
         patch("topdown.collector.apple_probe._run", side_effect=KeyboardInterrupt):
        result = probe(tmp_path / "new")
    assert result["status"] == "interrupted"
    assert result["exit_code"] == 130


def test_output_failure_has_machine_result(tmp_path):
    with patch("topdown.collector.apple_probe.doctor", return_value={"error": "unsupported"}), \
         patch("topdown.collector.apple_probe._write_json", side_effect=PermissionError("read only")):
        result = probe(tmp_path / "new")
    assert result["status"] == "probe_failed"
    assert result["exit_code"] == 1


@pytest.mark.parametrize("stderr,code", [("Operation not permitted", "permission_denied"),
                                       ("Permission denied", "permission_denied"), ("unknown error", "tool_error")])
def test_recorder_errors_have_structured_codes(tmp_path, stderr, code):
    with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 2, "", stderr)):
        with pytest.raises(AppleProfileError) as caught:
            _run(["xcrun"], tmp_path, "record", 5)
    assert caught.value.code == code


def test_probe_cli_stdout_is_json_even_when_capture_unavailable(tmp_path):
    with patch("topdown.collector.apple_probe.doctor", return_value=ENVIRONMENT), \
         patch("topdown.collector.apple_probe._run"), \
         patch("topdown.collector.apple_probe.record_profile", side_effect=AppleProfileError("no counters", code="counter_data_missing")):
        result = CliRunner().invoke(app, ["apple", "probe", "--output", str(tmp_path / "new")])
    assert result.exit_code == 3
    report = json.loads(result.output)
    assert report["status"] == "counter_data_missing"
    assert report["exit_code"] == result.exit_code
