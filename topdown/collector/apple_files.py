"""Read native JSON artifacts without waiting on devices or named pipes."""

import errno
import json
import math
import os
from pathlib import Path
import stat


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate Apple JSON field: {key}")
        result[key] = value
    return result


def _json_constant(value):
    raise ValueError(f"Non-finite Apple JSON number: {value}")


def _json_float(value):
    number = float(value)
    if not math.isfinite(number):
        _json_constant(value)
    return number


def decode_json_object(data: bytes) -> dict:
    try:
        value = json.loads(data, object_pairs_hook=_json_object, parse_constant=_json_constant, parse_float=_json_float)
    except RecursionError as exc:
        raise ValueError("Apple JSON exceeds the parser nesting limit") from exc
    if not isinstance(value, dict):
        raise ValueError("Require a JSON object in an Apple artifact")
    return value


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
