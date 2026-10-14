"""Remote execution receipt formats may reset in development, never after release.

This records durable external execution receipts beside the adapter state.
It does not own the remote worker. A released format that this runtime does
not understand fails before any receipt file is moved, deleted, or rewritten.
"""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
import tempfile

FORMAT = 1
# The first product release protects remote effects, including uncertain writes.
RELEASE_VERSION = "0.0.1"
SCHEMA = "mindie-remote-layout/1"
_LIMIT = 4096


def _version(value):
    if value is not None and (
            not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value)):
        raise ValueError(
            "remote receipt release version must be normal or null during development"
        )
    return value


def _present(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _read(base):
    path = Path(base) / "gate-layout.json"
    if not _present(path):
        return None
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("remote receipt layout must be a regular file")
    with path.open("rb") as stream:
        raw = stream.read(_LIMIT + 1)
    if len(raw) > _LIMIT:
        raise ValueError("remote receipt layout exceeds its metadata bound")
    value = json.loads(raw)
    if (not isinstance(value, dict) or set(value) != {"schema", "format", "release_version"}
            or value["schema"] != SCHEMA
            or type(value["format"]) is not int or value["format"] < 1):
        raise ValueError("remote receipt layout is invalid; existing receipts were preserved")
    _version(value["release_version"])
    return value


def receipt_root(base):
    """Read-only compatibility check. A released foreign format needs migration."""
    base = Path(base)
    previous = _read(base)
    _version(RELEASE_VERSION)
    if previous is None and any(
            path for directory in base.glob("gate-v[0-9]*")
            for pattern in ("*.sqlite3", "*.authority.json", "*.json")
            for path in directory.glob(pattern) if path.is_file()):
        raise ValueError("remote receipt layout is missing; existing receipts were preserved")
    if previous is not None:
        directory = base / ("gate-v" + str(previous["format"]))
        if not directory.is_dir():
            raise ValueError(
                "initialized remote receipt directory is missing; layout was preserved"
            )
        if previous["format"] != FORMAT and previous["release_version"] is not None:
            raise ValueError(
                "released remote receipts require an explicit format migration; "
                "receipts were preserved"
            )
    return base / ("gate-v" + str(FORMAT))


@contextmanager
def prepare_layout(base):
    """Publish the layout only after the caller finishes using the directory."""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    selected = receipt_root(base)
    previous = _read(base)
    yield selected
    release = RELEASE_VERSION
    if previous is not None and previous["format"] == FORMAT:
        release = release or previous["release_version"]
    value = dict(schema=SCHEMA, format=FORMAT, release_version=release)
    if value == previous:
        return
    descriptor, name = tempfile.mkstemp(dir=base, prefix=".gate-layout-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, base / "gate-layout.json")
        name = None
    finally:
        if name is not None:
            Path(name).unlink(missing_ok=True)
