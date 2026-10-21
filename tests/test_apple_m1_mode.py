"""Experimental M1 event export retains raw event vectors and strict configurations."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest

from topdown.collector.apple import (AppleProfileError, M1_EVENTS, M1_PACKAGE, _sha256, export_profile,
                                     parse_profile, record_profile)


FIXED_FIXTURE = Path(__file__).parent / "test_data/apple_fixed_profile_m6.xml"


def synthetic_m1_xml(path):
    root = ET.parse(FIXED_FIXTURE).getroot()
    for row in root.iter("row"):
        vector = row.find("pmc-events")
        if vector is not None:
            vector.text = "1 2 3 4 5 6 7 8 9 10"
    ET.ElementTree(root).write(path)


def fake_m1_export(cmd, **kwargs):
    output = Path(cmd[cmd.index("--output") + 1])
    if "--toc" in cmd:
        names = " ".join(f'&quot;{name}&quot;' for name in M1_EVENTS)
        output.write_text('<trace-toc><run number="1"><info><target><process pid="3362" '
                          'return-exit-status="0"/></target></info><data>'
                          f'<table schema="counters-profile" trigger="time" pmc-events="{names}"/>'
                          '</data></run></trace-toc>')
    else:
        synthetic_m1_xml(output)
    return subprocess.CompletedProcess(cmd, 0, "", "")


def test_m1_package_and_export_order_match():
    root = ET.parse(M1_PACKAGE).getroot()
    names = tuple(node.text for node in root.findall("./instrument/create-table/attribute[name='pmc-events']/array/string"))
    assert names == M1_EVENTS
    assert root.findtext("./instrument/create-table/attribute[name='trigger']/string") == "time"


def test_m1_vector_retains_positions_without_unverified_name_mapping(tmp_path):
    path = tmp_path / "m1.xml"
    synthetic_m1_xml(path)
    profile = parse_profile(path, "counters-profile", 3362, fixed_events=M1_EVENTS)
    assert profile["sample_count"] == 3
    assert profile["unmeasured_rows"] == 1
    assert profile["samples"][0]["native_event_values"] == list(range(1, 11))
    assert "event_values" not in profile["samples"][0]
    assert profile["configured_events"] == list(M1_EVENTS)
    assert profile["event_value_order_verified"] is False
    assert profile["tma_available"] is False
    assert set(profile["tma"].values()) == {None}


@pytest.mark.parametrize("change", ["reorder", "trigger", "missing"])
def test_m1_export_rejects_changed_configuration(tmp_path, change):
    def altered(cmd, **kwargs):
        result = fake_m1_export(cmd, **kwargs)
        if "--toc" in cmd:
            path = Path(cmd[cmd.index("--output") + 1])
            text = path.read_text()
            if change == "reorder":
                text = text.replace("RETIRE_UOP&quot; &quot;BRANCH_MISPRED_NONSPEC",
                                    "BRANCH_MISPRED_NONSPEC&quot; &quot;RETIRE_UOP")
            elif change == "trigger":
                text = text.replace('trigger="time"', 'trigger="pmi"')
            else:
                text = text.replace("&quot;L1I_CACHE_MISS_DEMAND&quot;", "")
            path.write_text(text)
        return result

    with patch("subprocess.run", side_effect=altered):
        with pytest.raises(AppleProfileError, match="m1 mode requires"):
            export_profile(tmp_path / "trace", tmp_path / "out", "m1")


def test_m1_record_uses_local_package_and_preserves_configuration(tmp_path):
    def fake_tools(cmd, **kwargs):
        if "instrumentbuilder" in cmd and "-o" in cmd:
            contents = Path(cmd[cmd.index("-o") + 1]) / "Contents"
            contents.mkdir(parents=True)
            (contents / "pkginfo.plist").write_text("compiled")
        elif "export" in cmd:
            return fake_m1_export(cmd, **kwargs)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with patch("topdown.collector.apple._require_m1_host"), \
         patch("topdown.collector.apple.doctor", return_value={}), \
         patch("subprocess.run", side_effect=fake_tools) as run:
        profile = record_profile(tmp_path / "capture", [__file__], mode="m1")
    commands = [call.args[0] for call in run.call_args_list]
    record = next(command for command in commands if "record" in command)
    assert record[record.index("--instrument") + 1] == "Topdown M1 Event Controls"
    assert "--package" in record and "--template" not in record
    assert profile["measurement_kind"] == "native-m1-event-samples"
    assert profile["sample_count"] == 3
    metadata = json.loads((tmp_path / "capture/capture.json").read_text())
    assert metadata["m1_package_sha256"] == _sha256(M1_PACKAGE)
    assert (tmp_path / "capture/m1-counters.instrpkg").read_bytes() == M1_PACKAGE.read_bytes()


def test_m1_wrong_host_is_rejected_before_output_creation(tmp_path):
    with patch("topdown.collector.apple.platform.system", return_value="Linux"):
        with pytest.raises(AppleProfileError, match="Apple M1 host"):
            record_profile(tmp_path / "capture", [__file__], mode="m1")
    assert not (tmp_path / "capture").exists()
