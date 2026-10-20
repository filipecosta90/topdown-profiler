# Apple silicon profiles

Native Instruments capture/export is available through `topdown apple`. This is an
experimental path separate from the Linux slot-TMA database and funnel. It adds no
runtime dependencies and does not invoke `sudo` or change host settings.

## Status on the tested M6

Checked 20 October 2026 on Apple M6, macOS 27.0, Xcode/xctrace 27.0 (27A266a):

| Mode | Native measurement | Local result |
|---|---|---|
| `time` | Timer-sampled stacks | Capture and JSON export work |
| `cpu` | Cycle-sampled stacks | Recorder fails configuring KPC: operation not permitted |
| `counters` | Native PMC event values | Recorder exits zero, but trace has no counter table; export correctly fails |

The smoke probe completed its 268,435,456-step recurrence with the expected checksum
`9087321014236724183`. The first CLI Time Profiler run exported 369 stack samples,
367 with `alu_work` as the leaf. These observations demonstrate capture/export;
they do not establish unbiased cost fractions or timing accuracy. The earlier
CPU Counters trace contained 363 timer samples, also deliberately excluded from PMU
mode. Logs and raw traces remain in the local `.local-runs/` directory.

The final smoke using the committed example source exported 372 timer samples with
the same expected target checksum. [Validation summary](apple-m6-validation.json)
retains commands, hashes, test/coverage results and observed limitations.

Developer Tools Security reported disabled, but this observation does **not**
establish the cause or a remedy. No privilege/security setting was changed.
[Apple's M6 issue thread](https://developer.apple.com/forums/thread/849179) reports
the same KPC failure; the Apple engineer requests diagnostics and offers no fix in
the retrieved answer. Do not retry PMU collection unchanged or describe this as
working M6 TMA.

## Record and export

Install from the checkout using the project's usual Poetry environment, then:

```sh
poetry run topdown apple doctor
xcrun clang -O3 -g examples/apple-probe.c -o /tmp/topdown-apple-probe

# Launch an explicit target. The output directory must not already exist.
poetry run topdown apple record --mode time --duration 4 \
  --output /tmp/apple-time-001 -- /tmp/topdown-apple-probe

# Request real PMU values; there is no timer fallback if counters are absent.
poetry run topdown apple record --mode counters --duration 4 \
  --output /tmp/apple-pmu-001 -- /tmp/topdown-apple-probe

# Export an existing trace, including one saved from the Instruments GUI.
poetry run topdown apple export /path/to/capture.trace \
  --mode counters --output /tmp/apple-export-001
```

`--mode cpu` selects CPU Profiler instead. The `record` command launches one target
and uses `--no-prompt`; it does not attach to unrelated processes or record all
processes. Programs should have bounded lifetimes, as the included probe does.
On recorder failure/interruption, target termination is unverified; inspect the
retained recorder/target artifacts before another capture. The probe's alarm also
bounds its lifetime independently of the recorder.

Exports select one run (`--run`, default 1) and one schema. They use the launch PID
from trace metadata; pass `--pid` for an ambiguous or different target. Optional
`--start-ns`/`--end-ns` select a trace-relative, half-open interval. Defaults include
startup and shutdown. Signposts help select an interval in Instruments, but this
implementation does not automatically translate a signpost into the export window.

The output directory contains commands, recorder/export logs, a raw trace for new
captures, `toc.xml`, `samples.xml`, and `profile.json` when export succeeds. New
captures also retain `capture.json` with executable/collector hashes, tool version,
host metadata and outcome. The JSON includes XML checksums, target exit metadata,
per-row PID/TID/core/time, and either native event values or stack/weight data.
Raw artifacts may contain local paths/device metadata; keep them local and publish
only reviewed summaries and hashes.

## Units and validation limits

`cpu-pmc-value` event values stay unsigned integers, including values above 2^53.
They retain native semantics: the importer does not assume cumulative values or
deltas, sum them across samples/cores, or derive IPC. `cpu-profile` requires the
native cycle-weight type. `time-profile` retains its native weight and reports
leaf **sample counts** separately. Malformed schemas, missing/cyclic references,
ambiguous tables, inconsistent PID scope and empty windows fail the export.

Every profile includes the four TMA roots with JSON `null`. Apple documents native
bottleneck categories and its own bandwidth/time denominators; equivalence to an
Intel pipeline-slot tree has not been established. No native samples are imported
into the existing TMA funnel, so a timer profile cannot become a 100% retiring or
backend-bound claim. [Apple's analysis workflow](https://developer.apple.com/documentation/xcode/addressing-cpu-bottlenecks)
and [Instruments presentation](https://developer.apple.com/videos/play/wwdc2025/308/)
are the primary methodology references.

The raw PMU and cycle importers are tested against schema-shaped synthetic rows,
based on the installed Xcode schema declarations. Positive M6 hardware-counter
export remains **unvalidated**. Before enabling derived bottleneck shares, obtain
a successful native capture, identify the actual formulas and denominators, and
run scoped idle/N/2N controls with known checksums and matched conditions. Verify
sampling, loss and heterogeneous-core semantics rather than renaming native metrics.

## Development evidence

Started from upstream `71dea5e`; local branch `feat/apple-silicon-profiling`.
The original test suite exposed two Intel-specific command assertions that depended
on the host architecture. They now explicitly simulate x86, with an added ARM
test retaining the existing system-wide behavior. No collector scope was changed.

The mandatory CPU-perf consultation used local MCP stdio, server 0.1.1, bundled
library `deb5a0bac467`. `library_status` and contextual `ask` succeeded, but the
library had 0/287 sources indexed and supplied no supporting passages. Its unread
suggestions were not treated as evidence. Calls/outputs are retained locally in
`.local-runs/cpu-perf/`. Actual implementation/review was one sequential Codex
session; exact serving model and token costs are unknown. No independent agent,
optimization population, performance acceptance, upstream PR or publication is claimed.
