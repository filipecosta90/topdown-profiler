"""Experimental M1 event export retains raw event vectors and strict configurations."""

import json
import subprocess
from pathlib import Path
from runpy import run_path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest

from topdown.collector.apple import (AppleProfileError, M1_EVENTS, M1_FETCH_EVENTS, M1_FETCH_PACKAGE,
                                     M1_MAP_EVENTS, M1_MAP_PACKAGE, M1_PACKAGE, _sha256, export_profile,
                                     parse_profile, record_profile)


FIXED_FIXTURE = Path(__file__).parent / "test_data/apple_fixed_profile_m6.xml"
PLANNER = Path(__file__).resolve().parents[1] / "examples/apple-m1-kpep-plan.py"


def synthetic_m1_xml(path):
    root = ET.parse(FIXED_FIXTURE).getroot()
    for row in root.iter("row"):
        vector = row.find("pmc-events")
        if vector is not None:
            vector.text = "1 2 3 4 5 6 7 8 9 10"
    ET.ElementTree(root).write(path)


def fake_m1_export(cmd, events=M1_EVENTS, **kwargs):
    output = Path(cmd[cmd.index("--output") + 1])
    if "--toc" in cmd:
        names = " ".join(f'&quot;{name}&quot;' for name in events)
        output.write_text('<trace-toc><run number="1"><info><target><process pid="3362" '
                          'return-exit-status="0"/></target></info><data>'
                          f'<table schema="counters-profile" trigger="time" pmc-events="{names}"/>'
                          '</data></run></trace-toc>')
    else:
        synthetic_m1_xml(output)
    return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.mark.parametrize("package,events,title", [
    (M1_PACKAGE, M1_EVENTS, "Topdown M1 Event Controls"),
    (M1_MAP_PACKAGE, M1_MAP_EVENTS, "Topdown M1 Map State"),
    (M1_FETCH_PACKAGE, M1_FETCH_EVENTS, "Topdown M1 Fetch Diagnostics"),
])
def test_m1_package_and_export_order_match(package, events, title):
    root = ET.parse(package).getroot()
    names = tuple(node.text for node in root.findall("./instrument/create-table/attribute[name='pmc-events']/array/string"))
    assert names == events
    assert root.findtext("./instrument/title") == title
    assert root.findtext("./instrument/create-table/attribute[name='trigger']/string") == "time"


def test_m1_kpep_plans_match_recorded_event_order():
    groups = run_path(str(PLANNER))["EVENT_GROUPS"]
    assert groups["controls"] == M1_EVENTS
    assert groups["map-state"] == M1_MAP_EVENTS
    assert groups["fetch"] == M1_FETCH_EVENTS


@pytest.mark.parametrize("events", [M1_EVENTS, M1_MAP_EVENTS, M1_FETCH_EVENTS])
def test_m1_vector_retains_positions_without_unverified_name_mapping(tmp_path, events):
    path = tmp_path / "m1.xml"
    synthetic_m1_xml(path)
    profile = parse_profile(path, "counters-profile", 3362, fixed_events=events)
    assert profile["sample_count"] == 3
    assert profile["unmeasured_rows"] == 1
    assert profile["samples"][0]["native_event_values"] == list(range(1, 11))
    assert "event_values" not in profile["samples"][0]
    assert profile["configured_events"] == list(events)
    assert profile["event_value_order_verified"] is False
    assert profile["tma_available"] is False
    assert set(profile["tma"].values()) == {None}


@pytest.mark.parametrize("mode,events", [("m1", M1_EVENTS), ("m1-map", M1_MAP_EVENTS),
                                        ("m1-fetch", M1_FETCH_EVENTS)])
@pytest.mark.parametrize("change", ["reorder", "trigger", "missing"])
def test_m1_export_rejects_changed_configuration(tmp_path, change, mode, events):
    def altered(cmd, **kwargs):
        result = fake_m1_export(cmd, events=events, **kwargs)
        if "--toc" in cmd:
            path = Path(cmd[cmd.index("--output") + 1])
            text = path.read_text()
            if change == "reorder":
                first = events[2]
                second = events[3]
                text = text.replace(f"{first}&quot; &quot;{second}", f"{second}&quot; &quot;{first}")
            elif change == "trigger":
                text = text.replace('trigger="time"', 'trigger="pmi"')
            else:
                text = text.replace(f"&quot;{events[-1]}&quot;", "")
            path.write_text(text)
        return result

    with patch("subprocess.run", side_effect=altered):
        with pytest.raises(AppleProfileError, match=f"{mode} mode requires"):
            export_profile(tmp_path / "trace", tmp_path / "out", mode)


@pytest.mark.parametrize("mode,events,package,title,kind,metadata_key", [
    ("m1", M1_EVENTS, M1_PACKAGE, "Topdown M1 Event Controls", "native-m1-event-samples", "m1_package_sha256"),
    ("m1-map", M1_MAP_EVENTS, M1_MAP_PACKAGE, "Topdown M1 Map State", "native-m1-map-state-samples",
     "m1_map_package_sha256"),
    ("m1-fetch", M1_FETCH_EVENTS, M1_FETCH_PACKAGE, "Topdown M1 Fetch Diagnostics",
     "native-m1-fetch-samples", "m1_fetch_package_sha256"),
])
def test_m1_record_uses_local_package_and_preserves_configuration(tmp_path, mode, events, package, title, kind,
                                                                   metadata_key):
    def fake_tools(cmd, **kwargs):
        if "instrumentbuilder" in cmd and "-o" in cmd:
            contents = Path(cmd[cmd.index("-o") + 1]) / "Contents"
            contents.mkdir(parents=True)
            (contents / "pkginfo.plist").write_text("compiled")
        elif "export" in cmd:
            return fake_m1_export(cmd, events=events, **kwargs)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with patch("topdown.collector.apple._require_m1_host"), \
         patch("topdown.collector.apple.doctor", return_value={}), \
         patch("subprocess.run", side_effect=fake_tools) as run:
        profile = record_profile(tmp_path / "capture", [__file__], mode=mode)
    commands = [call.args[0] for call in run.call_args_list]
    record = next(command for command in commands if "record" in command)
    assert record[record.index("--instrument") + 1] == title
    assert "--package" in record and "--template" not in record
    assert profile["measurement_kind"] == kind
    assert profile["sample_count"] == 3
    metadata = json.loads((tmp_path / "capture/capture.json").read_text())
    assert metadata[metadata_key] == _sha256(package)
    assert (tmp_path / f"capture/{mode}-counters.instrpkg").read_bytes() == package.read_bytes()


@pytest.mark.parametrize("mode", ["m1", "m1-map", "m1-fetch"])
def test_m1_wrong_host_is_rejected_before_output_creation(tmp_path, mode):
    with patch("topdown.collector.apple.platform.system", return_value="Linux"):
        with pytest.raises(AppleProfileError, match="Apple M1 host"):
            record_profile(tmp_path / "capture", [__file__], mode=mode)
    assert not (tmp_path / "capture").exists()
