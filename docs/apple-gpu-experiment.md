# Apple GPU top-down experiments

Start with the most expensive GPU passes, inspect Apple's native limiters, change
the workload, and verify both the result and its GPU timing. M6 supports this
workflow through **GPU replay profiling**, even though its Instruments counter
timeline currently exposes little useful compute data. These source-checkout
scripts produce local JSON and readable reports. GPU database/MCP integration is
not included in this experiment.

The commands use new output paths and do not change host security settings.
Raw captures stay local; reviewed M6 measurements are in
[apple-gpu-m6-validation.json](apple-gpu-m6-validation.json).

## M6 finding and useful GPU report

Measured on Apple M6, macOS 27.0.1 (26A434), Xcode 27.1 (27A9275):

| Method | Observed result |
|---|---|
| Public `MTLDevice.counterSets` | `timestamp/GPUTimestamp` only; stage-boundary sampling supported |
| Instruments Metal System Trace | Empty or RT Unit Active only across default, Limiters and Utilizations captures |
| `gpudebug profile run` | Native replay timings, encoder costs, occupancy and limiters; 33 counter groups observed |

The CPU programmable-counter kernel restriction does not prevent this GPU replay
path. The replay captures include the probe's command queue, rather than a
system-wide GPU activity stream.

The original kernel executes 1024 FMAs in a loop. The `alu` control performs the
same 1024 FMAs with fewer loop-control instructions and produces the same
checksum. Four-pass replay observations illustrate the top-down workflow:

| Workload | Original command GPU time | Replay GPU time | Native limiter observation in a profiled encoder |
|---|---:|---:|---|
| `compute` | 0.5820 ms | 0.2661 ms | Control Flow 71.31%; F32 15.70% |
| `alu` | 0.1308 ms | 0.0861 ms | F32 55.86%; Control Flow 1.73% |
| `memory` | 5.4718 ms | 4.4176 ms | Compute Shader Launch 105.14%, flagged as above 100% |
| `stream` | 6.1895 ms | 4.0438 ms | Compute Shader Launch 91.86%; MMU 5.53% |

Compute/ALU use 16,384 floats. Memory/stream use 16,777,216 floats in two shared
buffers; `memory` handles one float per thread, while `stream` handles 64 using
vector operations. Both memory variants apply one FMA per float and pass. All
eight 4/8-pass captures match an independent CPU FMA reference. Original command
times use Metal's command-buffer GPU timestamps; replay times come from the
profiler and must be compared within their own measurement path.

Doubling passes changes replay GPU time from 0.2661 to 0.5395 ms (`compute`),
0.0861 to 0.1830 ms (`alu`), 4.4176 to 8.3792 ms (`memory`), and 4.0438 to
8.1504 ms (`stream`). In a separate unprofiled control with 1,048,576 floats and
four passes, seven alternating trials per mode measured median command GPU times
of 12.904 ms and 2.124 ms respectively, a 6.08× difference. Ranges were
7.802–27.036 ms and 1.624–5.229 ms; frequency and other activity were not fixed.
This supports the loop-control improvement on this host, rather than a portable
speedup guarantee.

**These are overlapping native limiters, not a four-way percentage partition.**
The report retains values above 100% and flags them. Their M6 denominators are
not calibrated here. The memory cases do not establish a DRAM-bound
classification: the native launch limiter dominates both variants. Sampling
coverage also varies: the memory captures report 10/12 and 5/12 sampled cores,
while the compute, ALU and stream captures report 12/12. The report prints this
coverage and preserves all exported counters as native strings.

### Reproduce replay profiling on M6

Use a fresh directory and full Xcode:

```sh
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
mkdir /tmp/topdown-gpu-replay-001
xcrun swiftc -O -framework Metal examples/apple_gpu/probe.swift \
  -o /tmp/topdown-gpu-replay-001/probe

MTL_CAPTURE_ENABLED=1 /tmp/topdown-gpu-replay-001/probe 4 --mode compute \
  --result /tmp/topdown-gpu-replay-001/probe.json \
  --capture /tmp/topdown-gpu-replay-001/compute.gputrace

python3 examples/apple_gpu/replay_profile.py \
  /tmp/topdown-gpu-replay-001/compute.gputrace \
  --output /tmp/topdown-gpu-replay-001/replay
python3 examples/apple_gpu/report_replay.py \
  /tmp/topdown-gpu-replay-001/replay/profile.json
python3 examples/apple_gpu/report_replay.py \
  /tmp/topdown-gpu-replay-001/replay/profile.json --json
```

Repeat with `--mode alu`, `--mode memory --items 16777216`, or
`--mode stream --items 16777216`, and with 8 passes in separate directories.
The default four-pass compute checksum remains `8336.906823405065`, matching the
M1 experiment. `--mode idle` initializes Metal but submits no GPU commands;
use it for Instruments controls, not replay capture.

The collector runs `gpudebug` with default GPU state and overlapping execution.
Use `--execution serial` for a separate serial replay; keep settings matched
when comparing. It creates and terminates its own session, preserves command
logs, and waits at most 30 seconds for the performance tables after Xcode reports
the profile loaded. Missing data and unsupported replay profiling fail clearly.
Saved `profile.json` reports can be read offline on other operating systems.

The probe accepts 1–500 passes, up to 16,777,216 items, and at most 268,435,456
item-passes. Stream item counts must be divisible by 64. Pass counts and GPU
timestamps are recorded in `--result` JSON; checksums are checked against a CPU
reference before success is reported. An existing capture/result is not replaced.

Apple documents [programmatic capture](https://developer.apple.com/documentation/xcode/capturing-a-metal-workload-programmatically),
[the command-line GPU debugger](https://developer.apple.com/documentation/xcode/debugging-with-interactive-command-line-tools),
and [GPU performance counters](https://developer.apple.com/documentation/xcode/analyzing-apple-gpu-performance-using-counter-statistics).
Replay instrumentation and execution settings can change timing; profiler costs
describe the captured workload under those settings.

## M1 finding

On an Apple M1 (7 GPU cores), macOS 27.0.1, Xcode 27.1 (27A9275), the public
`MTLDevice.counterSets` list exposed only `timestamp/GPUTimestamp`. The public
sampling checks accepted stage boundaries, not draw, dispatch, or blit boundaries.
Nevertheless, **Instruments Metal System Trace exported real GPU counter intervals**
when its Performance Limiters profile was selected. A four-pass compute probe
completed with checksum `8336.906823405065` and 1.362 ms summed GPU command time.
The trace's `metal-gpu-counter-intervals` table contained 7,565 rows across 242
sampling intervals. Named counters included ALU Limiter, F32 Utilization, Compute
Occupancy, Buffer Read Limiter, and GPU Read Bandwidth. The Performance Utilizations
profile yielded 5,616 rows across 432 intervals in a separate four-pass capture.
The default CLI Metal System Trace template selected no counter set and produced
zero rows in this table.

`gpudebug profile run` on this M1 reported that its replay profiler requires
Apple M3 or A17 Pro or later. That restriction did not prevent Instruments from
exporting the counter intervals above.

The counter intervals are sampled across GPU activity, not automatically scoped
to our compute passes. Their percentages, bandwidth values, and “Top Performance
Limiter” categories are **not** interchangeable with Intel CPU pipeline-slot TMA
fractions. No bottleneck percentages are claimed from this first capture. Raw
traces stay local because they include activity from other processes.

## Reproduce the Instruments comparison

Use Xcode's developer directory; Command Line Tools alone do not include
Instruments. Set `DEVELOPER_DIR` to the actual Xcode app location if it differs.
The output directory must be new. The template helper copies Apple's installed
template and edits the two `counterprofile` settings in the copy. It fails if the
template layout or defaults differ; it never edits the Xcode installation.

```sh
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
mkdir /tmp/topdown-gpu-check-001
xcrun swiftc -O -framework Metal examples/apple_gpu/probe.swift \
  -o /tmp/topdown-gpu-check-001/probe

python3 examples/apple_gpu/make_counter_template.py \
  /Applications/Xcode.app/Contents/Applications/Instruments.app/Contents/Packages/GPU.instrdst/Contents/Templates/Metal\ System\ Trace.tracetemplate \
  /tmp/topdown-gpu-check-001/limiters.tracetemplate --profile 3

xcrun xctrace record \
  --template /tmp/topdown-gpu-check-001/limiters.tracetemplate \
  --output /tmp/topdown-gpu-check-001/limiters.trace \
  --time-limit 8s --no-prompt --target-stdout - \
  --launch -- /tmp/topdown-gpu-check-001/probe 4

xcrun xctrace export --input /tmp/topdown-gpu-check-001/limiters.trace \
  --toc --output /tmp/topdown-gpu-check-001/toc.xml
xcrun xctrace export --input /tmp/topdown-gpu-check-001/limiters.trace \
  --xpath '/trace-toc/run[@number="1"]/data/table[@schema="metal-gpu-counter-intervals"]' \
  --output /tmp/topdown-gpu-check-001/counters.xml
python3 examples/apple_gpu/summarize_counters.py \
  /tmp/topdown-gpu-check-001/counters.xml
```

Check the TOC's `counter-profile` attribute, the actual counter-name inventory,
and the probe's checksum. Nonzero row counts alone do not establish useful
compute counters: M6 can export thousands of RT-only observations.
To try Performance Utilizations, create a second template with `--profile 4` and
capture to another new trace path. Profile numbers 3 and 4 are observations from
Xcode 27.1, not a public compatibility contract; verify the TOC on each Xcode
version. Other numeric profile candidates were unsupported on the tested M1.

Six additional M6 captures used the default template, profiles 3/4, 128/256-pass
compute controls and an idle Metal process. The TOCs retained the requested
profile IDs, but the counter tables varied between empty and RT Unit Active
only, including under idle. Compute captures contained 128/256 target GPU
intervals; idle contained zero. All included other-process GPU activity.
The evidence records those counts, inventories and source hashes. It does not
establish why Instruments exposes this limited/inconsistent inventory on M6.

Before reporting a GPU bottleneck share, compare idle and matched N/2N probe
captures, align the target's labeled GPU intervals with sample timestamps, check
other-process activity, and confirm each counter's unit and denominator. The
summary script intentionally reports row counts and names only. Use the replay
report above for the measured M6 top-down experiment.

Apple's [Metal performance analysis guide](https://developer.apple.com/documentation/xcode/analyzing-the-performance-of-your-metal-app/)
describes selecting Performance Limiters in Instruments. Apple's
[programmatic capture guide](https://developer.apple.com/documentation/xcode/capturing-a-metal-workload-programmatically)
documents a separate GPU trace capture path.
