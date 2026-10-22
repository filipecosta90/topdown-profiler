"""Collect native GPU replay counters with gpudebug; keep capture resources local."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import time


def json_documents(text: str) -> list[dict]:
    decoder = json.JSONDecoder()
    documents = []
    while text.strip():
        value, end = decoder.raw_decode(text.lstrip())
        if not isinstance(value, dict):
            raise ValueError("Expected gpudebug JSON objects")
        documents.append(value)
        text = text.lstrip()[end:]
    if not documents:
        raise ValueError("gpudebug returned no JSON")
    return documents


def collect(trace: Path, output: Path, execution: str = "overlapping") -> dict:
    if execution not in {"overlapping", "serial"}:
        raise ValueError("Choose overlapping or serial replay execution")
    if not trace.exists() or trace.suffix != ".gputrace":
        raise ValueError("Require an existing .gputrace capture")
    output.mkdir(parents=True, exist_ok=False)
    sequence = 0
    session = None

    def run(arguments, timeout=120):
        nonlocal sequence
        sequence += 1
        command = ["xcrun", "gpudebug", *arguments]
        (output / f"{sequence:03d}.command.json").write_text(json.dumps(command))
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            (output / f"{sequence:03d}.stdout").write_bytes(exc.stdout or b"")
            (output / f"{sequence:03d}.stderr").write_bytes(exc.stderr or b"")
            raise ValueError(f"gpudebug timed out; partial artifacts: {output}") from exc
        (output / f"{sequence:03d}.stdout").write_text(result.stdout)
        (output / f"{sequence:03d}.stderr").write_text(result.stderr)
        if result.returncode:
            raise ValueError(f"gpudebug failed: {result.stderr.strip() or result.stdout.strip()}; artifacts: {output}")
        return result.stdout

    def query(command):
        response = run(["-s", session, "--json", "-c", command])
        try:
            return json_documents(response)[-1]
        except ValueError as exc:
            raise ValueError(f"Unexpected gpudebug response to {command!r}: {response.strip()[:300]}; artifacts: {output}") from exc

    try:
        started = run(["-t", str(trace.resolve()), "--timeout", "180", "-c", "profile"])
        match = re.search(r"Session (\d+) created\.", started)
        if not match:
            raise ValueError(f"gpudebug did not create a session; artifacts: {output}")
        session = match.group(1)
        run(["-s", session, "-c", f"profile run --gpu-state default --exec {execution}"])
        status = query("profile")
        if status.get("state") != "loaded":
            raise ValueError(f"GPU replay profiling is unavailable: {status}; artifacts: {output}")
        # Xcode can report "loaded" before its asynchronous performance tree is populated.
        deadline = time.monotonic() + 30
        while True:
            try:
                query("go /performance")
                query("go /performance/encoders")
                listing = query("list --all")
                if listing.get("children"):
                    break
            except ValueError as exc:
                if "has no info" not in str(exc) and "no info handler" not in str(exc):
                    raise
            if time.monotonic() >= deadline:
                raise ValueError(f"GPU performance tables did not become ready; artifacts: {output}")
            time.sleep(0.25)
        children = listing.get("children")
        if (not isinstance(children, list) or not children or listing.get("totalCount") != len(children)
                or any(not isinstance(row, dict) or not re.fullmatch(r"[a-z]+\d+", str(row.get("name", ""))) for row in children)):
            raise ValueError("Incomplete or unexpected GPU encoder listing")
        encoders = [{"id": row["name"], "listing": row, "counters": query(f"info /performance/encoders/{row['name']}")}
                    for row in children]
        query("go /performance/timeline/counters")
        timeline = query("info /performance/timeline")
        document = {
            "schema_version": 1, "measurement_kind": "apple-gpu-replay-profile",
            "scope": {"kind": "captured-metal-workload", "trace_name": trace.name},
            "settings": {"execution": execution, "gpu_state": "default", "replayed": True},
            "device": query("status").get("device"), "timeline": timeline, "encoders": encoders,
            "tma_available": False,
            "limitations": [
                "Measurements describe replay of the captured Metal workload, not original application wall time.",
                "Native limiter percentages overlap and must not be summed into a pipeline-slot partition.",
                "Encoder cost is the profiler's cost attribution, not a CPU-time percentage.",
                "Default replay GPU state and execution mode can change timings; compare matched settings.",
            ],
        }
        (output / "profile.json").write_text(json.dumps(document, indent=2, allow_nan=False) + "\n")
        return document
    finally:
        if session is not None:
            run(["--terminate", session], timeout=15)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--output", required=True, type=Path, help="new directory for native JSON and command logs")
    parser.add_argument("--execution", choices=["overlapping", "serial"], default="overlapping")
    args = parser.parse_args()
    try:
        result = collect(args.trace, args.output, args.execution)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps({"profile": str(args.output / "profile.json"), "device": result["device"],
                      "encoder_count": len(result["encoders"]), "timeline": result["timeline"]}, indent=2))


if __name__ == "__main__":
    main()
