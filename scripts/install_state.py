"""One installation profile and its existing operation lock."""

from contextlib import contextmanager
import json
import os
import sys
import time
from pathlib import Path

from paths import config_path, state_dir

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def lock_path(config=None):
    return state_dir(config) / "update" / "operation.lock"


class InstallationUnavailable(ValueError):
    """This fixed entry is no longer the selected installation."""


def read_binding(path=None):
    from entry import _read_regular_json

    state, value = _read_regular_json(Path(path or config_path()))
    if state is not None:
        raise InstallationUnavailable(
            "native_reload_required: installation configuration is unavailable"
        )
    for name in ("base_config", "engine_config", "python", "state_dir"):
        if not isinstance(value.get(name), str) or not os.path.isabs(value[name]):
            raise InstallationUnavailable(
                "unsupported development profile; existing choices and data are preserved"
            )
    return value


@contextmanager
def installed_binding(expected=None, *, timeout=5.0):
    """Check the process's fixed binding under the installation operation lock."""
    expected = read_binding() if expected is None else expected
    with OperationLock(expected).shared(timeout=timeout):
        selected = read_binding(expected["base_config"])
        fixed = read_binding()
        if (selected != expected or fixed != expected
                or os.path.normcase(expected["python"]) != os.path.normcase(sys.executable)):
            raise InstallationUnavailable(
                "native_reload_required: this entry is not the selected installation"
            )
        yield expected


def atomic_write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.replace(tmp, path)

class LockTimeout(RuntimeError):
    pass

def _lock_once(descriptor, exclusive: bool) -> bool:
    try:
        if os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            flag = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(descriptor, flag | fcntl.LOCK_NB)
        return True
    except OSError:
        return False

def _unlock(descriptor) -> None:
    try:
        if os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        pass
    os.close(descriptor)

class _Held:
    def __init__(self, path: Path, exclusive: bool, timeout: float):
        self._path = path
        self._exclusive = exclusive
        self._timeout = timeout
        self._descriptor = None

    def __enter__(self):
        from bounded import check_cancelled

        check_cancelled()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + self._timeout
        try:
            while True:
                check_cancelled()
                if _lock_once(descriptor, self._exclusive):
                    break
                if time.monotonic() >= deadline:
                    kind = "exclusive" if self._exclusive else "shared"
                    raise LockTimeout(f"timed out acquiring {kind} operation lock")
                time.sleep(0.05)
        except BaseException:
            os.close(descriptor)
            raise
        self._descriptor = descriptor
        return self

    def __exit__(self, *exc):
        if self._descriptor is not None:
            _unlock(self._descriptor)
            self._descriptor = None
        return False

class OperationLock:
    def __init__(self, config=None):
        self._path = lock_path(config)

    def shared(self, timeout=5.0):
        return _Held(self._path, False, timeout)

    def exclusive(self, timeout=30.0):
        return _Held(self._path, True, timeout)
