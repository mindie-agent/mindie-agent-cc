"""Remote execution receipt formats may reset in development, never after release.

This records the format of the directory ``mcp_server.remote_call`` already
uses: ``paths.state_dir(adapter) / "remote"``. Each native session is one
child of that directory, and ``REMOTE_DEV_STATE_DIR`` points at that child.
Shared remote-dev stores jobs and uncertain receipts inside the session
directory and does not publish its own format contract, so this file is the
adapter's contract for that real tree.

``remote-layout.json`` lives in the remote base, beside the session
directories. It does not name Codex's ``gate-v<N>`` root and it does not
relocate a session. A released format this runtime does not understand fails
before any session path is moved, deleted, reset, or rewritten.

The remote adapter calls ``prepare_layout`` before submitting external work.
The declaration must survive a failed or uncertain submission.
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
SCHEMA = "mindie-cc-remote-layout/1"
_LIMIT = 4096
# Files that remote-dev writes inside one native session directory.
_DURABLE = ("jobs", "logs", "reads", "artifacts", "patches", "context")


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
    path = Path(base) / "remote-layout.json"
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


def _occupied(base):
    """True when a native session directory already holds remote-dev state."""
    base = Path(base)
    if not base.is_dir():
        return False
    for session in base.iterdir():
        if not session.is_dir() or session.name.startswith("."):
            continue
        for name in _DURABLE:
            found = session / name
            if found.is_dir() or found.is_file():
                return True
        if any(path.suffix == ".json" and path.is_file() for path in session.glob("*")):
            return True
    return False


def receipt_root(base):
    """Read-only check of the actual remote base. Nothing is created or moved.

    A compatible result means the existing session directories may stay where
    ``remote_call`` wrote them. A released foreign format rejects before a
    switch and does not select a replacement directory.
    """
    base = Path(base)
    previous = _read(base)
    _version(RELEASE_VERSION)
    if previous is None and _occupied(base):
        raise ValueError("remote receipt layout is missing; existing receipts were preserved")
    if previous is not None and previous["format"] != FORMAT:
        raise ValueError(
            "remote receipts require an explicit format migration; "
            "receipts were preserved"
        )
    return base


@contextmanager
def prepare_layout(base):
    """Declare the existing layout before any possible external effect."""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    selected = receipt_root(base)
    previous = _read(base)
    release = RELEASE_VERSION
    if previous is not None and previous["format"] == FORMAT:
        release = release or previous["release_version"]
    value = dict(schema=SCHEMA, format=FORMAT, release_version=release)
    if value == previous:
        yield selected
        return
    descriptor, name = tempfile.mkstemp(dir=base, prefix=".remote-layout-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, base / "remote-layout.json")
        name = None
    finally:
        if name is not None:
            Path(name).unlink(missing_ok=True)
    yield selected
