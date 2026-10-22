# Apple GPU counter experiment

This branch shares bounded ALU and memory-streaming Metal probes and a repeatable Instruments
capture with the M6 session. It is a GPU experiment, separate from the CPU TMA
collector. The commands only write to a new local output directory and do not
change system settings.

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

**Check timestamp overlap, not only row count.** Two fresh recordings that exited
immediately after the probe had counter rows but zero counter intervals overlapping
its labeled GPU passes. The counter stream ended about 31 ms and 80 ms, respectively,
before the first pass. Keeping the recorder alive for one second after the probe
completed produced overlap. On this M1, a four-pass capture then covered all four
passes (12 overlapping counter intervals); a 100-pass capture covered all 100
(284 intervals, 5.37 ms sampled within 34.04 ms GPU work); and a 200-pass capture
covered 199 of 200 (562 intervals, 10.76 ms sampled within 67.53 ms GPU work).
These are sampled portions of labeled work, not complete instruction accounting.

## M1 workload contrast

The `alu` workload performs 1,024 dependent FMA operations per item. The `stream`
workload reads and writes 64 MiB each per pass with one addition per item. Both
verify a deterministic checksum. With the one-second capture tail, the native
counter samples that overlap the labeled passes changed substantially:

| Workload | Passes | GPU work | Sampled work | ALU Limiter | Buffer Write Limiter | GPU Read Bandwidth |
|---|---:|---:|---:|---:|---:|---:|
| ALU | 100 | 34.04 ms | 5.37 ms | 73.61% | 0.11% | 0.18 GB/s |
| ALU | 200 | 67.53 ms | 10.76 ms | 72.92% | 0.11% | 0.09 GB/s |
| Stream | 100 | 225.47 ms | 111.81 ms | 1.33% | 99.44% | 29.60 GB/s |
| Stream | 200 | 466.83 ms | 234.04 ms | 1.61% | 98.26% | 28.93 GB/s |

The table uses overlap-duration-weighted means of **sampled** counter intervals,
not a complete census of each pass. The 100-pass stream capture had no other
active GPU interval overlap in the exported timeline. The 200-pass stream capture
had 17.83 ms of other active GPU interval overlap within 466.83 ms of probe work;
the ALU captures had less than 0.33 ms each. The raw traces remain local. The
sanitized [M1 validation data](apple-gpu-m1-validation.json) includes pass counts,
checksums, coverage, units, and selected sampled means for M6 comparison.

Apple explains that utilization and limiter counters describe related but
different aspects of GPU work, and that a high ALU limiter alone does not prove
a performance problem. The contrast above shows the counter profiles respond to
these two controlled workloads; it does not establish an Intel-style 100% GPU
pipeline-slot partition. See Apple's
[GPU counter walkthrough](https://developer.apple.com/videos/play/wwdc2020/10603/),
[Metal compute guidance](https://developer.apple.com/videos/play/tech-talks/10580/),
and [occupancy analysis](https://developer.apple.com/documentation/xcode/finding-your-metal-apps-gpu-occupancy).

## Reproduce on M1 or test on M6

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
  --launch -- /bin/sh -c '/tmp/topdown-gpu-check-001/probe 4 && sleep 1'

xcrun xctrace export --input /tmp/topdown-gpu-check-001/limiters.trace \
  --toc --output /tmp/topdown-gpu-check-001/toc.xml
xcrun xctrace export --input /tmp/topdown-gpu-check-001/limiters.trace \
  --xpath '/trace-toc/run[@number="1"]/data/table[@schema="metal-gpu-counter-intervals"]' \
  --output /tmp/topdown-gpu-check-001/counters.xml
xcrun xctrace export --input /tmp/topdown-gpu-check-001/limiters.trace \
  --xpath '/trace-toc/run[@number="1"]/data/table[@schema="metal-gpu-intervals"]' \
  --output /tmp/topdown-gpu-check-001/gpu-intervals.xml
python3 examples/apple_gpu/summarize_counters.py \
  /tmp/topdown-gpu-check-001/counters.xml \
  --gpu-intervals /tmp/topdown-gpu-check-001/gpu-intervals.xml
```

The shell exits with the probe's status and waits one second only on success. The
wait gives Instruments time to retain delayed counter samples. Confirm the TOC
says `Counter Set: Performance Limiters`, the probe prints its checksum, and
`probe_coverage.passes_with_counter_samples` is nonzero. `counter_rows` alone
does not establish probe coverage. Use 100 and 200 passes for longer comparison
captures; the four-pass run is a smoke check. Inspect sampled time, pass coverage,
and `other_active_gpu_time_overlapping_ns` before interpreting
`sampled_counter_means`. The latter are weighted only over sampled intervals.
To run the memory control, substitute `probe 100 stream` in the shell command and
use a new trace/output directory. `probe 100 alu` selects the original workload
explicitly.
To try Performance Utilizations, create a second template with `--profile 4` and
capture to another new trace path. Profile numbers 3 and 4 are observations from
Xcode 27.1, not a public compatibility contract; verify the TOC on each Xcode
version. Other numeric profile candidates were unsupported on the tested M1.

For the M6 comparison, record its GPU model, macOS/Xcode versions, counter-set
label, probe checksum and elapsed GPU time, and the summary JSON. Keep the raw
trace and TOC local. If the table is empty or capture fails, preserve the actual
tool error; do not infer that M6 lacks GPU counters from the CPU KPC failure.

Before reporting a GPU bottleneck share, compare idle and matched N/2N probe
captures, check concurrent GPU activity, and confirm each counter's unit and
denominator. The summary script reports row counts, timestamp overlap, and native
sampled means with units. It does not convert them to CPU TMA fractions. For
example, the native `GPU Read Bandwidth` row is in GB/s and has
`is-percentage=0`, despite the generic schema also containing a `percent-value`
column.

Apple's [Metal performance analysis guide](https://developer.apple.com/documentation/xcode/analyzing-the-performance-of-your-metal-app/)
describes selecting Performance Limiters in Instruments. Apple's
[programmatic capture guide](https://developer.apple.com/documentation/xcode/capturing-a-metal-workload-programmatically)
documents a separate GPU trace capture path.
