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
