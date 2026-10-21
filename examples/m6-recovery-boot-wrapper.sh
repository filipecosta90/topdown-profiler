#!/bin/sh

# Run in paired Recovery. The staged helper retains the exact machine-specific
# UUID checks and owns the security-policy and custom-boot changes.

set -u
umask 077

TD_CANDIDATE_SHA=714872ac8f6eb514056790110b635bb90bc1c0a1122cbe21940687d09191f112
TD_HELPER_SHA=876c08b94afe748117f633d875fbab64c239ca53370270d3bd7afa90ac2d3f18
TD_SELFTEST_SHA=ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad
TD_ARTIFACT_REL=Users/oly/redislabs/topdown-profiler/.local-runs/m6-kdk-20261021T091524Z

field() {
    printf '%s\n' "$1" | sed -n "s/^[[:space:]]*$2:[[:space:]]*//p" | sed -n '1p'
}

error() {
    printf 'ERROR: %s\n' "$*" >&2
    return 1
}

sha_file() {
    case ${TD_HASH_KIND:-} in
        openssl)
            td_output=$("$TD_HASH_BIN" dgst -sha256 "$1" 2>/dev/null) || return 1
            td_digest=${td_output##* }
            ;;
        python)
            td_digest=$("$TD_HASH_BIN" -c '
import hashlib, sys
h = hashlib.sha256()
with open(sys.argv[1], "rb") as source:
    for block in iter(lambda: source.read(1024 * 1024), b""):
        h.update(block)
print(h.hexdigest())
' "$1" 2>/dev/null) || return 1
            ;;
        *) return 1 ;;
    esac
    td_digest=$(printf '%s' "$td_digest" | tr 'A-F' 'a-f')
    [ "${#td_digest}" -eq 64 ] || return 1
    case "$td_digest" in *[!0123456789abcdef]*) return 1 ;; esac
    printf '%s\n' "$td_digest"
}

shasum_compat() {
    [ "${1:-}" = "-a" ] && [ "${2:-}" = "256" ] ||
        { error "The staged helper requested an unsupported shasum mode"; return 2; }
    shift 2
    td_marker='  '
    if [ "${1:-}" = "-b" ]; then td_marker=' *'; shift; fi
    [ "$#" -gt 0 ] || { error "shasum needs a file"; return 2; }
    for td_file do
        [ -f "$td_file" ] || { error "Missing hash input: $td_file"; return 1; }
        td_hash=$(sha_file "$td_file") ||
            { error "SHA-256 tool failed on $td_file"; return 1; }
        printf '%s%s%s\n' "$td_hash" "$td_marker" "$td_file"
    done
}

if [ "${1:-}" = "--shasum" ]; then
    shift
    shasum_compat "$@"
    exit $?
fi

if [ "${1:-}" = "--help" ]; then
    printf 'Usage: sh m6-recovery-boot-wrapper.sh\n'
    printf 'Prompts for Data and System APFS disk identifiers in paired Recovery.\n'
    exit 0
fi
[ "$#" -eq 0 ] || { error "Unexpected arguments; use --help"; exit 2; }

for td_tool in diskutil sed tr wc tee mktemp date stat chown chmod mkdir cat sync; do
    command -v "$td_tool" >/dev/null 2>&1 ||
        { error "Missing Recovery tool: $td_tool"; exit 1; }
done

printf 'Data APFS disk identifier (for example disk3s5): '
IFS= read -r TD_DATA_ID || exit 1
printf 'System APFS disk identifier (for example disk3s1): '
IFS= read -r TD_SYSTEM_ID || exit 1
case "$TD_DATA_ID" in disk[0-9]*s[0-9]*) ;; *) error "Invalid Data disk identifier"; exit 1 ;; esac
case "$TD_SYSTEM_ID" in disk[0-9]*s[0-9]*) ;; *) error "Invalid System disk identifier"; exit 1 ;; esac
[ "$TD_DATA_ID" != "$TD_SYSTEM_ID" ] ||
    { error "System and Data identifiers are identical"; exit 1; }

TD_DATA_INFO=$(diskutil info "$TD_DATA_ID") ||
    { error "Cannot inspect Data volume $TD_DATA_ID"; exit 1; }
TD_DATA_MOUNT=$(field "$TD_DATA_INFO" "Mount Point")
if [ -z "$TD_DATA_MOUNT" ] || [ "$TD_DATA_MOUNT" = "Not mounted" ]; then
    case "$(field "$TD_DATA_INFO" "FileVault")" in
        Yes*)
            printf 'Unlocking %s. Enter FileVault credentials only at the native prompt.\n' "$TD_DATA_ID"
            if ! diskutil apfs unlockVolume "$TD_DATA_ID"; then
                diskutil mount "$TD_DATA_ID" ||
                    { error "Data could not be unlocked or mounted"; exit 1; }
            fi
            ;;
        *)
            diskutil mount "$TD_DATA_ID" ||
                { error "Data mount failed"; exit 1; }
            ;;
    esac
    TD_DATA_INFO=$(diskutil info "$TD_DATA_ID") ||
        { error "Cannot inspect mounted Data volume"; exit 1; }
    TD_DATA_MOUNT=$(field "$TD_DATA_INFO" "Mount Point")
fi
[ -d "$TD_DATA_MOUNT" ] ||
    { error "Data has no accessible mount point"; exit 1; }

TD_HOME="$TD_DATA_MOUNT/Users/oly"
TD_ARTIFACTS="$TD_DATA_MOUNT/$TD_ARTIFACT_REL"
[ -d "$TD_HOME" ] && [ -d "$TD_ARTIFACTS" ] ||
    { error "The original M6 home or handoff is absent on this Data volume"; exit 1; }
TD_LOG="$TD_ARTIFACTS/recovery-boot-$(date -u +%Y%m%dT%H%M%SZ)-$$.log"
TD_STATUS="${TD_LOG%.log}.status"
: > "$TD_LOG" || { error "Cannot create persistent log"; exit 1; }
chmod 600 "$TD_LOG" || exit 1
TD_OWNER=$(stat -f '%u:%g' "$TD_HOME") ||
    { error "Cannot determine home owner for readable log"; exit 1; }
chown "$TD_OWNER" "$TD_LOG" ||
    { error "Cannot make log readable by the normal account"; exit 1; }
printf 'Persistent log: %s\n' "$TD_LOG"

choose_hash_backend() {
    td_selftest=$(mktemp /tmp/m6-sha256.XXXXXX) || return 1
    printf abc > "$td_selftest"
    for td_bin in "$(command -v openssl 2>/dev/null || :)" \
                  "$1/usr/bin/openssl"; do
        [ -n "$td_bin" ] && [ -x "$td_bin" ] || continue
        TD_HASH_KIND=openssl
        TD_HASH_BIN=$td_bin
        if [ "$(sha_file "$td_selftest")" = "$TD_SELFTEST_SHA" ]; then
            rm -f "$td_selftest"
            export TD_HASH_KIND TD_HASH_BIN
            return 0
        fi
    done
    for td_bin in "$(command -v python3 2>/dev/null || :)" \
                  "$1/usr/bin/python3" "$TD_DATA_MOUNT/Users/oly/redislabs/topdown-profiler/.venv/bin/python"; do
        [ -n "$td_bin" ] && [ -x "$td_bin" ] || continue
        TD_HASH_KIND=python
        TD_HASH_BIN=$td_bin
        if [ "$(sha_file "$td_selftest")" = "$TD_SELFTEST_SHA" ]; then
            rm -f "$td_selftest"
            export TD_HASH_KIND TD_HASH_BIN
            return 0
        fi
    done
    rm -f "$td_selftest"
    return 1
}

audit_and_run() {
    printf 'Recovery boot wrapper started (UTC): %s\n' "$(date -u)"
    printf 'Data disk: %s; mount: %s\n' "$TD_DATA_ID" "$TD_DATA_MOUNT"
    printf 'System disk: %s\n' "$TD_SYSTEM_ID"
    TD_DATA_INFO=$(diskutil info "$TD_DATA_ID") ||
        { error "Data identity query failed"; return 1; }
    TD_SYSTEM_INFO=$(diskutil info "$TD_SYSTEM_ID") ||
        { error "System identity query failed"; return 1; }
    TD_DATA_GROUP=$(field "$TD_DATA_INFO" "APFS Volume Group")
    TD_SYSTEM_GROUP=$(field "$TD_SYSTEM_INFO" "APFS Volume Group")
    [ -n "$TD_DATA_GROUP" ] && [ "$TD_DATA_GROUP" = "$TD_SYSTEM_GROUP" ] ||
        { error "System and Data volume groups do not match"; return 1; }
    [ "$(field "$TD_DATA_INFO" "Mount Point")" = "$TD_DATA_MOUNT" ] ||
        { error "Data mount point changed"; return 1; }
    printf 'Verified volume group: %s\n' "$TD_DATA_GROUP"
    diskutil apfs listVolumeGroups || return 1

    TD_SYSTEM_MOUNT=$(field "$TD_SYSTEM_INFO" "Mount Point")
    if [ -z "$TD_SYSTEM_MOUNT" ] || [ "$TD_SYSTEM_MOUNT" = "Not mounted" ]; then
        diskutil mount readOnly "$TD_SYSTEM_ID" ||
            { error "System volume mount failed"; return 1; }
        TD_SYSTEM_INFO=$(diskutil info "$TD_SYSTEM_ID") ||
            { error "System identity recheck failed"; return 1; }
        [ "$(field "$TD_SYSTEM_INFO" "APFS Volume Group")" = "$TD_DATA_GROUP" ] ||
            { error "Mounted System volume group changed"; return 1; }
        TD_SYSTEM_MOUNT=$(field "$TD_SYSTEM_INFO" "Mount Point")
    fi
    [ -d "$TD_SYSTEM_MOUNT" ] ||
        { error "System has no accessible mount point"; return 1; }
    printf 'System mount: %s\n' "$TD_SYSTEM_MOUNT"
    printf 'Recovery model: '
    sysctl -n hw.model || return 1
    [ "$(sysctl -n hw.model)" = "Mac18,5" ] ||
        { error "This is not the authorized Mac18,5"; return 1; }
    sw_vers || :
    csrutil status || :
    bputil -d -v "$TD_DATA_GROUP" || :

    choose_hash_backend "$TD_SYSTEM_MOUNT" ||
        { error "No SHA-256 tool works in Recovery or the mounted system"; return 1; }
    printf 'Verified SHA-256 backend: %s (%s)\n' "$TD_HASH_KIND" "$TD_HASH_BIN"
    TD_HELPER="$TD_HOME/m6-boot.sh"
    TD_CANDIDATE="$TD_ARTIFACTS/M6-development-26A434.kc"
    [ -f "$TD_HELPER" ] && [ -f "$TD_CANDIDATE" ] ||
        { error "Reviewed helper or candidate is missing"; return 1; }
    TD_SIZE=$(wc -c < "$TD_CANDIDATE" | tr -d '[:space:]')
    [ "$TD_SIZE" = "133431296" ] ||
        { error "Candidate size differs from the reviewed collection"; return 1; }
    TD_ACTUAL_HELPER_SHA=$(sha_file "$TD_HELPER") ||
        { error "SHA-256 tool failed on staged helper"; return 1; }
    [ "$TD_ACTUAL_HELPER_SHA" = "$TD_HELPER_SHA" ] ||
        { error "Staged helper differs from the seven-review checkpoint"; return 1; }
    TD_ACTUAL_CANDIDATE_SHA=$(sha_file "$TD_CANDIDATE") ||
        { error "SHA-256 tool failed on candidate collection"; return 1; }
    [ "$TD_ACTUAL_CANDIDATE_SHA" = "$TD_CANDIDATE_SHA" ] ||
        { error "Candidate SHA-256 differs from the reviewed collection"; return 1; }
    printf 'Verified helper and collection SHA-256; collection bytes: %s\n' "$TD_SIZE"

    TD_SHIM_DIR="/tmp/m6-recovery-shim-$$"
    mkdir -m 700 "$TD_SHIM_DIR" || return 1
    cat > "$TD_SHIM_DIR/shasum" <<'SHIM'
#!/bin/sh
exec /bin/sh "$TD_WRAPPER_SCRIPT" --shasum "$@"
SHIM
    chmod 700 "$TD_SHIM_DIR/shasum" || return 1
    case "$0" in
        /*) TD_WRAPPER_SCRIPT=$0 ;;
        *) TD_WRAPPER_SCRIPT="$(pwd)/$0" ;;
    esac
    export TD_WRAPPER_SCRIPT
    PATH="$TD_SHIM_DIR:$PATH"
    export PATH
    printf 'phase=helper-started\n' > "$TD_STATUS"
    chown "$TD_OWNER" "$TD_STATUS" || return 1
    printf 'Starting the reviewed M6 helper; its native authentication prompts remain interactive.\n'
    printf 'The helper controls csrutil, kmutil configure-boot, and reboot.\n'
    sync
    sh -c 'shasum() { /bin/sh "$TD_WRAPPER_SCRIPT" --shasum "$@"; }; . "$0"' "$TD_HELPER"
}

(
    audit_and_run
    td_rc=$?
    printf 'helper-exit=%s\n' "$td_rc" > "$TD_STATUS"
    chown "$TD_OWNER" "$TD_STATUS" || :
    printf 'Recovery wrapper finished with status %s\n' "$td_rc"
    sync
    exit "$td_rc"
) 2>&1 | tee -a "$TD_LOG"

if [ -f "$TD_STATUS" ]; then
    TD_RESULT=$(sed -n 's/^helper-exit=//p' "$TD_STATUS")
    case "$TD_RESULT" in
        ''|*[!0-9]*) error "Invalid status file"; exit 1 ;;
        *) exit "$TD_RESULT" ;;
    esac
fi
error "The helper did not return a status; inspect $TD_LOG and its own checkpoints"
exit 1
