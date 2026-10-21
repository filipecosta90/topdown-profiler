# Resume the M6 development-kernel experiment

The matching M6 development-kernel collection has been generated and inspected.
At the last checkpoint, the Mac was still running the release kernel; the candidate
had not been activated or booted. The KDK is installed. The next action is the
locally authorized Recovery boot test, followed by read-only verification of the
actual running kernel and a scoped programmable-counter experiment.

Read this document from any computer, but run the commands on the original M6
Mac. The helper, collection and raw evidence are local artifacts; cloning the
repository on another machine does not install or transfer them. The instructions
below cover Recovery entry, unlocking Data, activation and planned restoration
without needing to open a local handoff file from the other computer.

## Recovery boot with a persistent log

On this Mac, the reviewed local activation helper is staged at
`/Users/oly/m6-boot.sh`. Recovery lacks the `shasum` tool it expects. Put the
[Recovery wrapper](../examples/m6-recovery-boot-wrapper.sh) on a USB drive or
download it into Recovery's `/tmp` from the reviewed commit. Shut down, hold
the power button to enter the paired Recovery environment, open Terminal,
and run:

```sh
sh /path/to/m6-recovery-boot-wrapper.sh
```

The wrapper can start before Data is mounted.
It asks for the Data and System disk identifiers, unlocks or mounts Data with
Apple's native prompt, and verifies that the volumes share a group. It then
mounts System, self-tests a SHA-256 tool from Recovery or the installed OS,
checks the reviewed helper and collection hashes, and calls the staged local
helper with a compatible `shasum -a 256` command. Any unavailable tool or
mismatch stops before the staged helper changes boot policy.

The wrapper prints its persistent log path under
`/Users/oly/redislabs/topdown-profiler/.local-runs/m6-kdk-20261021T091524Z/`
and gives the log to the normal account. The same file is available from
Recovery under Data's mount point if the experimental boot fails. The staged
helper still owns the exact machine-specific checks, native owner prompts,
`csrutil disable`, `kmutil configure-boot`, and reboot. The wrapper only runs
that helper after the checks above pass. Its preflight checks are written and
flushed to the persistent log before activation. Native authentication prompts
and helper output remain on the Recovery terminal; the staged helper saves its
own noncredential checkpoints for the activation steps. An immediate reboot can
leave the wrapper status at `phase=activation-pending`.

The helper verifies the Mac model, System/Data UUIDs and volume group, target OS
build, and collection SHA256. It mounts the verified System volume, runs the
approved `csrutil disable` and `kmutil configure-boot` steps with native interactive
prompts, then reboots only if both succeed. Select the verified target OS in the
prompts. Physical Recovery entry and owner authentication remain necessary.
If Data is locked or the helper path is missing, use Recovery Terminal to inspect
the volume groups:

```sh
diskutil apfs listVolumeGroups
```

Identify the original Mac's System/Data pair. Substitute its Data device identifier
from that output, such as `disk3s5`, into these commands:

```sh
diskutil apfs unlockVolume <Data-device-identifier>
diskutil info <Data-device-identifier>
```

Enter the FileVault credentials only at Apple's native prompt. If Data is already
unlocked but unmounted, use `diskutil mount <Data-device-identifier>` instead.
Read the actual mount point from `diskutil info`; substitute it for `/Volumes/Data`
in the helper command if different. Stop if the expected Data volume or helper is
absent. The helper independently verifies the exact authorized volume UUIDs before
changing security settings.

The preserved source and mock tests are `m6-boot.sh` and `test_m6_boot.py` in the
local artifact directory below. Nineteen local checks pass; actual Recovery
activation and boot remain untested. A failure can leave earlier security changes
in place; use the existing restoration plan if abandoning the experiment.
The helper saves noncredential activation checkpoints before reboot.
Seven independent scoped reviews recommend proceeding with this experimental
activation after the fixes; their portable results are recorded below. The detailed
machine-local review record is `seven-agent-review.json`.

Returning to Full Security requires an online signing check and may be refused
for an older OS build. The local plan includes an untested fallback to stock boot
under Reduced Security with SIP enabled. That fallback does not restore the
original Full Security setting; neither restoration path is guaranteed.

## Planned return to stock boot

These procedures have been reviewed but have not been tested on this Mac. Enter
the same physically selected paired Recovery environment and open Terminal.
Use `diskutil apfs listVolumeGroups` to identify the original System/Data pair.
Set the variable below to its verified volume-group UUID; it must match the
`TD_GROUP_UUID` assignment in the staged helper. Machine-specific volume UUIDs
remain in the local files rather than this public document.

```sh
TD_GROUP_UUID="<verified-volume-group-UUID>"
csrutil enable
bputil -f -v "$TD_GROUP_UUID"
```

Select the same target OS in Apple's prompts and authenticate locally. Full
Security disallows the custom boot object. If both operations succeed, inspect
`bputil -d -v "$TD_GROUP_UUID"` and `csrutil status`, then reboot manually.

`bputil -f` performs an online signing check: Recovery needs network access, and
Apple may refuse Full Security for an OS build that is no longer eligible. If it
fails, retain the exact error. The reviewed fallback attempts stock boot under
Reduced Security:

```sh
bputil -g -v "$TD_GROUP_UUID"
csrutil enable
bputil -d -v "$TD_GROUP_UUID"
csrutil status
```

Use plain `-g` without additional downgrade options. The installed `bputil(1)`
manual documents that this recreates LocalPolicy with only the requested options;
it permits stock Apple global signatures under Reduced Security and removes
custom-boot permission. Inspect the actual policy for Reduced Security without
custom-boot permission and SIP enabled. Stop on a failed policy-changing operation
and retain the error. Reboot manually only after successful changes.

After either restoration path, verify:

```sh
sysctl kern.osbuildconfig kern.uuid
csrutil status
csrutil authenticated-root status
```

Expected stock variant: `release`; running kernel UUID:
`77F5F1F1-C717-3A42-A5FA-487ACF280495`; SIP and Authenticated Root enabled.
The Reduced Security fallback does not restore original Full Security. Do not
automatically upgrade/reinstall macOS or add unrelated security changes if
restoration fails. Custom boot may withhold protected decryption keys or prevent
normal login; successful restoration is not guaranteed. See the installed
`bputil(1)` manual and [Apple's LocalPolicy documentation](https://support.apple.com/guide/security/secc745a0845/web).

## Seven-review checkpoint

Seven independent subagents reviewed this exact authorized experiment on
2026-10-21. Each recommended proceeding with the bounded experimental boot after
the fixes below. Their agreement does not establish bootability, valid counters,
successful restoration or full TMA.

| Review scope | Final verdict |
|---|---|
| Exact-target boot safety | Proceed with the experiment |
| Shell failure handling | Proceed after fix and staged-copy verification |
| Recovery compatibility | Proceed with physical entry and native authentication |
| Kernel provenance and collection compatibility | Proceed; runtime bootability remains unknown |
| Native counter probe | Proceed after hardening and the postboot runtime gate |
| Measurement validation | Proceed with exploratory controls; independent confirmation required |
| Stock-boot restoration | Proceed after documenting signing limits and fallback |

Resolved findings:

- A failed post-mount `diskutil` query could be hidden by a successful parser.
  The helper now captures the command separately, rechecks mounted volume identity,
  and has a regression test for valid output accompanied by a failing exit status.
- The helper checks required tools, prints the target group, saves noncredential
  phase checkpoints, and preserves failure/interruption exit statuses.
- The C probe now validates getter errors, masks, counts and mapping bounds;
  checks interruption between setup stages; attempts restoration after partially
  failed setters; and reads back restored state. It was compiled without running
  the real probe.
- The counter runner enforces actual running-kernel/build/UUID/policy checks,
  preserves three sequential public `INST_ALL` controls, validates programmable
  mapping, checksums, cleanup, positive adjusted counts and exploratory scaling,
  and saves failures without automatically retrying.
- Full Security's online eligibility limit and the Reduced Security stock-boot
  fallback are documented above.

Validation: **702 tests passed** (653 existing, 19 boot-helper mocks, 20 runner
mocks, 10 native-probe mock tests); Ruff and shell syntax passed. A read-only test
on the current release kernel confirmed that the runner refused to launch the
probe. No real counter programming, startup/security change or reboot was performed.
The staged helper's SHA256 at this checkpoint is
`876c08b94afe748117f633d875fbab64c239ca53370270d3bd7afa90ac2d3f18`.

## Start here after reboot

On the same Mac and checkout:

```sh
cd /Users/oly/redislabs/topdown-profiler
cat .local-runs/m6-kdk-20261021T091524Z/SESSION-HANDOFF.md
.venv/bin/python .local-runs/m6-kdk-20261021T091524Z/resume-status.py
```

The checker saves JSON under a new `resume-status-*` directory. It reads the
running kernel variant/UUID, OS build, CPC policy, SIP/Authenticated Root state,
Git state and candidate hash. It performs no counter configuration or boot/security
change. A development-kernel file on disk is not proof that it is running.

Expected candidate identity:

| Item | Value |
|---|---|
| Target | Mac18,5 / T8152, macOS 27.0.1, build 26A434 |
| Running variant after a successful test boot | `development`, `DEVELOPMENT_ARM64_T8152` |
| Development kernel UUID | `DB731926-AEDA-363D-A653-49575E91EE34` |
| Candidate | `M6-development-26A434.kc`, 133,431,296 bytes |
| Candidate SHA256 | `714872ac8f6eb514056790110b635bb90bc1c0a1122cbe21940687d09191f112` |
| Initial policy in the development file | Enum 1; inspect actual `kern.cpc.secure` after boot |

If the checker still reports the release kernel, investigate the activation/boot
outcome before repeating any counter sweep. If build/model/UUID differs, resolve
that mismatch before programming counters.

## Handoff files on this Mac

The local artifact root is
`.local-runs/m6-kdk-20261021T091524Z/`:

| File | Purpose |
|---|---|
| `SESSION-HANDOFF.md` | Complete context, prior experiments, results, constraints, next tests and artifact map |
| `RECOVERY-BOOT-PLAN.txt` | Exact target-volume checks, Recovery commands and planned stock-boot restoration |
| `authorization.json` | User authorization and its exact local scope; authorization does not imply execution |
| `session-state.json` | Last checkpoint and remaining physical steps |
| `resume-status.py` | Read-only postboot snapshot and candidate checks |
| `m6-boot.sh`, `test_m6_boot.py` | Preserved activation helper and mocked regression tests |
| `seven-agent-review.json` | Seven review verdicts, fixes, validation and artifact hashes |
| `run-scoped-counter.py`, `test_scoped_counter.py` | Runtime gate, sequential controls and mocked tests |
| `event-probe.c`, `event-probe-mock.c`, `test_event_probe.py` | Hardened native probe and mocked control-flow tests |
| `pre-reboot-status.json` | Baseline immediately before the handoff |
| `M6-development-26A434.kc` | Final verified collection; earlier collection outputs are superseded |
| `latest-collection-result.json` | Final native build/inspection result |
| `resume-integrity.json` | SHA256 manifest of the preserved handoff materials |
| `resume-bundle.zip` | Local archive of the handoff, final collection, probe and essential evidence |

`.local-runs/` is ignored through this checkout's `.git/info/exclude`. These files
persist across a normal reboot, but are not included in a fresh clone. The archive
stays local and contains no passwords. Machine-specific volume identifiers are in
the local handoff, not this public document. A different Mac or candidate requires
its own authorization and compatibility checks.

Recovery entry and owner authentication require the user's physical participation.
The recorded authorization covers this specific test; do not ask for the same
approval again when resuming it. Follow the local plan and record actual outcomes.
The proposed restoration procedure is documented but has not been tested here.

## Verified results and limits

The enclosing KDK installer verifies as signed Apple Software. Its T8152 release
kernel is byte-identical to the installed release kernel. Offline disassembly and
independent Mach-O decoding verify that the unmodified development binary has a
writable CPC policy control. The final custom collection links and inspects
successfully. All 308 required manifest entries are present, and its 387 driver
identities and versions match stock.

None of these checks establishes boot success or valid programmable event counts.
The KDK README says installing its variants on Apple silicon is unsupported. The
custom-boot mechanism requires Recovery and Permissive Security. See the
[reviewed KDK evidence](apple-m6-kdk-validation.json) and
[Apple's boot-security documentation](https://support.apple.com/guide/security/startup-disk-security-policy-control-sec7d92dc49f/web).

After verifying the running development kernel and CPC state, the next measurement
is one public `INST_ALL` event through the staged native `event-probe`, with
idle/N/2N controls, verified checksums and restoration of prior counter state.
The detailed local handoff records commands and failure handling. A successful API
read alone is insufficient: check nonzero work counts and scaling. Full TMA remains
unavailable until the event meanings and formulas have been validated.

The local `run-scoped-counter.py` runner enforces the snapshot's identity/policy
checks before launching the probe and preserves three sequential baseline/N/2N
captures. It rejects unexpected programmable mappings, checksum/cleanup failures,
zero work counts and scaling outside an exploratory tolerance. This is a first
measurement check; independent confirmation and formula validation remain required.

The existing `apple stat`, fixed-counter stack sampling, Processor Trace/scheduling
annotations, and system/cluster `apple soc` histograms remain useful independently.
Preserve their native units, scope, trace gaps and null TMA roots. Prior work includes
234 randomized firmware controls, independent raw-byte and scheduling audits, root
core-event rejection and a disabled exhaustive uncore-selector sweep; do not repeat
those unchanged. Their results are in the [CLI guide](apple-silicon.md),
[CLI validation](apple-m6-cli-validation.json),
[trace validation](apple-m6-trace-validation.json) and
[deep validation](apple-m6-deep-validation.json).

## Resume prompt

Paste this into the coding session after reboot:

> Resume the approved CLI-only M6 development-kernel experiment in
> /Users/oly/redislabs/topdown-profiler. Read docs/apple-m6-resume.md and the local
> SESSION-HANDOFF.md it links to, then run resume-status.py. Respect the recorded
> authorization for this exact candidate and verify the actual running kernel UUID
> and CPC state before testing one public programmable event with cleanup and
> idle/N/2N controls. Continue from the recorded evidence. Do not assume a file on
> disk is the running kernel or claim full TMA from unvalidated counts.
