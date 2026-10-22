"""Correlate decoded execution intervals with native thread-state observations."""

from bisect import bisect_right
from collections import defaultdict
from pathlib import Path

from topdown.collector.apple import AppleProfileError, _sha256
from topdown.collector.apple_trace import _identity, _table, _uint

STATE_TYPES = {"start": "start-time", "duration": "duration", "process": "process", "thread": "thread",
               "state": "thread-state", "core": "core"}


def annotate_scheduling(profile: dict, path: Path) -> None:
    """Intersect time spans; never subdivide or distribute native counter values."""
    rows, resolve = _table(path, "thread-state", STATE_TYPES)
    timeline = defaultdict(list)
    target = profile["scope"]["pid"]
    excluded, target_rows = 0, 0
    for cells in rows:
        process = cells["process"]
        if process.tag != "process":
            raise AppleProfileError("Unknown thread-state process representation")
        pid = _uint(resolve(process.find("pid")), "pid")
        if pid != target:
            excluded += 1
            continue
        _, tid = _identity(cells, resolve)
        start = _uint(cells["start"], "start-time")
        duration = _uint(cells["duration"], "duration")
        if start + duration >= 2**64:
            raise AppleProfileError("Thread-state interval end exceeds uint64")
        if cells["state"].tag != "thread-state" or not cells["state"].text:
            raise AppleProfileError("Unknown native thread-state representation")
        core = cells["core"]
        core_id = None if core.tag == "sentinel" else _uint(core, "core")
        target_rows += 1
        if duration:
            timeline[tid].append({"start": start, "end": start + duration, "state": cells["state"].text,
                                  "core": core_id, "core_label": core.get("fmt") if core_id is not None else None})
    ends = {}
    for tid, states in timeline.items():
        states.sort(key=lambda state: state["start"])
        previous_end = 0
        for state in states:
            if state["start"] < previous_end:
                raise AppleProfileError("Overlapping native thread-state intervals for one thread")
            previous_end = state["end"]
        ends[tid] = [state["end"] for state in states]

    single_core_intervals = 0
    for interval in profile["intervals"]:
        start, duration, tid = interval["start_ns"], interval["duration_ns"], interval["tid"]
        finish = start + duration
        states, cores = defaultdict(int), defaultdict(int)
        entries = timeline.get(tid, [])
        index = bisect_right(ends.get(tid, []), start)
        while index < len(entries) and entries[index]["start"] < finish:
            state = entries[index]
            overlap = min(finish, state["end"]) - max(start, state["start"])
            if overlap > 0:
                states[state["state"]] += overlap
                if state["state"] == "Running" and state["core"] is not None:
                    cores[state["core"], state["core_label"]] += overlap
            index += 1
        covered = sum(states.values())
        running = [{"core": key[0], "core_label": key[1], "duration_ns": value}
                   for key, value in sorted(cores.items(), key=lambda item: item[0][0])]
        single = None
        if duration > 0 and covered == duration and len(running) == 1 and running[0]["duration_ns"] == duration:
            single = {key: running[0][key] for key in ["core", "core_label"]}
            single_core_intervals += 1
        interval["scheduling"] = {"covered_ns": covered, "uncovered_ns": duration - covered,
                                  "states_ns": dict(states), "running_cores": running,
                                  "coverage_complete": duration > 0 and covered == duration,
                                  "single_running_core": single}
    profile.update(scheduling_available=True, thread_state_rows=target_rows, thread_state_other_pid_rows=excluded,
                   thread_state_xml_sha256=_sha256(path), single_running_core_intervals=single_core_intervals)
    profile["limitations"].extend([
        "Scheduling annotations describe inclusive interval time, not exclusive function CPU time.",
        "Native instruction/cycle values are not divided among states or cores.",
        "A single running core is assigned only when observed Running spans cover the entire interval on one core.",
        "Thread-state model boundaries may be synthetic; no process-wide waiting percentage is computed.",
        "Complete scheduling coverage does not establish complete instruction decoding.",
    ])
