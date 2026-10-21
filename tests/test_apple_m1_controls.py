"""Deterministic workload controls for future M1 counter calibration."""

import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "examples/apple-m1-controls.c"
ICACHE_SOURCE = Path(__file__).resolve().parents[1] / "examples/apple-m1-icache.c"
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


def test_icache_control_matches_hot_and_large_instruction_streams(tmp_path):
    if (platform.system() != "Darwin" or platform.machine() != "arm64"
            or not shutil.which("clang") or not shutil.which("otool")):
        pytest.skip("M1 instruction-cache control requires Apple silicon and clang")
    output = tmp_path / "icache"
    hot = tmp_path / "icache-hot"
    medium = tmp_path / "icache-medium"
    custom = tmp_path / "icache-custom"
    subprocess.run(["clang", "-O3", "-g", str(ICACHE_SOURCE), "-o", str(output)], check=True, timeout=30)
    subprocess.run(["clang", "-O3", "-g", "-DTOPDOWN_SMALL_ICACHE", str(ICACHE_SOURCE), "-o", str(hot)],
                   check=True, timeout=30)
    subprocess.run(["clang", "-O3", "-g", "-DTOPDOWN_NOP_BLOCK=32768", str(ICACHE_SOURCE), "-o", str(medium)],
                   check=True, timeout=30)
    subprocess.run(["clang", "-O3", "-g", "-DTOPDOWN_NOP_BLOCK=40960", "-DTOPDOWN_NOP_GROUPS=1",
                    str(ICACHE_SOURCE), "-o", str(custom)], check=True, timeout=30)

    def text_bytes(binary):
        result = subprocess.run(["otool", "-l", str(binary)], capture_output=True, text=True, check=True)
        return int(result.stdout.split("sectname __text", 1)[1].split("size 0x", 1)[1].split()[0], 16)

    assert text_bytes(hot) < 131072 < text_bytes(medium) < text_bytes(custom) < 196608 < text_bytes(output)
    for binary in (output, hot, medium, custom):
        result = subprocess.run([str(binary), "2"], capture_output=True, text=True, timeout=5, check=True)
        assert result.stdout.strip() == str(2 ^ 0x1CACED)
    for args in ((), ("0",), ("-1",), ("invalid",)):
        result = subprocess.run([str(output), *args], capture_output=True, text=True, timeout=5)
        assert result.returncode == 2


@pytest.mark.parametrize("group,expected_events", [
    ("controls", {"RETIRE_UOP", "BRANCH_MISPRED_NONSPEC", "SCHEDULE_UOP",
                  "CORE_ACTIVE_CYCLE", "MAP_DISPATCH_BUBBLE", "MAP_STALL_DISPATCH",
                  "L1D_CACHE_MISS_LD", "L1I_CACHE_MISS_DEMAND"}),
    ("map-state", {"RETIRE_UOP", "MAP_DISPATCH_BUBBLE", "MAP_STALL", "MAP_REWIND",
                   "MAP_STALL_DISPATCH", "MAP_INT_UOP", "MAP_LDST_UOP", "MAP_SIMD_UOP"}),
])
def test_m1_kpep_plan_uses_distinct_hardware_counters(group, expected_events):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        pytest.skip("KPEP requires Apple silicon")
    family = subprocess.check_output(["sysctl", "-n", "hw.cpufamily"], text=True).strip()
    if int(family) != 0x1B588BB3:
        pytest.skip("This event plan is for M1")
    result = subprocess.run([sys.executable, str(PLANNER), "--group", group],
                            capture_output=True, text=True, timeout=10, check=True)
    data = json.loads(result.stdout)
    mapping = data["event_to_counter"]
    assert data["group"] == group
    assert mapping["RETIRE_UOP"] == 7
    assert set(mapping) == {"FIXED_CYCLES", "FIXED_INSTRUCTIONS", *expected_events}
    assert set(mapping.values()) == set(range(10))
    assert data["hardware_access_verified"] is False
