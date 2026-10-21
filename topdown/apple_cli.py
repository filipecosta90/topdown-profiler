"""CLI for native Apple profiles whose units differ from Linux slot TMA."""

import json
from pathlib import Path
from typing import Annotated, Optional

import typer

from topdown.collector.apple import AppleProfileError, doctor, export_profile, record_profile

app = typer.Typer(help="Count, capture and export native Apple silicon profiles; slot TMA remains unavailable.")


def _profile_summary(profile, output):
    count = "interval_count" if "interval_count" in profile else "sample_count"
    result = {"profile": str(output / "profile.json"), count: profile[count],
              "measurement_kind": profile["measurement_kind"], "tma": profile["tma"]}
    if count == "interval_count":
        result.update(gap_count=profile["gap_count"], coverage_complete=profile["coverage_complete"])
    return result


@app.command(name="stat", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def stat_target(
    ctx: typer.Context,
    output: Annotated[Path, typer.Option(help="New directory for stat.json and target output")],
    timeout: Annotated[float, typer.Option(help="Seconds before stopping a launched target", min=0.001)] = 30,
    pid: Annotated[Optional[int], typer.Option(help="Observe an existing PID instead of launching a target", min=1)] = None,
    duration: Annotated[float, typer.Option(help="Observation interval with --pid, in seconds", min=0.001)] = 5,
):
    """Count instructions/cycles without root: stat --output run -- /path/program args, or --pid PID."""
    from topdown.collector.apple_stat import stat_pid, stat_process

    command = list(ctx.args)
    if command and command[0] == "--":
        command.pop(0)
    try:
        if pid is not None:
            if command:
                raise AppleProfileError("Choose either --pid or a command after '--'")
            if ctx.get_parameter_source("timeout").name == "COMMANDLINE":
                raise AppleProfileError("Use --duration with --pid; --timeout is for launched commands")
            result = stat_pid(output, pid, duration)
        else:
            if ctx.get_parameter_source("duration").name == "COMMANDLINE":
                raise AppleProfileError("Use --timeout for launched commands; --duration requires --pid")
            result = stat_process(output, command, timeout)
    except (AppleProfileError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2))
    raise typer.Exit(result["exit_code"])


@app.command(name="probe")
def probe_host(
    output: Annotated[Path, typer.Option(help="New directory for probe result and all raw artifacts")],
    mode: Annotated[str, typer.Option(help="counters, fixed, m1, m1-map, cpu or time")] = "counters",
):
    """Compile/run a bounded probe and emit JSON with a stable status and exit code."""
    from topdown.collector.apple_probe import probe

    result = probe(output, mode)
    typer.echo(json.dumps(result, indent=2))
    raise typer.Exit(result["exit_code"])


@app.command(name="doctor")
def check():
    """Check installed tools without recording or changing host settings."""
    result = doctor()
    typer.echo(json.dumps(result, indent=2))
    if result.get("error") or not result.get("xctrace"):
        raise typer.Exit(1)


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def record(
    ctx: typer.Context,
    output: Annotated[Path, typer.Option(help="New directory for raw trace, logs, metadata and profile.json")],
    mode: Annotated[str, typer.Option(help="counters (PMU), fixed (fixed PMU + stacks), m1/m1-map (experimental events), trace (hardware trace), cpu, time")] = "counters",
    duration: Annotated[int, typer.Option(help="Maximum recording duration in seconds", min=1)] = 5,
    pid: Annotated[Optional[int], typer.Option(help="Override target PID from trace metadata", min=1)] = None,
    start_ns: Annotated[int, typer.Option(help="Inclusive trace-relative start, nanoseconds", min=0)] = 0,
    end_ns: Annotated[Optional[int], typer.Option(help="Exclusive trace-relative end, nanoseconds", min=1)] = None,
):
    """Record one launched program: topdown apple record --output run -- /path/program args."""
    command = list(ctx.args)
    if command and command[0] == "--":
        command.pop(0)
    try:
        profile = record_profile(output.resolve(), command, duration, mode, pid, start_ns, end_ns)
    except (AppleProfileError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(_profile_summary(profile, output), indent=2))


@app.command(name="export")
def export_trace(
    trace: Annotated[Path, typer.Argument(exists=True, help="Existing Instruments .trace bundle")],
    output: Annotated[Path, typer.Option(help="New export directory")],
    mode: Annotated[str, typer.Option(help="counters, fixed, m1, m1-map, trace, cpu or time")] = "counters",
    pid: Annotated[Optional[int], typer.Option(help="Only this PID; otherwise use trace target", min=1)] = None,
    run: Annotated[int, typer.Option(help="Trace run number", min=1)] = 1,
    start_ns: Annotated[int, typer.Option(min=0)] = 0,
    end_ns: Annotated[Optional[int], typer.Option(min=1)] = None,
):
    """Export native events/stacks; missing counters never fall back to timer samples."""
    if output.exists():
        typer.echo("Error: Choose a new export directory", err=True)
        raise typer.Exit(1)
    try:
        profile = export_profile(trace, output.resolve(), mode, pid, run, start_ns, end_ns)
    except (AppleProfileError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(_profile_summary(profile, output), indent=2))
