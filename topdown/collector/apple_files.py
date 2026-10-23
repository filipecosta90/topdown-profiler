"""Read native JSON artifacts without waiting on devices or named pipes."""

import errno
import os
from pathlib import Path
import stat


def read_regular_bytes(path: Path) -> bytes:
    if not stat.S_ISREG(path.stat().st_mode):
        raise OSError(errno.EINVAL, "Require a regular Apple JSON artifact", str(path))
    # Nonblocking open also protects against a file being replaced by a FIFO after stat.
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError(errno.EINVAL, "Require a regular Apple JSON artifact", str(path))
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return stream.read()
    finally:
        os.close(descriptor)
