"""Collector scope must match the requested process or system scope."""

from unittest.mock import patch

import pytest

from topdown.collector.toplev import ToplevOptions, ToplevRunner


@pytest.mark.parametrize("pids,system_wide", [([123], False), ([123, 456], False), ([123], True)])
def test_pid_request_cannot_fall_back_to_cpu_zero(pids, system_wide):
    runner = ToplevRunner("toplev.py", ToplevOptions(pids=pids, system_wide=system_wide))
    with patch("topdown.collector.toplev.subprocess.Popen") as spawn:
        spawn.return_value.communicate.return_value = ("", "")
        with pytest.raises(ValueError, match="process-scoped"):
            runner.run(1)
    spawn.assert_not_called()


@pytest.mark.parametrize("pids", [None, []])
def test_explicit_system_scope_remains_supported(pids):
    runner = ToplevRunner("toplev.py", ToplevOptions(pids=pids, system_wide=True))
    command = runner.build_command()
    assert "-a" in command
    assert "-C" not in command
