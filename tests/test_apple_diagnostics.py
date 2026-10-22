"""Recognize the measured M6 kernel without guessing about other kernels or CPUs."""

import json
import subprocess
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from topdown.cli import app
from topdown.collector.apple import M6_BLOCKED_KERNEL_UUID, doctor


def host_doctor(cpu="Apple M6", uuid=M6_BLOCKED_KERNEL_UUID, fail_uuid=False):
    outputs = {
        ("sysctl", "-n", "machdep.cpu.brand_string"): cpu,
        ("sysctl", "-n", "kern.osversion"): "26A434",
        ("sysctl", "-n", "kern.uuid"): uuid,
        ("xcrun", "xctrace", "version"): "xctrace version 27.1 (27A9275)",
        ("xcrun", "xctrace", "list", "templates"): "CPU Counters\nCPU Profiler\nTime Profiler\nProcessor Trace",
        ("/usr/sbin/DevToolsSecurity", "-status"): "Developer mode is currently enabled.",
    }

    def fake(cmd, **kwargs):
        if cmd[-1] == "kern.uuid" and fail_uuid:
            return subprocess.CompletedProcess(cmd, 1, "", "kernel UUID unavailable")
        return subprocess.CompletedProcess(cmd, 0, outputs[tuple(cmd)], "")

    with patch("platform.system", return_value="Darwin"), patch("platform.machine", return_value="arm64"), \
         patch("subprocess.run", side_effect=fake):
        return doctor()


def test_known_kernel_identifies_blocked_modes_and_gives_working_capture_path():
    result = host_doctor(uuid=M6_BLOCKED_KERNEL_UUID.lower())
    diagnostic = result["programmable_counters"]
    assert diagnostic["status"] == "known_kernel_blocker"
    assert diagnostic["affected_modes"] == ["counters", "cpu"]
    assert "EPERM" in diagnostic["reason"] and "Xcode 27.0 and 27.1" in diagnostic["reason"]
    assert "--mode fixed" in diagnostic["next_step"] and "apple report" in diagnostic["next_step"]
    assert result["hardware_access"] == "unverified"
    assert result["os_build"] == "26A434"


@pytest.mark.parametrize("cpu,uuid,fail_uuid", [
    ("Apple M6", "FUTURE-KERNEL", False), ("Apple M1", M6_BLOCKED_KERNEL_UUID, False),
    ("Apple M6", M6_BLOCKED_KERNEL_UUID, True),
])
def test_other_hosts_and_missing_metadata_remain_unverified(cpu, uuid, fail_uuid):
    result = host_doctor(cpu, uuid, fail_uuid)
    assert result["programmable_counters"]["status"] == "unverified"
    assert "probe --mode counters" in result["programmable_counters"]["next_step"]
    if fail_uuid:
        assert "kernel UUID unavailable" in result["errors"]["kernel_uuid"]


def test_doctor_cli_retains_diagnostic_without_disabling_other_modes():
    report = host_doctor()
    with patch("topdown.apple_cli.doctor", return_value=report):
        result = CliRunner().invoke(app, ["apple", "doctor"])
    assert result.exit_code == 0
    assert json.loads(result.output)["programmable_counters"]["status"] == "known_kernel_blocker"


def test_timeout_retains_unknown_access_instead_of_guessing():
    with patch("platform.system", return_value="Darwin"), patch("platform.machine", return_value="arm64"), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired("sysctl", 10)):
        result = doctor()
    assert result["programmable_counters"]["status"] == "unverified"
    assert "kernel_uuid" in result["errors"]
