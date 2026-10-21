# Apple silicon profiles

Native CLI profiling is available through `topdown apple`. Process-accounted
instruction/cycle totals and fixed-counter samples with stacks work on the tested
M6. These experimental paths remain separate from the Linux slot-TMA database and
funnel. They add no runtime dependencies and do not invoke `sudo` or change host
settings. The tested M6 rejects programmable events; the M1 event mode is
unmeasured. Four-category TMA remains unavailable on both.

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

When `RUSAGE_INFO_V6` is available, `stat` also reports
`performance_instructions` and `performance_cycles` from Apple's performance-core-only
fields, plus performance-core user and system time. The matching `other` counts
and times are aggregate totals minus those performance-core values. On a
two-core-kind M1 these are the efficiency-core remainder; on other chips, retain
the broader `other` label. The selected flavor is recorded in `source`, and older
systems fall back to V4 without these fields. These counts do not reveal per-core
pipeline slots or programmable events.

## M1 top-down feasibility

On an M1 MacBook Air with macOS 27.0.1, launched and PID-interval `stat` counts
worked, including V6 performance-core instructions, cycles, user time and system
time. The `other` remainder was nearly zero in ordinary CPU-bound runs. Running
the same ALU control through `/usr/sbin/taskpolicy -b` moved more than 99.95% of
its counted cycles into `other`, confirming the M1 efficiency-core interpretation
for this host. Three runs gave median 6.645 instructions/cycle on P cores and
3.312 on E cores. These are instruction-throughput observations, not pipeline-slot
shares; both include process startup and any kernel work. The full controls and
counts are in [M1 validation](apple-m1-validation.json).

The M1 KPC interface reports two fixed and eight configurable counters. The
installed event database has 58 programmable events. Their names and selectors
match the T8103 allowlist in [Apple's XNU source](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/arm64/cpc_arm64_events.c).
The running T8103 kernel has the same UUID as the locally inspected kernel
binary. All 58 installed programmable name/selector pairs occur in its event
table. Offline disassembly of its `kpc_set_config` path shows a nonzero event
selector compared against this table and an error for unmatched selectors.
Published XNU code sets a release kernel's default policy to restrict events
to this known list
([policy definitions](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/arm64/cpc_arm64.h),
[secure-build control](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/kern/cpc.h)).
The compiled gate recognizes the named events; an authorized collection has
not yet been verified. This account's direct KPC configuration and counter
reads returned `EPERM`; root was unavailable. Apple's
[KPC sysctl implementation](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_kpc.c)
checks ktrace access (root or the blessed PID) before those operations, while
counter-count queries are public. The unprivileged `EPERM` therefore does not
show that the M1 event selector itself was rejected. The host has Command Line
Tools but no full Xcode, so `xctrace` and `instrumentbuilder` could not be run.
An authorized root KPC/PET collector is a second way to measure these events;
the original [mperf implementation](https://github.com/tmcgilchrist/mperf)
documents a `sudo` requirement and timer-sampled per-thread snapshots. Its
first/last deltas can miss short-lived threads, so whole-run values would still
need checks against Recount and long, stable controls. Root access was not
available for this investigation.
Apple introduced the guided CPU Bottlenecks mode in
[Xcode 26](https://developer.apple.com/documentation/xcode-release-notes/xcode-26-release-notes);
the available public documentation does not state whether that mode produces
counter data on M1. Its initial Xcode 26 release notes flag missing macOS data
in the separate “Instruction Processing Bottlenecks” and “Streaming SME
Bottlenecks” modes. A recording on this host is needed to establish support for
the initial four-category mode. Apple's [Processor Trace hardware
requirements](https://developer.apple.com/documentation/xcode/analyzing-cpu-usage-with-processor-trace)
start at M4 for Mac recording, so Processor Trace cannot supply a hardware
execution trace on this M1.
Apple's [CPU counter subsystem overview](https://github.com/apple-oss-distributions/xnu/blob/main/doc/observability/cpu_counters.md)
identifies KPC as the manager of configurable core counters; Monotonic and
Recount expose fixed instructions/cycles. Those fixed-counter paths cannot
provide the missing pipeline events.

### Experimental M1 event collection

The local KPEP library accepted the following group without enabling any PMU
counters. Run `python3 examples/apple-m1-kpep-plan.py` on an M1 to reproduce its
counter assignments. The command fails on a different CPU family.

| Counter | Event | What its database description measures |
|---:|---|---|
| 0, 1 | `FIXED_CYCLES`, `FIXED_INSTRUCTIONS` | Fixed cycles and retired instructions |
| 2 | `SCHEDULE_UOP` | Uops issued to execution units |
| 3 | `CORE_ACTIVE_CYCLE` | Cycles while the core was active; compare with fixed cycles to establish the denominator |
| 4 | `MAP_DISPATCH_BUBBLE` | Cycles with no map input and no map stall |
| 5 | `BRANCH_MISPRED_NONSPEC` | Architecturally executed mispredicted branches |
| 6 | `MAP_STALL_DISPATCH` | Cycles with dispatch backpressure at map |
| 7 | `RETIRE_UOP` | Retired uops |
| 8 | `L1D_CACHE_MISS_LD` | Load cache misses |
| 9 | `L1I_CACHE_MISS_DEMAND` | Demand instruction-cache misses |

`RETIRE_UOP` and `INST_ALL` both require configurable counter 7 and KPEP rejected
them in one configuration. The separate fixed instruction counter avoids that
collision. [Linux's M1 PMU driver](https://github.com/torvalds/linux/blob/master/drivers/perf/apple_m1_cpu_pmu.c)
independently documents the counter restrictions. KPEP acceptance only proves a
configuration can be constructed; it does not prove that Instruments or the
kernel will count these events on this host.
Apple's [Xcode 14.3 release notes](https://developer.apple.com/documentation/xcode-release-notes/xcode-14_3-release-notes/)
record a fix for `RETIRE_UOP` failing to count on Apple silicon in an older CPU
Counters instrument. Positive native values still need verification with the
actual Xcode build.
The installed `MAP_LDST_UOP` description also mentions SME instructions, while
`hw.optional.arm.FEAT_SME` is zero on this M1. Treat the database text as a
candidate description shared across chips, not proof of every M1 event detail.

A second KPEP plan replaces branch, cache and scheduler diagnostics with map
pipeline events. Run `python3 examples/apple-m1-kpep-plan.py --group map-state` to
inspect its assignments. It places `MAP_DISPATCH_BUBBLE`, `MAP_STALL`,
`MAP_REWIND`, `MAP_STALL_DISPATCH`, `MAP_INT_UOP`, `MAP_LDST_UOP`,
`MAP_SIMD_UOP`, and `RETIRE_UOP` on all eight configurable counters, alongside
the two fixed events. `MAP_REWIND` counts cycles blocked during a flush/restart;
it does not label the cause of the flush. `MAP_STALL` is for any map stall, so
`MAP_STALL_DISPATCH` may be a subset. The three mapped-uop classes have no
published guarantee that they exhaust map throughput or correspond one-to-one
with retired uops. In [Dougall Johnson's Firestorm NOP
measurements](https://dougallj.github.io/applecpu/measurements/firestorm/NOP.html),
a stream of roughly 80,000 retired NOP uops had only 200 `MAP_INT_UOP`
counts and no reported load/store or SIMD map-class counts. Those execution-unit
classes omit eliminated NOP uops, even without meaningful bad speculation.
A separate experimental `m1-map` package requests this group;
each recording uses one group and retains raw vectors.

A third KPEP-accepted group, `fetch`, pairs `L1I_CACHE_MISS_DEMAND`,
`L1I_TLB_MISS_DEMAND`, `L2_TLB_MISS_INSTRUCTION`, and `FETCH_RESTART` with
`SCHEDULE_EMPTY`, `MAP_DISPATCH_BUBBLE`, `RETIRE_UOP`, and `CORE_ACTIVE_CYCLE`.
It keeps fixed cycles and instructions in the same ten-event configuration.
Run `python3 examples/apple-m1-kpep-plan.py --group fetch` to inspect its
counter assignment; `m1-fetch` requests the matching Instruments package.
This group is designed to test whether the footprint sweep below produces
instruction-cache misses while instruction-TLB misses stay low. The two empty
pipeline signals and fetch restarts are diagnostics, not a slot-width or
front-end-bound percentage; the events' scopes and relationships need hardware
calibration. KPEP accepted this group offline, but no capture was available.

An experimental `m1` mode requests this exact order through a local Instruments
package and exports the raw counter vector with the stack and core label:

```sh
xcrun clang -O3 -g examples/apple-m1-controls.c -o /tmp/apple-m1-controls
poetry run topdown apple probe --mode m1 --output /tmp/m1-event-probe-001
poetry run topdown apple record --mode m1 --duration 5 \
  --output /tmp/m1-event-alu-001 -- /tmp/apple-m1-controls alu_wide 4000000000
poetry run topdown apple probe --mode m1-map --output /tmp/m1-map-probe-001
poetry run topdown apple record --mode m1-map --duration 5 \
  --output /tmp/m1-map-alu-001 -- /tmp/apple-m1-controls alu_wide 4000000000
poetry run topdown apple probe --mode m1-fetch --output /tmp/m1-fetch-probe-001
```

The output directory must be new. All three M1 modes require full Xcode's `xctrace`
and `instrumentbuilder`; on the tested host their probes return `tool_unavailable`
before capture. No M1 programmable values have yet been observed. The parser
validates the trace's configured event order and timer trigger, retains each
uint64 vector position without assigning an event name, and never adds sampled
rows or reports TMA percentages. The exact mapping from vector positions to
configured events needs calibration; KPEP's physical counter mapping alone does
not establish Instruments' export order. The M1 probes require at least three
positive vector positions in one sample containing the control function, beyond
what the two fixed events alone can explain. The packages are scoped to one explicit
target. The controls include wide ALU, serial dependence,
predictable and unpredictable branches, and random pointer chasing; each prints
a deterministic checksum. Use the matching `apple stat` capture and its V6
P/other split to reject runs with appreciable core migration.

An independent [Xcode counter calibration report](https://tahrioui.com/blog/no-ebpf-for-macos)
found unnamed vectors, counter resets between samples, and absolute totals that
did not match a known instruction count on another Apple chip. Its recording
used Xcode's guided CPU Bottlenecks mode, whose exported values include derived
metrics, so its vector interpretation cannot be copied to these custom raw-event
packages. Treat each raw M1 vector as an observation until its configured event
order, cumulative/delta behavior and reset points are checked against `stat`.
If `record` reports a tool error but retains a trace,
inspect its logs and try `topdown apple export` into a new directory; a trace's
existence alone does not qualify the counter data.

For the instruction-cache event, compile a separate large-code control:

```sh
xcrun clang -O3 -g examples/apple-m1-icache.c -o /tmp/apple-m1-icache
xcrun clang -O3 -g -DTOPDOWN_SMALL_ICACHE examples/apple-m1-icache.c \
  -o /tmp/apple-m1-icache-hot
xcrun clang -O3 -g -DTOPDOWN_NOP_BLOCK=40960 -DTOPDOWN_NOP_GROUPS=1 \
  examples/apple-m1-icache.c -o /tmp/apple-m1-icache-160k
poetry run topdown apple stat --output /tmp/m1-icache-stat-001 \
  -- /tmp/apple-m1-icache 200000
poetry run topdown apple stat --output /tmp/m1-icache-hot-stat-001 \
  -- /tmp/apple-m1-icache-hot 200000
poetry run topdown apple stat --output /tmp/m1-icache-160k-stat-001 \
  -- /tmp/apple-m1-icache-160k 320000
poetry run topdown apple record --mode m1 --duration 10 \
  --output /tmp/m1-icache-events-001 -- /tmp/apple-m1-icache 200000
poetry run topdown apple record --mode m1-fetch --duration 10 \
  --output /tmp/m1-fetch-icache-001 -- /tmp/apple-m1-icache 200000
poetry run topdown apple record --mode m1-fetch --duration 10 \
  --output /tmp/m1-fetch-icache-hot-001 -- /tmp/apple-m1-icache-hot 200000
```

The default function contains 65,536 `nop` instructions. Smaller builds repeat
a compact `nop` block to keep nominal work around 13.107 billion NOPs per
capture; nondivisor block sizes use `TOPDOWN_NOP_GROUPS=1` and a matching
repetition count recorded in the validation JSON. The 160 KiB build uses
320,000 repetitions. This host reports
196,608 bytes of P-core L1I and 131,072 bytes of E-core L1I through
`hw.perflevel*.l1icachesize`. Three fresh `stat` runs of each of 18 builds on
each core kind produced:

| NOP block | Compiled text bytes | P-core median IPC | E-core median IPC |
|---:|---:|---:|---:|
| 256 | 1,300 | 7.945 | 3.963 |
| 8,192 | 33,044 | 7.959 | 3.963 |
| 16,384 | 65,812 | 7.970 | 3.955 |
| 32,768 | 131,348 | 7.955 | 3.883 |
| 33,024 | 132,372 | 7.955 | 3.665 |
| 33,792 | 135,444 | 7.949 | 3.117 |
| 34,816 | 139,540 | 7.952 | 2.645 |
| 36,864 | 147,732 | 7.956 | 2.071 |
| 38,912 | 155,924 | 7.945 | 2.074 |
| 40,960 | 164,116 | 7.957 | 2.073 |
| 49,152 | 196,884 | 7.881 | 2.073 |
| 49,408 | 197,908 | 7.218 | 2.073 |
| 50,176 | 200,980 | 5.690 | 2.073 |
| 51,200 | 205,076 | 4.478 | 2.074 |
| 53,248 | 213,268 | 3.190 | 2.074 |
| 57,344 | 229,652 | 2.140 | 2.073 |
| 61,440 | 246,036 | 2.164 | 2.073 |
| 65,536 | 262,420 | 2.159 | 2.074 |

The 128 KiB NOP-block row uses two inner-loop passes. An additional control
used one pass and 400,000 repetitions, matching the just-over-128 KiB builds'
loop structure and nominal NOP work. Its three-run medians were 7.954 IPC on P
cores and 3.888 on E cores, close to the original 128 KiB row; the next 129 KiB
block still fell to 3.665 E-core IPC. Its six raw counts and binary hash are in
the validation JSON.

The E-core throughput starts falling between 128.3 KiB and 129.3 KiB of text,
then reaches about 2.07 IPC by 144.3 KiB. P-core throughput starts falling
between 192.3 KiB and 193.3 KiB, then reaches about 2.15 IPC by 224.3 KiB.
The three-run IPC ranges on each side of both initial steps do not overlap.
The boundaries are consistent with the reported L1I capacities, and the
opposite core kind stays near its prior throughput at each initial step.
The NOP block itself is exactly
128 or 192 KiB at the respective near-limit points; the text section also
contains loop and other program code. The host uses 16 KiB pages, so even the
largest text section touches at most 18 pages regardless of alignment. An
independent [M1 ITLB measurement](https://jia.je/hardware/2024/12/26/apple-m1/)
places the P/E L1 instruction TLB capacities at 192/128 pages. A simple ITLB
capacity limit therefore does not explain the observed drops, although this
experiment does not exclude translation conflicts or other fetch effects.
All 108 runs completed with the expected checksum,
and at least 99.4% of cycles were accounted to the intended core kind. Smaller
blocks execute more loop branches, and process startup, frequency and thermal
state also affect the fixed counts. An L1I miss count still needs an authorized
programmable-counter capture. [M1 validation](apple-m1-validation.json)
summarizes the points and binary hashes; [raw sweep
counts](apple-m1-icache-sweep.csv) retain every run. The validation JSON also
retains an earlier two-size comparison.
The compact throughputs are consistent with the independently measured eight-
and four-uop P/E limits; fixed *instruction* counts and process overhead do not
prove a slot denominator or four-way partition. The `nop` stream is a fetch
footprint control.
[Firestorm measurements](https://dougallj.github.io/applecpu/firestorm.html)
find that `nop` retires without issuing to an execution unit, so this workload
must not calibrate useful work from scheduler issues or retired instructions.

### What a full four-category result still needs

Intel's [Top-Down Microarchitecture Analysis Method](https://www.intel.com/content/www/us/en/docs/vtune-profiler/cookbook/2024-1/top-down-microarchitecture-analysis-method.html)
classifies pipeline slots as Retiring, Frontend Bound, Backend Bound, or Bad
Speculation. The M1 events above provide useful signals but no established
disjoint four-way slot classification. For example, `BRANCH_MISPRED_NONSPEC`
counts branches, not the slots lost during their recovery. `MAP_DISPATCH_BUBBLE`
counts fully empty map cycles, not all empty delivery slots. `SCHEDULE_UOP`
counts execution-unit issues, not all allocated uops:
[Firestorm measurements](https://dougallj.github.io/applecpu/firestorm.html)
show instructions whose issue and retired-uop counts differ. Subtracting
`RETIRE_UOP` from `SCHEDULE_UOP` therefore does not measure bad speculation.
The same research finds an eight-uop P-core pipeline limit and [up to four
retired uops per E-core cycle](https://dougallj.github.io/applecpu/icestorm.html).
These black-box throughput limits do not establish which pipeline stage defines
the slot denominator for mixed P/E execution or prove the observable fixed-cycle
event matches the relevant active pipeline cycles.

Arm's [Neoverse V1 top-down methodology](https://documentation-service.arm.com/static/668cffe969e89f01e39c7ba7)
illustrates the missing information: its four-way formulas require a documented
slot width, speculative and retired operation counts, total/front-end/back-end
stalled slots, and a flush-recovery correction. The installed M1 database has no
events named `OP_SPEC` or `STALL_SLOT_*`; `RETIRE_UOP` is only a candidate for
Arm's retired-operation count. M1's cycle-level map signals cannot be inserted
into those slot formulas without proving their widths, overlap and recovery
semantics. This comparison does not rule out a different M1-specific formula.
The M1 database also lacks `MAP_UOP` and `MAP_DISPATCH_BUBBLE_SLOT`, though
both names occur in other event catalogues shipped on this macOS installation.
The three M1 mapped-uop class counters and its *cycle*-level
`MAP_DISPATCH_BUBBLE` need separate validation; those slot-level event names
cannot be assumed to describe M1 hardware.
An undocumented `dispatch uop` selector `0x78` appears in independent
[Firestorm measurements](https://dougallj.github.io/applecpu/measurements/firestorm/NOP.html),
but it is absent from the installed M1 event database and Apple's
[T8103 release-kernel event list](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/arm64/cpc_arm64_events.c).
The release kernel's known-event policy rules it out as a stock macOS fallback.

| TMA category | M1 signal to investigate | Missing measurement or proof |
|---|---|---|
| Retiring | `RETIRE_UOP` | Per-core-kind slot denominator, counter scope and retire width under each workload |
| Frontend Bound | `MAP_DISPATCH_BUBBLE`, instruction-cache/TLB misses, `SCHEDULE_EMPTY` | Partially empty delivery slots; cycle events and misses cannot identify every empty slot or its cause |
| Bad Speculation | `BRANCH_MISPRED_NONSPEC`, `FLUSH_RESTART_OTHER_NONSPEC`, `MAP_REWIND` | Wasted speculative uops plus recovery slots; these count flushes or rewind cycles, not all lost slots |
| Backend Bound | `MAP_STALL_DISPATCH`, `MAP_STALL`, cache/TLB misses | Partial backend stall slots and non-overlap with rewind/front-end stalls |

The controls group also pairs `CORE_ACTIVE_CYCLE` with `FIXED_CYCLES` in one
configuration. Compare their ratio across idle, ALU, pointer-chase and branch
controls before using either for the slot denominator. The event's database
description alone does not establish whether either count is thread-scoped,
core-scoped, or equivalent to the cycles relevant to a TMA stage.
An independent [M1/Asahi Linux counter experiment](https://blog.cyyself.name/tso-apple-m1/)
observed `CORE_ACTIVE_CYCLE`, `MAP_STALL`, `MAP_STALL_DISPATCH`, and
`MAP_REWIND` change with memory-ordering behavior. In its two workloads,
`MAP_STALL_DISPATCH` was close to `MAP_STALL`; that is a useful directional
check for the map-state group, not a universal equality or a macOS calibration.
Its reported active cycles per instruction are consistent with reciprocal IPC
from `perf` cycles/instructions within the rounding of the reader and writer
cases. This makes `CORE_ACTIVE_CYCLE` a credible M1 denominator candidate, but
its scope and equivalence to fixed cycles still need a same-run macOS check.

The map-state group provides a direct experiment for overlap: compare
`MAP_STALL` with `MAP_STALL_DISPATCH` and `MAP_REWIND`, and compare the sum of
mapped-uop classes with retired uops on the same P-core controls. The NOP
counterexample above already rules out using that sum as a general all-uop
counter or subtracting it from `RETIRE_UOP` to estimate discarded work. Any apparent
cycle partition must also account for cycles in which map is processing uops,
partially filled map cycles, counter sampling scope and core migration. A
cycle count for "no uops" cannot by itself reveal how many of the other
cycles delivered fewer than the pipeline's possible uops. An event plan
accepted by KPEP is not evidence that these identities hold on hardware.

Use the three packages as paired experiments on stable workloads. Each capture
has its own event group, so compare normalized responses across repeated runs
rather than subtracting one group's raw sample from another's. The immediate
checks are:

| Control comparison | Event response to check | Interpretation if absent |
|---|---|---|
| Random versus predictable branch | Mispredicted branches in `m1`; `MAP_REWIND` in `m1-map` | Recheck the executed branch, vector positions, event support and sample scope |
| Pointer chase versus wide ALU | Load-cache misses in `m1`; map backpressure in `m1-map` | Recheck cache footprint and distinguish memory latency from unrelated stalls |
| Large versus compact `nop` stream | L1I misses versus instruction-TLB misses in `m1-fetch`, plus map bubbles and scheduler-empty cycles | Recheck the instruction footprint, translation behavior, vector mapping and sample scope |
| Wide ALU on P versus E cores | Retired/mapped uops, fixed cycles and Recount core-kind split | Keep separate denominators; reject captures with substantial migration |

On the same map-state samples, check whether `MAP_STALL_DISPATCH` is bounded by
`MAP_STALL`, whether `MAP_REWIND` overlaps those cycles, and whether the three
mapped-uop classes account for the observed ALU work. A directional response
is evidence for an event's sensitivity, not a slot-classification formula.

Before enabling `tma_available`, collect actual programmable values on M1 and
check vector order, scope, sampling semantics, simultaneous availability, loss
and counter multiplexing. Calibrate each event against the control workloads,
including P-only and E-only runs. Establish per-core-kind slot widths and
event formulas with explicit overlap rules, then verify that the four shares
sum to 100% across independent controls and correlate with an authorized
Instruments CPU Bottlenecks recording. Apple's [CPU Bottlenecks
categories](https://developer.apple.com/documentation/xcode/addressing-cpu-bottlenecks)
partition maximum sustainable CPU bandwidth into Useful, Instruction Delivery,
Instruction Processing and Discarded; their correspondence to Intel slot TMA
must be tested rather than assumed. Until then, native event names and units
remain separate from the Linux TMA database and funnel.
Apple's [CPU Optimization Guide](https://developer.apple.com/documentation/apple-silicon/cpu-optimization-guide)
advertises event and metric lists but requires a registered developer account
and a separate agreement for the full guide. Public Xcode documentation does
not provide a validated M1 formula for this four-way partition.

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
The unattended `apple probe` command does not support `trace`; use
`record --mode trace` with an explicitly bounded target.

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
new product command. Serial/parallel arithmetic controls produced different
native instruction/cycle ratios, which cannot identify a unique stall cause.
See [follow-up validation](apple-m6-trace-validation.json) for the measured controls
and limitations.

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
`samples.xml`; trace mode retains `intervals.xml` and `gaps.xml`. New
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
