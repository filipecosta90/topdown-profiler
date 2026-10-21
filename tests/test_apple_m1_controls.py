"""Deterministic workload controls for future M1 counter calibration."""

import platform
import shutil
import subprocess
import json
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "examples/apple-m1-controls.c"
PLANNER = Path(__file__).resolve().parents[1] / "examples/apple-m1-kpep-plan.py"


@pytest.fixture(scope="module")
def controls(tmp_path_factory):
    if platform.system() != "Darwin" or platform.machine() != "arm64" or not shutil.which("clang"):
        pytest.skip("M1 workload controls require Apple silicon and clang")
    output = tmp_path_factory.mktemp("apple-m1-controls") / "controls"
    subprocess.run(["clang", "-O3", str(SOURCE), "-o", str(output)], check=True, timeout=30)
    return output


@pytest.mark.parametrize("args,expected", [
    (("serial", "100"), "1838323455979401253"),
    (("parallel", "100"), "15343226476468928520"),
    (("alu_wide", "100"), "3636"),
    (("branch_predictable", "100"), "18127535559106838743"),
    (("branch_random", "100"), "18127535559106838807"),
    (("memory", "100", "1024"), "985"),
])
def test_controls_complete_with_deterministic_checksums(controls, args, expected):
    result = subprocess.run([str(controls), *args], capture_output=True, text=True, timeout=5, check=True)
    assert result.stdout.strip() == expected
    assert result.stderr == ""


@pytest.mark.parametrize("args", [(), ("unknown", "100"), ("memory", "100", "1")])
def test_invalid_control_arguments_exit_without_running(controls, args):
    result = subprocess.run([str(controls), *args], capture_output=True, text=True, timeout=5)
    assert result.returncode == 2


def test_m1_kpep_plan_uses_distinct_hardware_counters():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        pytest.skip("KPEP requires Apple silicon")
    family = subprocess.check_output(["sysctl", "-n", "hw.cpufamily"], text=True).strip()
    if int(family) != 0x1B588BB3:
        pytest.skip("This event plan is for M1")
    result = subprocess.run(["python3", str(PLANNER)], capture_output=True, text=True, timeout=10, check=True)
    data = json.loads(result.stdout)
    mapping = data["event_to_counter"]
    assert mapping["RETIRE_UOP"] == 7
    assert set(mapping.values()) == set(range(10))
    assert data["hardware_access_verified"] is False
