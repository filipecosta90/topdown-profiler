import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "apple_gpu" / "summarize_counters.py"
SPEC = importlib.util.spec_from_file_location("summarize_counters", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_summary_resolves_xctrace_references():
    source = b"""<trace-query-result><node><schema name="metal-gpu-counter-intervals"/>
    <row><start-time id="1">100</start-time><duration>20</duration><uint32>0</uint32>
    <gpu-counter-name id="2">ALU Limiter</gpu-counter-name></row>
    <row><start-time ref="1"/><duration>20</duration><uint32>1</uint32>
    <gpu-counter-name ref="2"/></row>
    </node></trace-query-result>"""
    assert MODULE.summarize(source) == {
        "schema": "metal-gpu-counter-intervals",
        "counter_rows": 2,
        "sample_intervals": 1,
        "counter_names": {"ALU Limiter": 2},
    }


def test_summary_rejects_wrong_schema():
    with pytest.raises(ValueError, match="expected"):
        MODULE.summarize(b'<trace-query-result><schema name="gpu-intervals"/></trace-query-result>')


def test_summary_checks_probe_counter_overlap():
    counters = b"""<trace-query-result><node><schema name="metal-gpu-counter-intervals"/>
    <row><start-time id="1">105</start-time><duration id="2">10</duration><uint32>0</uint32>
    <gpu-counter-name id="3">ALU Limiter</gpu-counter-name></row>
    <row><start-time>145</start-time><duration ref="2"/><uint32>0</uint32>
    <gpu-counter-name ref="3"/></row></node></trace-query-result>"""
    gpu = b"""<trace-query-result><node><schema name="metal-gpu-intervals">
    <col><mnemonic>start</mnemonic></col><col><mnemonic>duration</mnemonic></col>
    <col><mnemonic>event-label</mnemonic></col></schema>
    <row><start-time>100</start-time><duration>20</duration>
    <formatted-label fmt="topdown GPU probe pass 0:burn compute pass 0 (probe)"/></row>
    <row><start-time>200</start-time><duration>20</duration>
    <formatted-label fmt="topdown GPU probe pass 1:burn compute pass 1 (probe)"/></row>
    </node></trace-query-result>"""
    result = MODULE.summarize(counters, gpu)["probe_coverage"]
    assert result == {
        "passes_observed": 2,
        "passes_with_counter_samples": 1,
        "gpu_work_time_ns": 40,
        "sampled_gpu_work_time_ns": 10,
        "counter_sample_intervals_overlapping": 1,
        "last_counter_end_minus_last_probe_end_ns": -65,
    }


def test_summary_reports_counter_rows_without_probe_coverage():
    counters = b"""<trace-query-result><node><schema name="metal-gpu-counter-intervals"/>
    <row><start-time>10</start-time><duration>2</duration><uint32>0</uint32>
    <gpu-counter-name>ALU Limiter</gpu-counter-name></row></node></trace-query-result>"""
    gpu = b"""<trace-query-result><node><schema name="metal-gpu-intervals">
    <col><mnemonic>start</mnemonic></col><col><mnemonic>duration</mnemonic></col>
    <col><mnemonic>event-label</mnemonic></col></schema>
    <row><start-time>100</start-time><duration>20</duration>
    <formatted-label fmt="topdown GPU probe pass 0:burn compute pass 0"/></row>
    </node></trace-query-result>"""
    result = MODULE.summarize(counters, gpu)["probe_coverage"]
    assert result["passes_observed"] == 1
    assert result["passes_with_counter_samples"] == 0
    assert result["sampled_gpu_work_time_ns"] == 0
