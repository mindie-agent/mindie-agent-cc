#!/usr/bin/env python3
"""Materialize this native plugin's locked runtime, then leave host selection alone.

[Role]
Prepares and validates the locked Python virtual environment (`uv sync --locked`)
for `mindie-agent-cc` and invokes `setup.py` to record the installation binding.

[Invariants]
- Runtime directory is keyed by `native_version` and `source_identity` digest.
- Never alters Claude Code's own marketplace selection or unrelated plugins.

[Timeouts & Locks]
- Acquires `OperationLock` and runs subprocesses through `bounded.run` with
  explicit deadlines (`BOOTSTRAP_TOTAL = 540.0s` from `mindie_launch.py`).

[Side-Effect Policy]
- Materializes the isolated runtime under `runtime_root()` and writes atomic
  installation state via `setup.py`.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import sysconfig

import bounded
from install_state import OperationLock
from product_contract import native_version, package_files, source_identity
from install_state import atomic_write


def runtime_root():
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "mindie-agent" / "runtimes" / "cc"


def runtime_key(source):
    source = Path(source).resolve()
    digest = hashlib.sha256()
    for path in sorted(package_files(source)):
        digest.update(path.relative_to(source).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    digest.update(f"{sys.implementation.cache_tag}:{sysconfig.get_platform()}".encode())
    return digest.hexdigest()


def readback(python, source, *, on_process=None):
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
        on_process=on_process, timeout=60.0,
    )
    value = json.loads(output)
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


def _run(stage, command, *, on_process=None, **kwargs):
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


def ensure_runtime(source, *, prepare=True, on_process=None):
    with bounded.operation(signals=True):
        return _ensure_runtime(source, prepare=prepare, on_process=on_process)


def _ensure_runtime(source, *, prepare, on_process=None):
    from paths import config_path
    config = config_path()
    current = json.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
    active = current.get("component_source")
    if active and current["product_validation"]["source_sha256"] == source_identity(source):
        python = Path(current["python"])
        return python, readback(python, source, on_process=on_process)
    python, scripts = _baseline_runtime(source, prepare=prepare, on_process=on_process)
    if active or current.get("component_commands"):
        output = _run("component-floor", [str(python), "-I", "-B", str(scripts / "component_update.py"),
                      str(source), str(python), str(Path(current["state_dir"]) / "component-runtimes"),
                      current["python"]], on_process=on_process, timeout=830)
        effective = json.loads(output)
        if effective:
            return Path(effective["python"]), Path(effective["scripts"])
    return python, scripts


def _baseline_runtime(source, *, prepare, on_process=None):
    source = Path(source).resolve()
    if not (source / "uv.lock").is_file():
        raise ValueError("the native plugin does not contain its complete uv.lock")
    runtime = runtime_root() / runtime_key(source)
    python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    adapter = {"state_dir": str(runtime.parent)}
    with OperationLock(adapter, bootstrap=True).exclusive(timeout=120):
        complete = runtime.with_suffix(".ready.json")
        expected = dict(key=runtime.name, python=str(python),
                        source_sha256=source_identity(source),
                        lock_sha256=hashlib.sha256((source / "uv.lock").read_bytes()).hexdigest(),
                        abi=sys.implementation.cache_tag, platform=sysconfig.get_platform())
        if complete.is_file():
            if json.loads(complete.read_text(encoding="utf-8")) != expected:
                raise ValueError("completed runtime identity differs; not repaired")
            return python, readback(python, source, on_process=on_process)
        if not prepare:
            raise RuntimeError("runtime is not prepared; start a new Claude session")
        uv = shutil.which("uv")
        if uv is None:
            raise FileNotFoundError("uv is required to prepare the locked MindIE runtime")
        env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(runtime), UV_NO_PROGRESS="1",
                   GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
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
             env=env, on_process=on_process, timeout=540.0)
        scripts = readback(python, source, on_process=on_process)
        atomic_write(complete, expected)
        return python, scripts


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    source = Path(__file__).resolve().parents[1]
    prepare = not (argv[:1] == ["hook"] and argv[1:2] != ["session-start"])
    if not prepare:
        raise RuntimeError("release entry is not prepared; start a new Claude session")
    python, scripts = ensure_runtime(source, prepare=True)
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    command = [str(python), str(scripts / "mindie_launch.py"),
               "--source", str(source), *argv]
    bounded.exec_or_wait(command, env)


if __name__ == "__main__":
    main()
