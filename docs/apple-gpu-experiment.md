# Apple GPU counter experiment

This branch shares a bounded Metal compute probe and a repeatable Instruments
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
captures; the four-pass run is a smoke check. Inspect the sampled time and pass
coverage in each result before interpreting the values.
To try Performance Utilizations, create a second template with `--profile 4` and
capture to another new trace path. Profile numbers 3 and 4 are observations from
Xcode 27.1, not a public compatibility contract; verify the TOC on each Xcode
version. Other numeric profile candidates were unsupported on the tested M1.

For the M6 comparison, record its GPU model, macOS/Xcode versions, counter-set
label, probe checksum and elapsed GPU time, and the summary JSON. Keep the raw
trace and TOC local. If the table is empty or capture fails, preserve the actual
tool error; do not infer that M6 lacks GPU counters from the CPU KPC failure.

Before reporting a GPU bottleneck share, compare idle and matched N/2N probe
captures, check other-process activity, and confirm each counter's unit and
denominator. The summary script reports row counts, names and timestamp overlap;
it deliberately does not calculate bottleneck percentages. For example, the
native `GPU Read Bandwidth` row is in GB/s and has `is-percentage=0`, despite
the generic schema also containing a `percent-value` column.

Apple's [Metal performance analysis guide](https://developer.apple.com/documentation/xcode/analyzing-the-performance-of-your-metal-app/)
describes selecting Performance Limiters in Instruments. Apple's
[programmatic capture guide](https://developer.apple.com/documentation/xcode/capturing-a-metal-workload-programmatically)
documents a separate GPU trace capture path.
