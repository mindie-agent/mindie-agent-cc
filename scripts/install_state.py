"""One installation profile and its existing operation lock.

[Role]
Manages the cross-process `OperationLock` (`operation.lock`), atomic JSON
writes, and validation of the installed runtime binding (`installation.json`).

[Invariants]
- Verifies `INSTALLATION_SCHEMA` (`mindie-cc-installation/1`) and `FACADE_PROTOCOL`
  before returning a runtime binding.

[Timeouts & Locks]
- `OperationLock` polls `flock`/`msvcrt.locking` up to caller-specified
  `timeout` seconds and raises `LockTimeout` on expiry.
- Candidate preparation uses a separate lock from admitted business work.

[Side-Effect Policy]
- Acquires `<state_dir>/update/operation.lock` and performs atomic temp-file
  replacements with `os.replace`.
"""

from contextlib import contextmanager
import json
import os
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
    """The installation profile cannot supply a compatible helper."""


INSTALLATION_SCHEMA = "mindie-cc-installation/1"
FACADE_PROTOCOL = 1


def read_binding(path=None):
    from entry import _read_regular_json

    state, value = _read_regular_json(Path(path or config_path()))
    if state is not None:
        raise InstallationUnavailable(
            "installation configuration is unavailable; existing data is preserved"
        )
    if (value.get("schema") != INSTALLATION_SCHEMA
            or value.get("facade_protocol") != FACADE_PROTOCOL):
        raise InstallationUnavailable("unsupported installation protocol")
    for name in ("base_config", "engine_config", "python", "state_dir",
                 "runtime_scripts", "adapter_config"):
        if not isinstance(value.get(name), str) or not os.path.isabs(value[name]):
            raise InstallationUnavailable(
                "unsupported development profile; existing choices and data are preserved"
            )
    return value


def _complete_binding(expected, selected):
    if (selected["base_config"] != expected["base_config"]
            or selected["state_dir"] != expected["state_dir"]):
        raise InstallationUnavailable(
            "installation profile identity changed; not executed"
        )
    fixed = read_binding(selected["adapter_config"])
    if selected != fixed:
        raise InstallationUnavailable("selected helper binding is incomplete; not executed")
    return selected


def binding_snapshot():
    """Read one complete immutable helper selection without service admission.

    Stop writes the existing durable queue even while an owner is retired.
    Its helper keeps this snapshot; shared consent and revocation remain live.
    """
    path = config_path()
    expected = read_binding(path)
    selected = (expected if str(path) == expected["base_config"]
                else read_binding(expected["base_config"]))
    return _complete_binding(expected, selected)


@contextmanager
def installed_binding(expected=None, *, timeout=5.0):
    """Hold shared admission for one operation on the current complete helper.

    A facade retains only its base-profile identity. Runtime selection belongs
    to the canonical installation config, never to the old facade's code.
    """
    expected = read_binding() if expected is None else expected
    with OperationLock(expected).shared(timeout=timeout):
        selected = read_binding(expected["base_config"])
        yield _complete_binding(expected, selected)


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

def _lock_once(descriptor, exclusive: bool, *, bootstrap=False) -> bool:
    if not bootstrap:
        from mindie_knowledge.loop.locks import try_lock_file
        return try_lock_file(descriptor, exclusive=exclusive)
    # Runtime construction precedes shared-core availability. uv preparation
    # uses only this existing exclusive lock; no reader protocol is needed.
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

def _unlock(descriptor, *, bootstrap=False) -> None:
    try:
        if not bootstrap:
            from mindie_knowledge.loop.locks import unlock_file
            unlock_file(descriptor)
        elif os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)

class _Held:
    def __init__(self, path: Path, exclusive: bool, timeout: float, *, bootstrap=False):
        self._path = path
        self._exclusive = exclusive
        self._timeout = timeout
        self._descriptor = None
        self._bootstrap = bootstrap

    def __enter__(self):
        from bounded import check_cancelled

        check_cancelled()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + self._timeout
        try:
            while True:
                check_cancelled()
                if _lock_once(descriptor, self._exclusive, bootstrap=self._bootstrap):
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
            _unlock(self._descriptor, bootstrap=self._bootstrap)
            self._descriptor = None
        return False

class OperationLock:
    def __init__(self, config=None, *, bootstrap=False):
        self._path = lock_path(config)
        self._bootstrap = bootstrap

    def shared(self, timeout=5.0):
        return _Held(self._path, False, timeout, bootstrap=self._bootstrap)

    def exclusive(self, timeout=30.0):
        return _Held(self._path, True, timeout, bootstrap=self._bootstrap)

    def preparation(self, timeout=30.0):
        """Serialize cold binding preparation without blocking admitted work."""
        return _Held(self._path.with_name("preparation.lock"), True, timeout,
                     bootstrap=self._bootstrap)
