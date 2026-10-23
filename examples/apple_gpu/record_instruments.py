"""Record a checksum-verified M1 Metal control with Instruments GPU counters."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import subprocess
import xml.etree.ElementTree as ET

from make_counter_template import PROFILE_NAMES, select_profile
from summarize_counters import summarize


TEMPLATE = ("Applications/Instruments.app/Contents/Packages/GPU.instrdst/Contents/Templates/"
            "Metal System Trace.tracetemplate")
REQUIRED_COUNTERS = {
    3: {"ALU Limiter": "%", "Buffer Write Limiter": "%", "GPU Read Bandwidth": "GB/s",
        "Top Performance Limiter": "%"},
    4: {"ALU Utilization": "%", "Buffer Store Utilization": "%", "GPU Read Bandwidth": "GB/s"},
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(output: Path, name: str, command: list[str], timeout: float) -> None:
    (output / f"{name}.command.json").write_text(json.dumps(command, indent=2) + "\n")
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        for suffix, value in (("stdout", exc.stdout), ("stderr", exc.stderr)):
            (output / f"{name}.{suffix}").write_text(
                value.decode(errors="replace") if isinstance(value, bytes) else value or "")
        raise ValueError(f"{name} timed out; retained artifacts in {output}") from exc
    (output / f"{name}.stdout").write_text(result.stdout)
    (output / f"{name}.stderr").write_text(result.stderr)
    if result.returncode:
        raise ValueError(f"{name} failed (exit {result.returncode}): "
                         f"{result.stderr.strip() or result.stdout.strip()}; artifacts: {output}")


def _verify(toc: Path, stdout: Path, profile: int, mode: str, passes: int) -> None:
    text = stdout.read_text()
    if ("device=Apple M1\n" not in text or
            f"workload={mode} passes={passes} " not in text or "checksum_verified=true" not in text):
        raise ValueError("M1 probe identity, arguments or checksum could not be verified")
    try:
        run = ET.parse(toc).getroot().find("./run[@number='1']")
    except ET.ParseError as exc:
        raise ValueError("Invalid Instruments table of contents") from exc
    target = run.find("./info/target/process") if run is not None else None
    if target is None or target.get("return-exit-status") != "0":
        raise ValueError("Instruments target did not exit successfully")
    profiles = run.findall("./data/table[@schema='metal-gpu-counter-profile']")
    if len(profiles) != 1 or profiles[0].get("counter-profile") != str(profile):
        raise ValueError("Instruments did not select the requested GPU counter profile")
    if f"Counter Set: {PROFILE_NAMES[profile]}" not in {node.text for node in run.iter("value")}:
        raise ValueError("Instruments did not identify the requested GPU counter set")
    for schema in ("metal-gpu-counter-intervals", "metal-gpu-intervals"):
        if len(run.findall(f"./data/table[@schema='{schema}']")) != 1:
            raise ValueError(f"Instruments did not export exactly one {schema} table")


def _verify_coverage(result: dict, mode: str, passes: int, profile: int, output: Path) -> None:
    coverage = result["probe_coverage"]
    expected_workload = "burn" if mode == "alu" else "stream"
    if (coverage["workloads_observed"] != [expected_workload] or coverage["passes_observed"] != passes
            or coverage["passes_with_counter_samples"] != passes or not result["sampled_counter_means"]):
        raise ValueError(f"Incomplete M1 probe counter coverage; artifacts: {output}")
    means = result["sampled_counter_means"]
    if any(means.get(name, {}).get("unit") != unit for name, unit in REQUIRED_COUNTERS[profile].items()):
        raise ValueError(f"Missing M1 GPU metrics or unexpected units for counter profile {profile}; artifacts: {output}")
    if profile == 3 and (not result["top_limiter_categories"]
                         or result["top_limiter_unparsed_sampled_time_ns"]):
        raise ValueError(f"Unrecognized M1 top-limiter labels; artifacts: {output}")


def record(output: Path, mode: str = "alu", passes: int = 100, profile: int = 3,
           tail: float = 1.0, time_limit: int = 8) -> dict:
    if (mode not in ("alu", "stream") or type(passes) is not int or not 1 <= passes <= 500
            or profile not in PROFILE_NAMES or not math.isfinite(tail) or not 0.5 <= tail <= 5
            or type(time_limit) is not int or not 5 <= time_limit <= 60):
        raise ValueError("Choose alu/stream, 1–500 passes, profile 3/4, tail 0.5–5s and time limit 5–60s")
    developer = Path(os.environ.get("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")).resolve()
    source_template = developer.parent / TEMPLATE
    source_probe = Path(__file__).with_name("probe.swift")
    if not source_template.is_file():
        raise ValueError(f"Full Xcode Metal System Trace template is missing at {source_template}; set DEVELOPER_DIR")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    template = output / "counters.tracetemplate"
    template.write_bytes(select_profile(source_template.read_bytes(), profile))
    binary, trace = output / "probe", output / "recording.trace"
    _run(output, "compile", ["xcrun", "swiftc", "-O", "-framework", "Metal", str(source_probe),
                             "-o", str(binary)], 90)
    target = f"{shlex.quote(str(binary))} {passes} {mode} && sleep {tail}"
    _run(output, "record", ["xcrun", "xctrace", "record", "--no-prompt", "--template", str(template),
                            "--time-limit", f"{time_limit}s", "--target-stdout", str(output / "target.stdout"),
                            "--output", str(trace), "--launch", "--", "/bin/sh", "-c", target],
         time_limit + 40)
    toc = output / "toc.xml"
    _run(output, "toc", ["xcrun", "xctrace", "export", "--input", str(trace), "--toc",
                          "--output", str(toc)], 60)
    _verify(toc, output / "target.stdout", profile, mode, passes)
    paths = {}
    for name, schema in (("counters", "metal-gpu-counter-intervals"), ("gpu-intervals", "metal-gpu-intervals")):
        path = output / f"{name}.xml"
        xpath = f'/trace-toc/run[@number="1"]/data/table[@schema="{schema}"]'
        _run(output, f"export-{name}", ["xcrun", "xctrace", "export", "--input", str(trace),
                                         "--xpath", xpath, "--output", str(path)], 120)
        paths[name] = path
    result = summarize(paths["counters"], paths["gpu-intervals"])
    _verify_coverage(result, mode, passes, profile, output)
    result["capture"] = {
        "mode": mode, "passes": passes, "counter_profile": PROFILE_NAMES[profile],
        "checksum_verified": True, "target_exit_status": 0,
        "source_sha256": _sha256(source_probe), "template_sha256": _sha256(template),
        "toc_sha256": _sha256(toc), "counter_xml_sha256": _sha256(paths["counters"]),
        "gpu_xml_sha256": _sha256(paths["gpu-intervals"]),
    }
    (output / "profile.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new directory for trace, XML and summary")
    parser.add_argument("--mode", choices=("alu", "stream"), default="alu")
    parser.add_argument("--passes", type=int, default=100)
    parser.add_argument("--profile", type=int, choices=sorted(PROFILE_NAMES), default=3)
    parser.add_argument("--tail", type=float, default=1)
    parser.add_argument("--time-limit", type=int, default=8)
    args = parser.parse_args()
    try:
        result = record(args.output, args.mode, args.passes, args.profile, args.tail, args.time_limit)
    except (OSError, ValueError, ET.ParseError) as exc:
        parser.error(str(exc))
    print(json.dumps({"profile": str(args.output.resolve() / "profile.json"),
                      "counter_rows": result["counter_rows"], "probe_coverage": result["probe_coverage"]}, indent=2))


if __name__ == "__main__":
    main()
