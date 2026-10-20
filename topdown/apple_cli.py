"""CLI for native Apple profiles whose units differ from Linux slot TMA."""

import json
from pathlib import Path
from typing import Annotated, Optional

import typer

from topdown.collector.apple import AppleProfileError, doctor, export_profile, record_profile

app = typer.Typer(help="Capture/export native Apple silicon profiles; slot TMA remains unavailable.")


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
    mode: Annotated[str, typer.Option(help="counters (PMU), cpu (cycle samples) or time (timer samples)")] = "counters",
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
    typer.echo(json.dumps({"profile": str(output / "profile.json"), "sample_count": profile["sample_count"],
                           "measurement_kind": profile["measurement_kind"], "tma": profile["tma"]}, indent=2))


@app.command(name="export")
def export_trace(
    trace: Annotated[Path, typer.Argument(exists=True, help="Existing Instruments .trace bundle")],
    output: Annotated[Path, typer.Option(help="New export directory")],
    mode: Annotated[str, typer.Option(help="counters, cpu or time")] = "counters",
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
    typer.echo(json.dumps({"profile": str(output / "profile.json"), "sample_count": profile["sample_count"],
                           "measurement_kind": profile["measurement_kind"], "tma": profile["tma"]}, indent=2))
