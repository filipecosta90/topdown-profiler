# Resume the M6 development-kernel experiment

The matching M6 development-kernel collection has been generated and inspected.
At the last checkpoint, the Mac was still running the release kernel; the candidate
had not been activated or booted. The KDK is installed. The next action is the
locally authorized Recovery boot test, followed by read-only verification of the
actual running kernel and a scoped programmable-counter experiment.

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
