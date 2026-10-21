"""Exercise the Recovery wrapper without changing host boot policy."""

import os
import pty
import select
import subprocess
import time
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "m6-recovery-boot-wrapper.sh"
ARTIFACTS = Path("Users/oly/redislabs/topdown-profiler/.local-runs/m6-kdk-20261021T091524Z")
CANDIDATE_SHA = "714872ac8f6eb514056790110b635bb90bc1c0a1122cbe21940687d09191f112"
HELPER_SHA = "876c08b94afe748117f633d875fbab64c239ca53370270d3bd7afa90ac2d3f18"
ABC_SHA = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def _command(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def _environment(tmp_path: Path, *, group_match: bool = True, data_locked: bool = False,
                 recovery_hash_fails: bool = False,
                 helper_hash: str = HELPER_SHA, sync_fails: bool = False) -> tuple[dict, Path, Path]:
    commands = tmp_path / "bin"
    commands.mkdir()
    data = tmp_path / "Data"
    system = tmp_path / "System"
    home = data / "Users" / "oly"
    artifacts = data / ARTIFACTS
    artifacts.mkdir(parents=True)
    home.mkdir(parents=True, exist_ok=True)
    (system / "usr" / "bin").mkdir(parents=True)
    (artifacts / "M6-development-26A434.kc").write_bytes(b"candidate")
    _command(home, "m6-boot.sh", """\
PATH=/usr/bin:/bin
command -v shasum >/dev/null || exit 1
shasum -a 256 "$TD_TEST_CANDIDATE" || exit 1
printf 'helper-ran\\n'
: > "$TD_TEST_MARKER"
""")
    _command(commands, "diskutil", """\
case "$1:$2" in
    info:disk3s5)
        if [ -f "$TD_TEST_DATA_STATE" ]; then
            printf '   Volume Name: Data\\n   Mounted: Yes\\n   Mount Point: %s\\n   APFS Volume Group: GROUP-A\\n   FileVault: Yes\\n' "$TD_TEST_DATA"
        else
            printf '   Volume Name: Data\\n   Mounted: No\\n   Mount Point: Not mounted\\n   APFS Volume Group: GROUP-A\\n   FileVault: Yes\\n'
        fi ;;
    info:disk3s1)
        printf '   Volume Name: Macintosh HD\\n   Mounted: Yes\\n   Mount Point: %s\\n   APFS Volume Group: %s\\n' "$TD_TEST_SYSTEM" "$TD_TEST_SYSTEM_GROUP" ;;
    apfs:listVolumeGroups) printf 'GROUP-A\\n' ;;
    apfs:unlockVolume) : > "$TD_TEST_DATA_STATE" ;;
    *) exit 1 ;;
esac
""")
    openssl_body = """\
[ "$1" = dgst ] && [ "$2" = -sha256 ] || exit 1
case "$3" in
    */m6-boot.sh) hash="$TD_TEST_HELPER_HASH" ;;
    */M6-development-26A434.kc) hash="$TD_TEST_CANDIDATE_HASH" ;;
    *) [ "$(cat "$3")" = abc ] || exit 1; hash="$TD_TEST_ABC_HASH" ;;
esac
printf 'SHA2-256(%s)= %s\\n' "$3" "$hash"
"""
    _command(system / "usr" / "bin", "openssl", openssl_body)
    _command(commands, "openssl", "exit 1\n" if recovery_hash_fails else openssl_body)
    _command(commands, "sysctl", """\
[ "$1:$2" = '-n:hw.model' ] || exit 1
printf 'Mac18,5\\n'
""")
    _command(commands, "stat", """\
[ "$1" = -f ] || exit 1
printf '%s\\n' "$TD_TEST_OWNER"
""")
    _command(commands, "chown", "exit 0\n")
    _command(commands, "wc", "printf '133431296\\n'\n")
    for name in ("sw_vers", "csrutil", "bputil"):
        _command(commands, name, "exit 0\n")
    _command(commands, "sync", "exit 1\n" if sync_fails else "exit 0\n")
    data_state = tmp_path / "data-unlocked"
    if not data_locked:
        data_state.touch()
    env = os.environ.copy()
    env.update(
        PATH=f"{commands}:{env['PATH']}",
        TD_TEST_DATA=str(data),
        TD_TEST_DATA_STATE=str(data_state),
        TD_TEST_SYSTEM=str(system),
        TD_TEST_SYSTEM_GROUP="GROUP-A" if group_match else "GROUP-B",
        TD_TEST_OWNER=f"{os.getuid()}:{os.getgid()}",
        TD_TEST_HELPER_HASH=helper_hash,
        TD_TEST_CANDIDATE_HASH=CANDIDATE_SHA,
        TD_TEST_ABC_HASH=ABC_SHA,
        TD_TEST_CANDIDATE=str(artifacts / "M6-development-26A434.kc"),
        TD_TEST_MARKER=str(tmp_path / "helper.marker"),
    )
    return env, artifacts, tmp_path / "helper.marker"


def _run(tmp_path: Path, **kwargs) -> tuple[subprocess.CompletedProcess, Path, Path]:
    env, artifacts, marker = _environment(tmp_path, **kwargs)
    result = subprocess.run(
        ["sh", str(SCRIPT)], input="disk3s5\ndisk3s1\n", text=True,
        capture_output=True, env=env, timeout=20,
    )
    return result, artifacts, marker


def test_recovery_wrapper_logs_and_runs_reviewed_helper(tmp_path):
    result, artifacts, marker = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.exists()
    logs = list(artifacts.glob("recovery-boot-*.log"))
    assert len(logs) == 1
    content = logs[0].read_text()
    assert "Verified helper and collection SHA-256" in content
    assert "Starting the reviewed M6 helper" in content
    assert "helper-ran" in result.stdout
    assert list(artifacts.glob("recovery-boot-*.status"))[0].read_text() == "helper-exit=0\n"


def test_recovery_wrapper_unlocks_data_and_uses_system_hash_tool(tmp_path):
    result, artifacts, marker = _run(tmp_path, data_locked=True, recovery_hash_fails=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.exists()
    assert (tmp_path / "data-unlocked").exists()
    content = next(artifacts.glob("recovery-boot-*.log")).read_text()
    assert "Verified SHA-256 backend: openssl" in content
    assert f"{tmp_path}/System/usr/bin/openssl" in content


def test_recovery_wrapper_rejects_wrong_volume_group(tmp_path):
    result, artifacts, marker = _run(tmp_path, group_match=False)
    assert result.returncode != 0
    assert not marker.exists()
    assert "volume groups do not match" in next(artifacts.glob("recovery-boot-*.log")).read_text()


def test_recovery_wrapper_rejects_changed_helper(tmp_path):
    result, artifacts, marker = _run(tmp_path, helper_hash="0" * 64)
    assert result.returncode != 0
    assert not marker.exists()
    assert "Staged helper differs" in next(artifacts.glob("recovery-boot-*.log")).read_text()


def test_recovery_wrapper_stops_if_log_cannot_be_flushed(tmp_path):
    result, artifacts, marker = _run(tmp_path, sync_fails=True)
    assert result.returncode != 0
    assert not marker.exists()
    assert "Cannot flush the persistent log" in result.stderr
    assert "phase=activation-pending\n" == next(artifacts.glob("recovery-boot-*.status")).read_text()


def test_recovery_wrapper_keeps_native_terminal_for_helper(tmp_path):
    env, artifacts, marker = _environment(tmp_path)
    helper = tmp_path / "Data" / "Users" / "oly" / "m6-boot.sh"
    _command(helper.parent, helper.name, "[ -t 0 ] && [ -t 1 ] && [ -t 2 ] || exit 14\n"
             ': > "$TD_TEST_MARKER"\n')
    master, slave = pty.openpty()
    try:
        process = subprocess.Popen(["sh", str(SCRIPT)], stdin=slave, stdout=slave,
                                   stderr=slave, env=env)
        os.write(master, b"disk3s5\ndisk3s1\n")
        deadline = time.monotonic() + 20
        while process.poll() is None and time.monotonic() < deadline:
            if select.select([master], [], [], 0.2)[0]:
                os.read(master, 4096)
        assert process.wait(timeout=1) == 0
    finally:
        os.close(master)
        os.close(slave)
    assert marker.exists()
    assert "helper-exit=0\n" == next(artifacts.glob("recovery-boot-*.status")).read_text()


def test_recovery_wrapper_shasum_mode_matches_sha256(tmp_path):
    content = tmp_path / "abc"
    content.write_text("abc")
    result = subprocess.run(
        ["sh", str(SCRIPT), "--shasum", "-a", "256", str(content)],
        text=True, capture_output=True,
        env={**os.environ, "TD_HASH_KIND": "openssl", "TD_HASH_BIN": "/usr/bin/openssl"},
    )
    assert result.returncode == 0
    assert result.stdout == f"{ABC_SHA}  {content}\n"


def test_recovery_wrapper_rejects_unsupported_shasum_mode(tmp_path):
    content = tmp_path / "abc"
    content.write_text("abc")
    result = subprocess.run(
        ["sh", str(SCRIPT), "--shasum", "-a", "1", str(content)],
        text=True, capture_output=True,
        env={**os.environ, "TD_HASH_KIND": "openssl", "TD_HASH_BIN": "/usr/bin/openssl"},
    )
    assert result.returncode != 0
    assert "unsupported shasum mode" in result.stderr
