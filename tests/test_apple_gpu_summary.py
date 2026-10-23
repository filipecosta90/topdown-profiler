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
        "status": "sampled",
        "workloads_observed": ["burn"],
        "passes_observed": 2,
        "passes_with_counter_samples": 1,
        "gpu_work_time_ns": 40,
        "sampled_gpu_work_time_ns": 10,
        "counter_sample_intervals_overlapping": 1,
        "other_active_gpu_time_overlapping_ns": None,
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
    assert result["workloads_observed"] == ["burn"]
    assert result["passes_with_counter_samples"] == 0
    assert result["status"] == "no_counter_overlap"
    assert result["sampled_gpu_work_time_ns"] == 0


def test_summary_counts_other_active_gpu_overlap():
    counters = b'<trace-query-result><schema name="metal-gpu-counter-intervals"/></trace-query-result>'
    gpu = b"""<trace-query-result><node><schema name="metal-gpu-intervals">
    <col><mnemonic>start</mnemonic></col><col><mnemonic>duration</mnemonic></col>
    <col><mnemonic>event-label</mnemonic></col><col><mnemonic>state</mnemonic></col></schema>
    <row><start-time>100</start-time><duration>20</duration>
    <formatted-label fmt="topdown GPU probe pass 0:stream compute pass 0"/>
    <gpu-state fmt="Active"/></row>
    <row><start-time>110</start-time><duration>20</duration>
    <formatted-label fmt="other GPU activity"/><gpu-state fmt="Active"/></row>
    <row><start-time>110</start-time><duration>20</duration>
    <formatted-label fmt="inactive"/><gpu-state fmt="Inactive"/></row>
    </node></trace-query-result>"""
    result = MODULE.summarize(counters, gpu)["probe_coverage"]
    assert result["workloads_observed"] == ["stream"]
    assert result["other_active_gpu_time_overlapping_ns"] == 10


def test_summary_weights_sampled_values_and_keeps_units():
    counters = b"""<trace-query-result><node><schema name="metal-gpu-counter-intervals">
    <col><mnemonic>start</mnemonic></col><col><mnemonic>duration</mnemonic></col>
    <col><mnemonic>counter-id</mnemonic></col><col><mnemonic>name</mnemonic></col>
    <col><mnemonic>label</mnemonic></col><col><mnemonic>value</mnemonic></col>
    <col><mnemonic>is-percentage</mnemonic></col></schema>
    <row><start-time>100</start-time><duration>10</duration><uint32>0</uint32>
    <gpu-counter-name fmt="ALU Limiter">1.ALU Limiter</gpu-counter-name>
    <formatted-label fmt="10%"/><fixed-decimal>10</fixed-decimal><boolean>1</boolean></row>
    <row><start-time>110</start-time><duration>20</duration><uint32>0</uint32>
    <gpu-counter-name fmt="ALU Limiter">1.ALU Limiter</gpu-counter-name>
    <formatted-label fmt="30%"/><fixed-decimal>30</fixed-decimal><boolean>1</boolean></row>
    <row><start-time>100</start-time><duration>10</duration><uint32>1</uint32>
    <gpu-counter-name fmt="GPU Read Bandwidth">2.GPU Read Bandwidth</gpu-counter-name>
    <formatted-label fmt="2 GB/s"/><fixed-decimal>2</fixed-decimal><boolean>0</boolean></row>
    <row><start-time>100</start-time><duration>30</duration><uint32>2</uint32>
    <gpu-counter-name fmt="Top Performance Limiter">-2.Top Performance Limiter</gpu-counter-name>
    <formatted-label fmt="30% (1.ALU Limiter)"/><fixed-decimal>30</fixed-decimal><boolean>1</boolean></row>
    </node></trace-query-result>"""
    gpu = b"""<trace-query-result><node><schema name="metal-gpu-intervals">
    <col><mnemonic>start</mnemonic></col><col><mnemonic>duration</mnemonic></col>
    <col><mnemonic>event-label</mnemonic></col></schema>
    <row><start-time>100</start-time><duration>30</duration>
    <formatted-label fmt="topdown GPU probe pass 0:burn compute pass 0"/></row>
    </node></trace-query-result>"""
    summary = MODULE.summarize(counters, gpu)
    means = summary["sampled_counter_means"]
    assert means["ALU Limiter"] == {
        "value": 23.333333,
        "unit": "%",
        "sampled_time_ns": 30,
        "counter_intervals": 2,
    }
    assert means["GPU Read Bandwidth"]["unit"] == "GB/s"
    assert summary["top_limiter_categories"] == [{
        "name": "ALU Limiter",
        "sampled_time_ns": 30,
        "share_of_top_limiter_samples_percent": 100.0,
    }]


def test_summary_requires_pid_for_concurrent_labeled_probes():
    counters = b'<trace-query-result><schema name="metal-gpu-counter-intervals"/></trace-query-result>'
    gpu = b"""<trace-query-result><node><schema name="metal-gpu-intervals">
    <col><mnemonic>start</mnemonic></col><col><mnemonic>duration</mnemonic></col>
    <col><mnemonic>event-label</mnemonic></col><col><mnemonic>state</mnemonic></col>
    <col><mnemonic>process</mnemonic></col></schema>
    <row><start-time>100</start-time><duration>20</duration>
    <formatted-label fmt="topdown GPU probe pass 0:burn compute pass 0"/>
    <gpu-state fmt="Active"/><process><pid>101</pid></process></row>
    <row><start-time>110</start-time><duration>20</duration>
    <formatted-label fmt="topdown GPU probe pass 0:burn compute pass 0"/>
    <gpu-state fmt="Active"/><process><pid>202</pid></process></row>
    </node></trace-query-result>"""
    with pytest.raises(ValueError, match="multiple labeled probe PIDs"):
        MODULE.summarize(counters, gpu)
    result = MODULE.summarize(counters, gpu, pid=101)["probe_coverage"]
    assert result["passes_observed"] == 1
    assert result["gpu_work_time_ns"] == 20
    assert result["other_active_gpu_time_overlapping_ns"] == 10
    with pytest.raises(ValueError, match="no labeled probe intervals"):
        MODULE.summarize(counters, gpu, pid=303)


def test_summary_streams_files_and_keeps_cross_row_and_nested_references(tmp_path):
    counters = tmp_path / "counters.xml"
    counters.write_bytes(b"""<trace-query-result><node><schema name="metal-gpu-counter-intervals">
    <col><mnemonic>start</mnemonic></col><col><mnemonic>duration</mnemonic></col>
    <col><mnemonic>counter-id</mnemonic></col><col><mnemonic>name</mnemonic></col>
    <col><mnemonic>label</mnemonic></col><col><mnemonic>value</mnemonic></col>
    <col><mnemonic>is-percentage</mnemonic></col></schema>
    <row><start-time id="1">100</start-time><duration id="2">10</duration><uint32>0</uint32>
    <gpu-counter-name id="4" fmt="ALU Limiter">1.ALU Limiter</gpu-counter-name>
    <formatted-label><fixed-decimal id="6">5</fixed-decimal></formatted-label>
    <fixed-decimal ref="6"/><boolean id="7">1</boolean></row>
    <row><start-time>110</start-time><duration ref="2"/><uint32>0</uint32>
    <gpu-counter-name ref="4"/><formatted-label/>
    <fixed-decimal ref="6"/><boolean ref="7"/></row>
    </node></trace-query-result>""")
    gpu = tmp_path / "gpu.xml"
    gpu.write_bytes(b"""<trace-query-result><node><schema name="metal-gpu-intervals">
    <col><mnemonic>start</mnemonic></col><col><mnemonic>duration</mnemonic></col>
    <col><mnemonic>event-label</mnemonic></col><col><mnemonic>state</mnemonic></col>
    <col><mnemonic>process</mnemonic></col></schema>
    <row><start-time>100</start-time><duration>10</duration>
    <formatted-label fmt="topdown GPU probe pass 0:burn compute pass 0"/>
    <gpu-state fmt="Active"/><process id="21"><pid id="22">101</pid></process></row>
    <row><start-time>110</start-time><duration>10</duration>
    <formatted-label fmt="topdown GPU probe pass 1:burn compute pass 1"/>
    <gpu-state fmt="Active"/><process ref="21"/></row>
    </node></trace-query-result>""")
    result = MODULE.summarize(counters, gpu)
    assert result["probe_coverage"]["passes_observed"] == 2
    assert result["sampled_counter_means"]["ALU Limiter"]["value"] == 5.0


@pytest.mark.parametrize("source,message", [
    (b'<trace-query-result><schema name="metal-gpu-counter-intervals"/>'
     b'<row><start-time id="1">1</start-time><duration>1</duration><uint32>0</uint32>'
     b'<gpu-counter-name>A</gpu-counter-name></row>'
     b'<row><start-time id="1">2</start-time><duration>1</duration><uint32>0</uint32>'
     b'<gpu-counter-name>A</gpu-counter-name></row></trace-query-result>', "duplicate XML id"),
    (b'<trace-query-result><schema name="metal-gpu-counter-intervals"/>'
     b'<row><start-time>1</start-time><duration>0</duration><uint32>0</uint32>'
     b'<gpu-counter-name>A</gpu-counter-name></row></trace-query-result>', "duration must be positive"),
])
def test_summary_rejects_ambiguous_or_zero_length_samples(source, message):
    with pytest.raises(ValueError, match=message):
        MODULE.summarize(source)
