#!/usr/bin/env python3
"""Materialize this native plugin's locked runtime, then leave host selection alone.

[Role]
Prepares and validates the locked Python virtual environment (`uv sync --locked`)
for `mindie-agent-cc` and invokes `setup.py` to record the installation binding.

[Invariants]
- Runtime directory is keyed by `native_version` and `source_identity` digest;
  Windows new builds use a 24-character key while an existing 64-character
  directory and its `.ready.json` remain readable.
- A plugin upgrade that inherits an installed component floor asks the existing
  interpreter for the merged runtime first, so one `.venv` is built instead of
  a baseline plus a merged copy. A baseline is built only when no component
  override is required or no existing interpreter is usable.
- Never alters Claude Code's own marketplace selection or unrelated plugins.

[Timeouts & Locks]
- Acquires `OperationLock` and runs subprocesses through `bounded.run` with
  the shared SessionStart deadline (`BOOTSTRAP_TOTAL` + `UV_PYTHON_PROVISION`).
  `session_start.arm_deadline` publishes wall time across the 3.9→3.13 `execve`,
  then rebases onto local `MINDIE_SESSION_START_DEADLINE_MONOTONIC` because
  macOS monotonic clocks are not comparable across those interpreters.

[Side-Effect Policy]
- Materializes the isolated runtime under `runtime_root()` and writes atomic
  installation state via `setup.py`. `uv sync` uses a same-volume
  `runtime.parent/.uv-cache` when the platform default cache crosses a device.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import sysconfig
import time

import bounded
from install_state import OperationLock
from install_state import atomic_write


def runtime_root():
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "mindie-agent" / "runtimes" / "cc"


WINDOWS_RUNTIME_KEY_LENGTH = 24


def runtime_key(source):
    from product_contract import package_files
    source = Path(source).resolve()
    digest = hashlib.sha256()
    for path in sorted(package_files(source)):
        digest.update(path.relative_to(source).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    digest.update(f"{sys.implementation.cache_tag}:{sysconfig.get_platform()}".encode())
    return digest.hexdigest()


def runtime_key_names(key, *, windows=None):
    """New Windows key first, legacy 64-character directory second."""
    windows = os.name == "nt" if windows is None else bool(windows)
    if windows and len(key) > WINDOWS_RUNTIME_KEY_LENGTH:
        return (key[:WINDOWS_RUNTIME_KEY_LENGTH], key)
    return (key,)


def _uv_default_cache(env):
    xdg = env.get("XDG_CACHE_HOME")
    if isinstance(xdg, str) and xdg.strip():
        return Path(xdg) / "uv"
    if os.name == "nt":
        base = env.get("LOCALAPPDATA") or os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "uv" / "cache"
        return Path.home() / "AppData" / "Local" / "uv" / "cache"
    home = Path.home()
    if sys.platform == "darwin":
        legacy = home / ".cache" / "uv"
        try:
            if legacy.exists() or legacy.is_symlink():
                return legacy
        except TimeoutError:
            raise
        except OSError:
            pass
        return home / "Library" / "Caches" / "uv"
    return home / ".cache" / "uv"


def _device(path):
    probe = Path(path)
    while True:
        try:
            return os.stat(probe).st_dev
        except FileNotFoundError:
            if probe.parent == probe:
                return None
            probe = probe.parent
        except TimeoutError:
            raise
        except OSError:
            return None


def same_volume_uv_cache(runtime, env):
    """Place uv's cache on the runtime device when the default crosses it.

    Cross-device caches turn ``clonefile``/hardlinks into a full copy of every
    generation. An explicit non-empty ``UV_CACHE_DIR`` always wins.
    """
    explicit = env.get("UV_CACHE_DIR")
    if isinstance(explicit, str) and explicit.strip():
        return env
    runtime = Path(runtime)
    target = _device(runtime)
    if target is None or _device(_uv_default_cache(env)) == target:
        return env
    chosen = runtime.parent / ".uv-cache"
    if _device(chosen) != target:
        return env
    env["UV_CACHE_DIR"] = str(chosen)
    return env


def readback(python, source, *, on_process=None, deadline_at=None):
    """Check the actual interpreter and noneditable installed adapter bytes."""
    script = """
import importlib.metadata as m, importlib.util, json, pathlib, sys
d = m.distribution('mindie-agent-cc')
direct = json.loads(d.read_text('direct_url.json') or '{}')
if direct.get('dir_info', {}).get('editable') is True:
    raise ValueError('editable adapter runtime is unsupported')
spec = importlib.util.find_spec('mindie_cc')
print(json.dumps({'scripts':str(pathlib.Path(spec.origin).parent),
                  'prefix':sys.prefix, 'version':d.version}))
"""
    output = _run(
        "runtime-readback", [str(python), "-I", "-c", script],
        on_process=on_process, timeout=60.0, deadline_at=deadline_at,
    )
    value = json.loads(output)
    from product_contract import native_version
    if value["version"] != native_version(source):
        raise ValueError("installed adapter version differs from the native plugin")
    scripts = Path(value["scripts"])
    if Path(value["prefix"]).resolve() != python.parent.parent.resolve():
        raise ValueError("runtime interpreter identity differs")
    for path in (Path(source) / "scripts").glob("*.py"):
        installed = scripts / path.name
        if not installed.is_file() or installed.read_bytes() != path.read_bytes():
            raise ValueError("installed adapter differs from native plugin: " + path.name)
    return scripts


def _run(stage, command, *, on_process=None, deadline_at=None, **kwargs):
    if deadline_at is not None:
        kwargs["deadline_at"] = deadline_at
    process = {}
    if on_process is not None:
        on_process(stage, None)
    try:
        output = bounded.run(command, receipt=process, **kwargs)
        if process.get("cleanup_failed"):
            raise RuntimeError("runtime preparation cleanup is not confirmed")
    except BaseException as exc:
        exc.mindie_stage = stage
        exc.add_note(f"MindIE preparation failed at {stage}; not retried.")
        if on_process is not None:
            try:
                on_process(stage, process)
            except Exception as recording:
                exc.add_note("preparation outcome recording failed: " + type(recording).__name__)
        raise
    if on_process is not None:
        on_process(stage, process)
    return output


def ensure_runtime(source, *, prepare=True, on_process=None, deadline_at=None):
    with bounded.operation(signals=True):
        return _ensure_runtime(
            source, prepare=prepare, on_process=on_process, deadline_at=deadline_at)


def _usable_python(value):
    if not isinstance(value, str) or not os.path.isabs(value):
        return None
    path = Path(value)
    return path if path.is_file() else None


def _component_override(source, python, current, *, active_python, on_process=None,
                        deadline_at=None):
    """Build the merged runtime from an already-installed interpreter.

    The child owns discovery, build, verification and its own bounded cleanup;
    a transport catalog failure keeps its existing typed classification.
    """
    state_dir = current.get("state_dir")
    if not isinstance(state_dir, str) or not os.path.isabs(state_dir):
        return None
    from component_update import typed_catalog_error
    try:
        output = _run(
            "component-floor",
            [str(python), "-I", "-B", str(Path(source) / "scripts" / "component_update.py"),
             str(source), str(python), str(Path(state_dir) / "component-runtimes"),
             str(active_python)],
            on_process=on_process, timeout=830, deadline_at=deadline_at,
            transport=True)
    except TimeoutError:
        raise
    except Exception as exc:
        raise typed_catalog_error(exc) from None
    effective = json.loads(output)
    return effective if effective else None


def _ensure_runtime(source, *, prepare, on_process=None, deadline_at=None):
    from paths import config_path
    config = config_path()
    current = json.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
    from product_contract import source_identity
    active = current.get("component_source")
    validation = current.get("product_validation")
    if (active and isinstance(validation, dict)
            and validation.get("source_sha256") == source_identity(source)):
        python = Path(current["python"])
        return python, readback(python, source, on_process=on_process, deadline_at=deadline_at)
    components = bool(active or current.get("component_commands"))
    current_python = _usable_python(current.get("python"))
    if components and current_python is not None:
        effective = _component_override(
            source, current_python, current, active_python=current_python,
            on_process=on_process, deadline_at=deadline_at)
        if effective:
            return Path(effective["python"]), Path(effective["scripts"])
        return _baseline_runtime(
            source, prepare=prepare, on_process=on_process, deadline_at=deadline_at)
    python, scripts = _baseline_runtime(
        source, prepare=prepare, on_process=on_process, deadline_at=deadline_at)
    if components:
        effective = _component_override(
            source, python, current, active_python=python,
            on_process=on_process, deadline_at=deadline_at)
        if effective:
            return Path(effective["python"]), Path(effective["scripts"])
    return python, scripts


def _runtime_candidates(source, source_sha256, lock_sha256):
    """Every acceptable runtime spelling, newest first, with its ready file."""
    candidates = []
    for name in runtime_key_names(runtime_key(source)):
        runtime = runtime_root() / name
        python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        expected = dict(key=name, python=str(python),
                        source_sha256=source_sha256, lock_sha256=lock_sha256,
                        abi=sys.implementation.cache_tag,
                        platform=sysconfig.get_platform())
        candidates.append((runtime, python, expected,
                           runtime.with_suffix(".ready.json")))
    return candidates


def _completed_runtime(candidates):
    """Return the first completed candidate, refusing a mismatched identity."""
    mismatch = None
    for runtime, python, expected, complete in candidates:
        if not complete.is_file():
            continue
        if json.loads(complete.read_text(encoding="utf-8")) == expected:
            return runtime, python, expected, complete
        mismatch = mismatch or complete
    if mismatch is not None:
        raise ValueError("completed runtime identity differs; not repaired")
    return None


def _baseline_runtime(source, *, prepare, on_process=None, deadline_at=None):
    source = Path(source).resolve()
    if not (source / "uv.lock").is_file():
        raise ValueError("the native plugin does not contain its complete uv.lock")
    lock_sha256 = hashlib.sha256((source / "uv.lock").read_bytes()).hexdigest()
    from product_contract import source_identity
    source_sha256 = source_identity(source)
    candidates = _runtime_candidates(source, source_sha256, lock_sha256)
    completed = _completed_runtime(candidates)
    runtime, python, expected, complete = completed or candidates[0]
    adapter = {"state_dir": str(runtime.parent)}
    lock_timeout = 120
    if deadline_at is not None:
        remaining = deadline_at - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("session-start budget exhausted before runtime lock")
        lock_timeout = min(lock_timeout, remaining)
    with OperationLock(adapter, bootstrap=True).exclusive(timeout=lock_timeout):
        completed = _completed_runtime(candidates)
        if completed is not None:
            runtime, python, expected, complete = completed
            return python, readback(
                python, source, on_process=on_process, deadline_at=deadline_at)
        runtime, python, expected, complete = candidates[0]
        if not prepare:
            raise RuntimeError("runtime is not prepared; start a new Claude session")
        from paths import which_host_binary
        uv = which_host_binary("uv", which_fn=shutil.which)
        if uv is None:
            raise FileNotFoundError("uv is required to prepare the locked MindIE runtime")
        env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(runtime), UV_NO_PROGRESS="1",
                   GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
        env = same_volume_uv_cache(runtime, env)
        if "UV_SYSTEM_CERTS" not in env and "UV_NATIVE_TLS" not in env:
            env["UV_SYSTEM_CERTS"] = "true"
            env["UV_NATIVE_TLS"] = "true"
        elif "UV_SYSTEM_CERTS" not in env:
            env.setdefault("UV_NATIVE_TLS", "true")
        if "GIT_CONFIG_COUNT" not in env:
            git_pairs = (
                ("core.longpaths", "true"),
                ("core.autocrlf", "false"),
                ("credential.https://github.com.provider", "github"),
            )
            env["GIT_CONFIG_COUNT"] = str(len(git_pairs))
            for idx, (k, v) in enumerate(git_pairs):
                env[f"GIT_CONFIG_KEY_{idx}"] = k
                env[f"GIT_CONFIG_VALUE_{idx}"] = v
        env.pop("PYTHONPATH", None)
        _run("locked-runtime-install",
             [uv, "sync", "--project", str(source), "--locked", "--no-editable",
              "--no-dev", "--python", sys.executable],
             env=env, on_process=on_process, timeout=540.0, deadline_at=deadline_at,
             transport=True)
        scripts = readback(python, source, on_process=on_process, deadline_at=deadline_at)
        atomic_write(complete, expected)
        return python, scripts


SESSION_START_INNER_ENV = "MINDIE_SESSION_START_INNER"


def _reexec_python313(argv):
    from paths import which_host_binary
    uv = which_host_binary("uv", which_fn=shutil.which)
    if uv is None:
        raise FileNotFoundError("uv is required to prepare the locked MindIE runtime")
    os.environ[SESSION_START_INNER_ENV] = "1"
    command = [uv, "--native-tls", "run", "--no-project", "--python", "3.13",
               "python", str(Path(__file__).resolve()), *argv]
    bounded.exec_or_wait(command, dict(os.environ))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    source = Path(__file__).resolve().parents[1]
    prepare = not (argv[:1] == ["hook"] and argv[1:2] != ["session-start"])
    if not prepare:
        raise RuntimeError("release entry is not prepared; start a new Claude session")
    deadline_at = None
    if argv[:1] == ["hook"] and argv[1:2] == ["session-start"]:
        import session_start
        inner = os.environ.get(SESSION_START_INNER_ENV) == "1"
        deadline_at = session_start.arm_deadline(inner)
        if not inner:
            _reexec_python313(argv)
            return
    python, scripts = ensure_runtime(source, prepare=True, deadline_at=deadline_at)
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    if deadline_at is not None:
        env["MINDIE_SESSION_START_DEADLINE_MONOTONIC"] = str(deadline_at)
    command = [str(python), str(scripts / "mindie_launch.py"),
               "--source", str(source), *argv]
    bounded.exec_or_wait(command, env)


if __name__ == "__main__":
    main()
