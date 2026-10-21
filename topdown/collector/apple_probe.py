"""Unattended native Apple capability check with a machine-readable outcome."""

from datetime import datetime, timezone
from pathlib import Path

from topdown.collector.apple import (AppleProfileError, FIXED_EVENTS, M1_EVENTS, M1_FETCH_EVENTS, M1_MAP_EVENTS,
                                     MODES, TMA_ROOTS, _require_m1_host, _run, _write_json, _xml, doctor,
                                     record_profile)

EXPECTED_CHECKSUM = "9087321014236724183"
PROBE_SOURCE = r'''#include <stdint.h>
#include <stdio.h>
#include <unistd.h>

__attribute__((noinline)) uint64_t alu_work(uint64_t n) {
    uint64_t x = 1;
    for (uint64_t i = 0; i < n; i++) {
        x ^= x << 13; x ^= x >> 7; x ^= x << 17;
    }
    return x;
}
int main(void) {
    alarm(8);
    usleep(250000);
    uint64_t x = alu_work(268435456ULL);
    printf("checksum=%llu\n", (unsigned long long)x);
    return x != 9087321014236724183ULL;
}
'''

EXIT_CODES = {
    "counters_present": 0,
    "profile_present": 0,
    "probe_failed": 1,
    "permission_denied": 2,
    "counter_data_missing": 3,
    "tool_unavailable": 4,
    "unsupported_host": 4,
    "timeout": 5,
    "interrupted": 130,
}


def _target_completed(output: Path) -> bool:
    try:
        text = (output / "target.stdout").read_text().strip()
        root = _xml(output / "toc.xml")
        target = root.find("./run[@number='1']/info/target/process")
        return (text == f"checksum={EXPECTED_CHECKSUM}" and target is not None
                and target.get("return-exit-status") == "0")
    except (OSError, AppleProfileError):
        return False


def probe(output: Path, mode: str = "counters") -> dict:
    """Run one owned fixed-work capture, without retries, GUI or privilege changes.

    A positive result establishes native observations only. It does not qualify
    event semantics, denominators, sampling accuracy or a TMA decomposition.
    """
    output = output.absolute()
    result = {
        "schema_version": 1,
        "status": "probe_failed",
        "exit_code": 1,
        "mode": mode,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": str(output),
        "tma_available": False,
        "tma": dict.fromkeys(TMA_ROOTS),
        "target_completed": False,
        "sample_count": 0,
    }
    created = False
    try:
        if mode not in MODES or mode == "trace":
            raise AppleProfileError("Probe supports counters, fixed, m1, m1-map, m1-fetch, cpu or time; "
                                    "use record --mode trace for Processor Trace")
        output.mkdir(parents=True, exist_ok=False)
        created = True
        environment = doctor()
        result["environment"] = environment
        if environment.get("error"):
            result.update(status="unsupported_host", reason=environment["error"])
            return result
        if mode in {"m1", "m1-map", "m1-fetch"}:
            try:
                _require_m1_host()
            except AppleProfileError as exc:
                result.update(status="unsupported_host", reason=str(exc))
                return result
        template = MODES[mode][0]
        if not environment.get("xctrace") or (template and template not in environment.get("templates", [])):
            result.update(status="tool_unavailable", reason=f"xctrace or {template or mode + ' counter tooling'} unavailable")
            return result
        source = output / "probe.c"
        executable = output / "probe"
        source.write_text(PROBE_SOURCE)
        _run(["xcrun", "clang", "-O3", "-g", str(source), "-o", str(executable)], output, "compile", 30)
        profile = record_profile(output / "capture", [str(executable)], duration=4, mode=mode)
        result["target_completed"] = _target_completed(output / "capture")
        if not result["target_completed"]:
            raise AppleProfileError("Probe completion and expected checksum were not verified")
        if mode in {"counters", "fixed", "m1", "m1-map", "m1-fetch"}:
            if mode == "fixed":
                positive = any(set(sample["event_values"]) == set(FIXED_EVENTS)
                               and all(value > 0 for value in sample["event_values"].values())
                               and any(frame["name"] == "alu_work" for frame in sample["frames"])
                               for sample in profile["samples"])
            elif mode in {"m1", "m1-map", "m1-fetch"}:
                event_count = len({"m1": M1_EVENTS, "m1-map": M1_MAP_EVENTS,
                                   "m1-fetch": M1_FETCH_EVENTS}[mode])
                # Vector order is unverified; three positive positions exceed the two fixed events.
                positive = any(len(sample.get("native_event_values", ())) == event_count
                               and sum(value > 0 for value in sample["native_event_values"]) >= 3
                               and any(frame["name"] == "alu_work" for frame in sample["frames"])
                               for sample in profile["samples"])
            else:
                positive = any(sample["value"] > 0 for sample in profile["samples"])
            if not positive:
                raise AppleProfileError("PMC rows lack sufficient positive values or observed work; "
                                        "access remains unqualified")
            result["status"] = "counters_present"
        else:
            if not any(frame["name"] == "alu_work" for sample in profile["samples"] for frame in sample["frames"]):
                raise AppleProfileError("No observed probe work in stack samples")
            result["status"] = "profile_present"
        result.update(sample_count=profile["sample_count"], measurement_kind=profile["measurement_kind"],
                      profile=str(output / "capture/profile.json"),
                      reason="Native observations verified; TMA formulas and measurement accuracy remain unvalidated.")
    except AppleProfileError as exc:
        result.update(status=exc.code if exc.code in EXIT_CODES else "probe_failed", reason=str(exc))
    except OSError as exc:
        result.update(status="probe_failed", reason=str(exc))
    except KeyboardInterrupt:
        result.update(status="interrupted", reason="Probe interrupted; target termination remains unverified.")
    finally:
        if created:
            result["target_completed"] = _target_completed(output / "capture")
        result["ended_at"] = datetime.now(timezone.utc).isoformat()
        result["exit_code"] = EXIT_CODES[result["status"]]
        if created:
            try:
                _write_json(output / "probe.json", result)
            except OSError as exc:
                result.update(status="probe_failed", exit_code=1, reason=f"Could not save probe result: {exc}")
    return result
