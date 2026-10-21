# Apple silicon profiles

Native CLI profiling is available through `topdown apple`. Process-accounted
instruction/cycle totals and fixed-counter samples with stacks work on the tested
M6. Native system/cluster firmware histograms and trace scheduling context are also
available. These experimental paths remain separate from the Linux slot-TMA database and
funnel. They add no runtime dependencies and do not invoke `sudo` or change host
settings. Programmable events and four-category TMA remain unavailable.

## Process instruction and cycle totals

`stat` uses macOS `proc_pid_rusage` accounting. It needs neither root nor Xcode and
supports Python 3.10–3.13. Use a new output directory:

```sh
# Capture the complete lifetime of one launched program.
poetry run topdown apple stat --output /tmp/apple-stat-001 \
  -- /path/to/program args

# Observe an existing process for five seconds without stopping it.
poetry run topdown apple stat --pid 1234 --duration 5 \
  --output /tmp/apple-stat-pid-001
```

The command prints JSON and retains `stat.json`. Launched targets also retain
`target.stdout`, `target.stderr`, executable/collector hashes and exit status.
`counters` contains unsigned integer instruction/cycle counts and user/system
times converted from Mach time to nanoseconds. Counts cover all threads in one
PID, exclude descendant processes, and retain the source API's semantics.

For launched commands, startup and shutdown are included. The final read occurs
after exit while the child is still waitable, before reaping it; short programs
therefore retain their counts. `--timeout` defaults to 30 seconds. On timeout or
interrupt, the owned process group receives SIGTERM, with a two-second grace
period before SIGKILL. Counts include any shutdown work during that grace period.
Timeout exits 5, interruption 130; ordinary target exit codes are propagated.
`target_completed` requires an observed successful exit without timeout/interrupt.

With `--pid`, counts are differences between snapshots. Process start identity is
checked to reject PID reuse, and the command never signals or waits for the target.
An idle interval can validly contain zero deltas when cumulative native counts
establish counter access. If the process disappears before the final read, the
command reports failure and retains the initial snapshot without inventing totals.

The implementation uses the macOS accounting interface described in Apple's
[Recount documentation](https://github.com/apple-oss-distributions/xnu/blob/main/doc/observability/recount.md).
The totals are not a TMA decomposition, and no IPC, frequency or residency estimate
is derived. macOS also exposes basic instruction/cycle totals through
`/usr/bin/time -l /path/to/program`; `stat` adds structured output and PID intervals.

## Fixed-counter samples and stacks

`fixed` mode requests only `FIXED_CYCLES` and `FIXED_INSTRUCTIONS`, with a timer
trigger. It avoids the programmable-event configuration rejected on this M6:

```sh
poetry run topdown apple probe --mode fixed --output /tmp/m6-fixed-check-001

xcrun clang -O3 -g examples/apple-probe.c -o /tmp/topdown-apple-probe
poetry run topdown apple record --mode fixed --duration 4 \
  --output /tmp/apple-fixed-001 -- /tmp/topdown-apple-probe

poetry run topdown apple export /path/to/fixed-recording.trace \
  --mode fixed --output /tmp/apple-fixed-export-001
```

This mode requires Xcode's `instrumentbuilder` and `xctrace`. The bundled package
is compiled into the new capture directory and supplied through `--package`;
nothing is installed into Instruments or the OS. The capture keeps the package
source, its hash, build commands/logs, raw trace, XML, and `profile.json`.

Each exported sample contains `event_values`, stack frames, PID/TID, timestamp and
the native core label. Values are read from the native `counters-profile` vector;
the event order and timer trigger must match the trace's metadata. A timer stack
alone cannot satisfy this mode. Rows with absent counter vectors are counted as
`unmeasured_rows`, rather than filled with zero. Other PIDs and rows outside the
requested interval are excluded. Native values remain integers and are neither
summed nor treated as calibrated instruction costs, IPC or slot-TMA shares.

The final CLI probe completed with its expected checksum and 370 scoped
fixed-counter samples, 367 containing `alu_work`. A separate reconstruction from
the raw XML matched every exported counter row. These establish counter observations and stack
association; sampling loss, per-core accounting and exact cost attribution remain
unvalidated. See [CLI validation](apple-m6-cli-validation.json) for the controls,
root experiments, hashes and test results.

## Hardware execution trace through CLI

The tested M6 also supports the **Processor Trace** template without root or
programmable-event configuration. Use `trace` mode for decoded execution
intervals and their native instruction/cycle counts:

```sh
xcrun clang -O2 -g -c examples/apple-trace-probe.c -o /tmp/apple-trace-probe.o
xcrun clang -g /tmp/apple-trace-probe.o -o /tmp/apple-trace-probe
xcrun dsymutil /tmp/apple-trace-probe -o /tmp/apple-trace-probe.dSYM

poetry run topdown apple record --mode trace --duration 1 \
  --output /tmp/apple-trace-001 -- /tmp/apple-trace-probe 100000

poetry run topdown apple export /path/to/recording.trace \
  --mode trace --output /tmp/apple-trace-export-001
```

The example has a five-second alarm, a bounded recurrence and a known checksum.
The default 100,000 steps produce checksum `2700328192655336907`. Unlike the large
fixed-counter probe, this example is intended for short hardware traces. Trace
recordings can still be large: an initial subsecond recording used about 140 MB.
The unattended `apple probe` command continues to support only its original four
modes; use `record --mode trace` with an explicitly bounded target.

`profile.json` contains `intervals` and `interval_count`, rather than samples.
Each interval retains its native label, binary offset, call depth and frames,
start/duration, `self_instructions`, `self_cycles`, and `self_duration_ns`.
Unreported self duration stays null. The exporter also requires and retains the
native gap table as `gaps.xml`, with its hash and selected PID/thread markers.
`coverage_complete` remains false, even for a window without gap markers.

The time window includes only intervals wholly inside the selected range.
`boundary_rows_excluded` counts overlapping intervals that cannot be subdivided
without inventing instruction/cycle attribution. Call intervals can overlap at
different depths; summing their durations would double-count elapsed time.
Gap markers are points rather than measured missing durations, so their count
cannot establish a coverage percentage or guarantee complete decoding.

Some native labels identify an address inside a function. Frames retain the
binary UUID and load address for offline symbol lookup. With a matching dSYM,
`xcrun atos -o /path/to/binary.dSYM/Contents/Resources/DWARF/binary -l LOAD_ADDRESS ADDRESS`
resolved the tested loop to its function and source line. Retain the object file
until generating the dSYM. An explicit `xctrace symbolicate` attempt exceeded the
experimental timeout; capture/export does not run it automatically.

On this Xcode build, disassembly of a 100,000-step serial loop predicts 500,004
instructions, which the trace reproduced in multiple captures. Longer 200,000-step
controls differed from their predicted count by 1 and 12 instructions despite
no in-loop gap markers. These observations support retaining decoded costs for
investigation, with their accuracy limits. They do not establish calibrated
function costs or four-category TMA. Apple's
[Processor Trace explanation](https://developer.apple.com/videos/play/wwdc2025/308/?time=923)
describes its hardware tracing and executable-based reconstruction.

The follow-up also exercised `thread_selfcounts` and `PROC_PIDTHREADCOUNTS`:
native counters are available separately for Super, Performance and Efficiency
cores. A separate C reader matched the Python reader's bytes for a stopped owned
target. Thread lifetime counts differed from process-accounted lifetime totals;
these sources remain separate and the per-thread interface is not exposed as a
new product command. A follow-up self-exec control found exact agreement of thread
and process instruction deltas within ten individual phases, while their cumulative
offset changed across exec. The offset's precise cause remains unresolved; phase
agreement does not establish lifetime equivalence. Serial/parallel arithmetic
controls produced different native instruction/cycle ratios, which cannot identify
a unique stall cause.
See [follow-up validation](apple-m6-trace-validation.json) for the measured controls
and limitations.

## System and cluster firmware context

`apple soc` reads private native IOReport channels without root, Xcode, or new runtime
dependencies. It does not launch or attach to a workload. Run the workload separately
and use a new directory:

```sh
poetry run topdown apple soc --duration 2 --output /tmp/apple-soc-001
```

On the tested M6, the command exports 27 channels: six PMP stall distributions
(`Core Block Slot`, `M$ Stall`, `DRAM Stall` for EACC0/PACC0), nine DCS bandwidth
histograms, and twelve core performance-state distributions. It retains native
channel/state IDs, labels, units, exact unsigned counter deltas, transition deltas,
read timestamps, before/after snapshots, collector hash, and worker logs. The
private native reader runs in a subprocess with a duration-plus-30-second timeout;
missing channels, changing layouts, counter resets and invalid values fail the run.
Other machines may expose different channels or reject subscriptions.

The PMP channels use native **events**, including those read through a function
named `IOReportStateGetResidency`. These values are observation counts, not
nanoseconds. The CPU performance-state channels use native `24Mticks`.
`share_of_reported_values` divides each state's delta by that channel's total;
zero totals produce null shares. The percentage and GB/s strings are native bucket
labels. Their boundaries, sample cadence and activity denominator remain unknown.
No exact bandwidth, clock frequency, process stall percentage, or TMA share is
computed. Results include unrelated activity across the system/cluster.

Controlled, randomized workload experiments found a reproducible PACC0 `DRAM Stall`
response to large dependent pointer-chasing. One-worker 8 KiB–4 MiB controls remained
near the first bucket, while 64–256 MiB controls moved strongly toward high buckets.
Disjoint 128 MiB chains retained the high response with one, two and six workers;
shared chains behaved differently, exposing cache-sharing as a confound. This
supports a coarse contextual memory signal under these controls. It does not
calibrate the labels as a workload's percentage of stalled cycles. `Core Block
Slot` remained near its first bucket in the arithmetic and memory controls; its
responsiveness and meaning are unvalidated. No automatic bottleneck classifier is
provided. A separate static branch-ring control expanded its code footprint from
256 bytes to 4 MiB and slowed by about 37 times, while `Core Block Slot` stayed in
the first bucket. This channel therefore did not identify that control's slowdown;
the experiment does not isolate instruction-cache misses from branch-target effects.
See [deep CLI validation](apple-m6-deep-validation.json).

## Trace scheduling context

When a Processor Trace recording includes a `thread-state` table, export also
retains `thread-state.xml` and adds `scheduling` to each decoded interval. Native
Running/Blocked/other state spans are intersected with that interval and thread.
The annotation reports covered and uncovered nanoseconds, native state durations,
and observed Running cores. A single core is assigned only when the complete
positive-duration interval was observed Running on that core. Missing spans,
blocking, migration and unknown core identity prevent that assignment.

The measured serial-loop fixtures and native exports resolve to both S-core and
E-core execution in separate recordings. Counts remain the original native
instruction/cycle values: they are never prorated across scheduling states or
cores. Call intervals contain inclusive time and can overlap. Native thread-state
model boundaries can be synthetic, so no process-wide waiting percentage is
computed. A two-sleep control measured 110.039 ms and 103.388 ms in the target;
corresponding native Blocked spans were 110.003 ms and 103.351 ms. Other Blocked
spans also occurred, so they are not all classified as those sleeps. Complete
scheduling coverage does not establish complete instruction trace decoding.
Traces without this table remain exportable and report
`scheduling_available: false`.

## Remaining OS counter barriers

The uncore interface accepts ownership and reports two monitors with 24 counters,
but an exhaustive disabled sweep of all 65,536 16-bit selectors accepted only zero;
all 65,535 nonzero selectors returned `EPERM`. The probe never enabled counters and
closed/reset its ownership. Together with the earlier programmable-core rejection
and running-kernel inspection, this closes another direct acquisition route on
this installed kernel. No host security setting, kernel, or boot configuration was
changed.

IOReport enumerates 207 AMC memory-controller channels, but subscriptions to each
individual channel failed without root. Enumeration alone does not establish
readable counters. A root comparison still requires interactive CLI authentication
and has not been completed. No developer-kernel or bootable OS patch was built or
tested. The existing kernel gate explains a blocker; it does not establish that
patching one branch would provide valid event mappings, safe counter programming,
or a complete top-down model.

A support recheck on October 21 found no remedy in the retrieved
[Apple engineer's forum response](https://developer.apple.com/forums/thread/849179).
A separate participant reports the failure on macOS 27.2 Beta; this is a user
report, not a local test or Apple confirmation. Apple has released
[Xcode 27.1](https://developer.apple.com/news/releases/?id=10152026a), but the
retrieved 27.1/27.2 release notes supplied no M6/KPC fix. Newer Xcode/OS builds were
not installed or tested here. Absence of a release-note entry does not establish
whether a newer build changes behavior.

## Unattended capability probe

One command compiles and launches the fixed-work probe, checks completion and the
known checksum, inspects the exported native data, and prints **only JSON** to
stdout. The output directory must be new:

```sh
.venv/bin/topdown apple probe --mode fixed --output /tmp/m6-counter-check-001
```

The same JSON is retained as `probe.json` beside the source, compiler logs and
capture artifacts. Use `--mode fixed` for the working fixed-counter path,
`--mode cpu` for cycle sampling or `--mode time` for a timer-profile check.
Default mode remains `counters`, which requests the standard CPU Counters
template and is still blocked on this machine. Each invocation performs at most
one recording; there are no automatic retries, GUI interactions or permission
changes. The target has an independent eight-second alarm. Tool calls also have
timeouts, including compile (30 seconds) and record (four seconds plus 30 seconds
for tool completion).

| Exit | JSON `status` | Meaning |
|---|---|---|
| 0 | `counters_present` | Scoped, nonzero native PMC values and probe completion observed |
| 0 | `profile_present` | Selected stack profile contains probe work; checksum/exit verified |
| 1 | `probe_failed` | Build, data, checksum, output or other probe failure |
| 2 | `permission_denied` | Tool explicitly reports a permission denial |
| 3 | `counter_data_missing` | Recorder completed but the required counter table is absent |
| 4 | `tool_unavailable` / `unsupported_host` | Required tooling/template absent or host unsupported |
| 5 | `timeout` | A bounded tool call exceeded its deadline |
| 130 | `interrupted` | Probe interrupted |

Automation should inspect `status`, `exit_code`, `target_completed` and
`sample_count`. Exit zero establishes native observations, not calibrated event
semantics or TMA. `tma_available` remains false and the four TMA values remain null.
Permission classification requires an explicit tool error; an empty counter table
is not silently relabelled as a permissions failure. Evidence is preserved even
when the command returns a nonzero exit status.
`target_completed` means both the expected checksum and successful target exit
were verified; false means unverified. The original three modes were checked on M6:
counters exited 3, CPU Profiler exited 2, and time mode exited 0 with 368 samples.
[Probe validation](apple-probe-validation.json) records hashes and the 419 passing
tests; the original capture validation below remains unchanged.

This automates the CLI capability check. It does not establish whether a separate
interactive Instruments launch would behave differently or fix the macOS KPC issue.

## OS-level follow-up on the tested M6

Rechecked on macOS 27.0.1 (26A434), Xcode 27.0 (27A266a), with Developer Tools
Security already enabled. Root CLI recordings still failed in CPU Profiler and
produced no PMU table with the standard CPU Counters template. Direct KPC fixed
counts worked as root. All 134 installed programmable events were rejected in
both execution scopes through both manual KPC and KPEP apply paths (536 tests),
as were four fully populated configurations. An empty configurable-counter
configuration was accepted.

Offline disassembly of `kernel.release.t8152`, whose UUID matches the running
kernel, shows the setter checking each selected event word's low 16 bits and
returning EPERM for a nonzero selector. That establishes the compiled restriction
behind these failures; the source/build omission that produced this policy remains
unproven. The binary hash and inspection details are retained in the validation
summary. The other shipped `t8132` kernel contains a 104-entry selector lookup
where this `t8152` path rejects nonzero selectors. A custom fixed-cycle PMI request
also failed with `event unavailable`;
the supported fixed-counter sampled path uses a timer trigger.

`kern.cpc.secure` is 1 and its live sysctl flags lack write permission. Apple's
published source makes the control writable only in DEVELOPMENT/DEBUG builds,
and its ARM KPC path returns EPERM for disallowed events. Developer Tools
authorization does not change those build-time restrictions.
[CPC build control](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/kern/cpc.h),
[sysctl implementation](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_cpc.c),
[KPC event check](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/arm64/kpc.c).

SIP and authenticated-root protection remain enabled. No development KDK/kernel
is installed. Booting a custom kernel would require a compatible kernel and
changes to startup security; enabling Developer Tools alone does not provide
that environment. [Apple's startup-security description](https://support.apple.com/guide/security/startup-disk-security-policy-control-sec7d92dc49f/web)
explains that custom XNU kernels use Permissive Security. No kernel, boot-policy,
permission or security-setting change was used for the working CLI paths above.

## Original Instruments template validation

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

Exports select one run (`--run`, default 1). The mode selects the measurement
schema; `trace` also requires its gap table. Exports use the launch PID
from trace metadata; pass `--pid` for an ambiguous or different target. Optional
`--start-ns`/`--end-ns` select a trace-relative, half-open interval. Defaults include
startup and shutdown. Signposts help select an interval in Instruments, but this
implementation does not automatically translate a signpost into the export window.

The output directory contains commands, recorder/export logs, a raw trace for new
captures, `toc.xml`, and `profile.json` when export succeeds. Sampled modes retain
`samples.xml`; trace mode retains `intervals.xml`, `gaps.xml`, and, when available,
`thread-state.xml`. New
captures also retain `capture.json` with executable/collector hashes, tool version,
host metadata and outcome. The JSON includes XML checksums, target exit metadata,
per-row PID/TID/time, and mode-specific native measurements. Sampled modes retain
core labels; trace intervals retain code locations and gap associations.
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

The `cpu-pmc-value` and cycle importers are tested against schema-shaped synthetic
rows, based on the installed Xcode declarations. The fixed `counters-profile`
importer also has a measured M6 fixture and successful native captures.
Programmable-event export remains **unvalidated**. Before enabling derived
bottleneck shares, identify the actual formulas and denominators, and
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
