"""The M1 Instruments runner verifies the probe and selected native counter set."""

from pathlib import Path
from runpy import run_path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "examples/apple_gpu/record_instruments.py"


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    return run_path(str(SCRIPT))


def test_runner_requires_completed_m1_probe_and_requested_counter_profile(tmp_path, runner):
    toc = tmp_path / "toc.xml"
    stdout = tmp_path / "target.stdout"
    xml = ('<trace-toc><run number="1"><info><target><process return-exit-status="0"/>'
           '</target><value>Counter Set: Performance Limiters</value></info>'
           '<data><table schema="metal-gpu-counter-profile" counter-profile="3"/>'
           '<table schema="metal-gpu-counter-intervals"/><table schema="metal-gpu-intervals"/>'
           '</data></run></trace-toc>')
    toc.write_text(xml)
    text = "device=Apple M1\nworkload=alu passes=4 items=16384 gpu_seconds=0.001 checksum=1\nchecksum_verified=true\n"
    stdout.write_text(text)
    runner["_verify"](toc, stdout, 3, "alu", 4)
    toc.write_text(xml.replace('counter-profile="3"', 'counter-profile="4"'))
    with pytest.raises(ValueError, match="requested GPU counter profile"):
        runner["_verify"](toc, stdout, 3, "alu", 4)
    toc.write_text(xml.replace("Counter Set: Performance Limiters", "Counter Set: Performance Utilizations"))
    with pytest.raises(ValueError, match="requested GPU counter set"):
        runner["_verify"](toc, stdout, 3, "alu", 4)
    toc.write_text(xml.replace('return-exit-status="0"', 'return-exit-status="1"'))
    with pytest.raises(ValueError, match="did not exit successfully"):
        runner["_verify"](toc, stdout, 3, "alu", 4)
    toc.write_text(xml)
    stdout.write_text(text.replace("checksum_verified=true", "checksum_verified=false"))
    with pytest.raises(ValueError, match="checksum"):
        runner["_verify"](toc, stdout, 3, "alu", 4)
    stdout.write_text(text)
    toc.write_text(xml.replace('<table schema="metal-gpu-intervals"/>', ""))
    with pytest.raises(ValueError, match="metal-gpu-intervals"):
        runner["_verify"](toc, stdout, 3, "alu", 4)
    toc.write_text("<broken")
    with pytest.raises(ValueError, match="Invalid Instruments"):
        runner["_verify"](toc, stdout, 3, "alu", 4)


@pytest.mark.parametrize("options", [
    {"mode": "idle"}, {"passes": 0}, {"passes": 501}, {"profile": 5},
    {"tail": 0}, {"tail": float("nan")}, {"time_limit": 0},
])
def test_runner_rejects_invalid_options_before_creating_artifacts(tmp_path, runner, options):
    output = tmp_path / "capture"
    with pytest.raises(ValueError, match="Choose alu/stream"):
        runner["record"](output, **options)
    assert not output.exists()


def test_runner_requires_full_xcode_template_before_creating_artifacts(tmp_path, runner, monkeypatch):
    monkeypatch.setenv("DEVELOPER_DIR", str(tmp_path / "CommandLineTools/Developer"))
    output = tmp_path / "capture"
    with pytest.raises(ValueError, match="Full Xcode Metal System Trace template"):
        runner["record"](output)
    assert not output.exists()
