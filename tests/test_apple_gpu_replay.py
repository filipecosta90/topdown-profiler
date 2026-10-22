"""Native replay percentages remain distinct from additive pipeline-slot shares."""

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "examples" / "apple_gpu"


def script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REPLAY = script("replay_profile")
REPORT = script("report_replay")


def native_profile():
    return {
        "measurement_kind": "apple-gpu-replay-profile", "tma_available": False, "device": "Apple M6",
        "scope": {"kind": "captured-metal-workload", "trace_name": "test.gputrace"},
        "settings": {"execution": "overlapping", "gpu_state": "default", "replayed": True},
        "timeline": {"GPU time": "0.2661 ms"}, "limitations": ["Replay measurements"],
        "encoders": [{"id": "ce0", "listing": {"name": "ce0", "values": [{"type": "string", "value": '"compute pass 0"'}]},
                      "counters": {"Cost": "100.00%", "Duration": "0.2661 ms", "Control Flow Limiter": "64,52%",
                                   "Integer and Conditional Limiter": "37,34%", "Kernel Occupancy": "21,25%",
                                   "Instructions": "18446744073709551615"}}],
    }


def test_report_preserves_native_values_without_normalizing_overlapping_limiters():
    profile = native_profile()
    report = REPORT.summarize(profile)
    row = report["encoders"][0]
    assert row["label"] == "compute pass 0" and row["cost_percent"] == 100
    assert row["limiters"] == [
        {"name": "Control Flow Limiter", "percent": 64.52, "native_value": "64,52%", "exceeds_100": False},
        {"name": "Integer and Conditional Limiter", "percent": 37.34, "native_value": "37,34%", "exceeds_100": False},
    ]
    assert sum(entry["percent"] for entry in row["limiters"]) > 100
    assert row["native_counters"] == profile["encoders"][0]["counters"]
    assert "do not sum to 100%" in REPORT.render(report) and "not application wall time" in REPORT.render(report)


@pytest.mark.parametrize("value", ["NaN%", "inf%", "100.1%", "-1%", "12.5", True, None])
def test_bad_native_percentages_fail_instead_of_fabricating_shares(value):
    with pytest.raises(ValueError):
        REPORT.percent(value)


def test_native_limiters_above_100_are_retained_and_flagged():
    profile = native_profile()
    profile["encoders"][0]["counters"]["Compute Shader Launch Limiter"] = "105,26%"
    result = REPORT.summarize(profile)
    limiter = result["encoders"][0]["limiters"][0]
    assert limiter["percent"] == 105.26 and limiter["exceeds_100"]
    assert "native value exceeds 100%" in REPORT.render(result)


@pytest.mark.parametrize("source", ["", "error: profiling unavailable", "[]", '{"ok":true}\nerror: failed'])
def test_non_json_tool_errors_are_rejected_even_when_tool_exits_zero(source):
    with pytest.raises(ValueError):
        REPLAY.json_documents(source)


def test_multiple_json_responses_and_whitespace():
    assert REPLAY.json_documents(' \n{"a":1}\n{\n"b":2\n}\n') == [{"a": 1}, {"b": 2}]


@pytest.fixture
def fake_gpudebug(monkeypatch):
    state = {"commands": [], "loaded": True, "performance_opened": False, "timeline_opened": False,
             "error": None, "pending": 0}
    profile = native_profile()

    def run(command, **kwargs):
        state["commands"].append(command)
        if "-t" in command:
            response = "Session 42 created.\nProfile: not loaded (0 sessions available)\n"
        elif "--terminate" in command:
            response = "Session 42 terminated."
        else:
            instruction = command[command.index("-c") + 1]
            if instruction == state["error"]:
                response = "error: counter metadata unavailable"
            elif instruction == "go /performance/encoders" and state["pending"]:
                state["pending"] -= 1
                response = "error: 'encoders' has no info. Available actions: go"
            elif instruction.startswith("profile run"):
                response = "Profile data collected. (1s)"
            elif instruction == "profile":
                response = json.dumps({"state": "loaded" if state["loaded"] else "not_loaded"})
            elif instruction == "go /performance":
                state["performance_opened"] = True
                response = '{"children":[],"totalCount":0}'
            elif instruction == "go /performance/timeline/counters":
                state["timeline_opened"] = True
                response = '{"children":[],"totalCount":0}'
            elif instruction == "info /performance/timeline":
                assert state["timeline_opened"]
                response = json.dumps(profile["timeline"])
            elif instruction in {"go /performance/encoders", "list --all"}:
                assert state["performance_opened"]
                response = json.dumps({"children": [profile["encoders"][0]["listing"]], "totalCount": 1})
            elif instruction == "info /performance/encoders/ce0":
                response = json.dumps(profile["encoders"][0]["counters"])
            elif instruction == "status":
                response = '{"device":"Apple M6"}'
            else:
                raise AssertionError(instruction)
        return subprocess.CompletedProcess(command, 0, response, "")
    monkeypatch.setattr(REPLAY.subprocess, "run", run)
    return state


def test_collect_native_replay_preserves_metadata_and_closes_only_its_session(tmp_path, fake_gpudebug):
    trace = tmp_path / "test.gputrace"
    trace.mkdir()
    output = tmp_path / "replay"
    profile = REPLAY.collect(trace, output)
    assert profile["encoders"][0]["counters"] == native_profile()["encoders"][0]["counters"]
    assert json.loads((output / "profile.json").read_text()) == profile
    assert fake_gpudebug["commands"][-1] == ["xcrun", "gpudebug", "--terminate", "42"]
    assert (output / "001.command.json").is_file() and (output / "001.stdout").is_file()
    assert REPORT.summarize(profile)["encoders"][0]["limiters"][0]["percent"] == 64.52


@pytest.mark.parametrize("failure", ["unsupported", "json-error"])
def test_capture_failure_retains_logs_and_terminates_owned_session(tmp_path, fake_gpudebug, failure):
    trace = tmp_path / "test.gputrace"
    trace.mkdir()
    if failure == "unsupported":
        fake_gpudebug["loaded"] = False
    else:
        fake_gpudebug["error"] = "info /performance/timeline"
    output = tmp_path / "failed"
    with pytest.raises(ValueError, match="artifacts"):
        REPLAY.collect(trace, output)
    assert not (output / "profile.json").exists()
    assert fake_gpudebug["commands"][-1][-2:] == ["--terminate", "42"]
    assert list(output.glob("*.stdout"))


def test_async_tables_are_retried_until_ready(tmp_path, fake_gpudebug, monkeypatch):
    trace = tmp_path / "test.gputrace"
    trace.mkdir()
    fake_gpudebug["pending"] = 1
    sleeps = []
    monkeypatch.setattr(REPLAY.time, "sleep", sleeps.append)
    result = REPLAY.collect(trace, tmp_path / "retry")
    assert result["encoders"] and sleeps == [0.25]


def test_performance_readiness_has_a_deadline(tmp_path, fake_gpudebug, monkeypatch):
    trace = tmp_path / "test.gputrace"
    trace.mkdir()
    fake_gpudebug["pending"] = 10
    clock = iter([0, 31])
    monkeypatch.setattr(REPLAY.time, "monotonic", lambda: next(clock))
    with pytest.raises(ValueError, match="did not become ready"):
        REPLAY.collect(trace, tmp_path / "not-ready")
    assert fake_gpudebug["commands"][-1][-2:] == ["--terminate", "42"]


@pytest.mark.parametrize("replacement", [None, {}, {"values": None}, {"values": []}])
def test_missing_encoder_labels_fail_cleanly(replacement):
    profile = native_profile()
    profile["encoders"][0]["listing"] = replacement
    with pytest.raises(ValueError):
        REPORT.summarize(profile)


def test_encoder_labels_keep_their_own_quotes_and_zero_limiters_are_observations():
    profile = native_profile()
    row = profile["encoders"][0]
    row["listing"]["values"][0]["value"] = '""quoted""'
    row["counters"] = {"Cost": "0%", "Duration": "0 ns"}
    result = REPORT.summarize(profile)
    assert result["encoders"][0]["label"] == '"quoted"'
    assert result["encoders"][0]["limiters"] == []
    assert "unavailable" in REPORT.render(result)


def test_new_output_and_existing_trace_are_required(tmp_path, fake_gpudebug):
    with pytest.raises(ValueError, match="existing"):
        REPLAY.collect(tmp_path / "missing.gputrace", tmp_path / "unused")
    trace = tmp_path / "test.gputrace"
    trace.mkdir()
    with pytest.raises(FileExistsError):
        REPLAY.collect(trace, tmp_path)
    assert fake_gpudebug["commands"] == []


def test_report_cli_outputs_readable_and_json_views(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(native_profile()))
    import sys
    for flags in [[], ["--json"]]:
        result = subprocess.run([sys.executable, str(SCRIPTS / "report_replay.py"), str(path), *flags],
                                text=True, capture_output=True, check=True)
        if flags:
            assert json.loads(result.stdout)["encoders"][0]["limiters"][0]["percent"] == 64.52
        else:
            assert "Control Flow Limiter" in result.stdout


EVIDENCE = json.loads((SCRIPTS.parents[1] / "docs/apple-gpu-m6-validation.json").read_text())


@pytest.mark.parametrize("case", EVIDENCE["replay_cases"], ids=lambda case: case["case"])
def test_measured_m6_profiles_keep_native_values_and_sampling_coverage(case):
    profile = {"measurement_kind": "apple-gpu-replay-profile", "tma_available": False,
               "device": "Apple M6", "settings": case["settings"], "timeline": case["timeline"],
               "encoders": [{"id": row["id"], "listing": {"values": [{"value": row["label"]}]},
                             "counters": row["native_counters"]} for row in case["encoders"]]}
    report = REPORT.summarize(profile)
    assert len(report["encoders"]) == case["probe"]["passes"]
    assert report["timeline"]["Cores"] in REPORT.render(report)
    originals = {row["id"]: row["native_counters"] for row in case["encoders"]}
    assert {row["id"]: row["native_counters"] for row in report["encoders"]} == originals
    assert case["probe"]["checksum_verified"]
    if case["case"].startswith("memory-"):
        assert any(entry["exceeds_100"] for row in report["encoders"] for entry in row["limiters"])
