#!/usr/bin/env python3
"""Materialize this native plugin's locked runtime, then leave host selection alone."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig

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


def readback(python, source):
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
    result = subprocess.run([str(python), "-I", "-c", script],
                            capture_output=True, text=True, check=True)
    value = json.loads(result.stdout)
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


def ensure_runtime(source, *, prepare=True):
    source = Path(source).resolve()
    if not (source / "uv.lock").is_file():
        raise ValueError("the native plugin does not contain its complete uv.lock")
    runtime = runtime_root() / runtime_key(source)
    python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    adapter = {"state_dir": str(runtime.parent)}
    with OperationLock(adapter).exclusive(timeout=120):
        complete = runtime.with_suffix(".ready.json")
        expected = dict(key=runtime.name, python=str(python),
                        source_sha256=source_identity(source),
                        lock_sha256=hashlib.sha256((source / "uv.lock").read_bytes()).hexdigest(),
                        abi=sys.implementation.cache_tag, platform=sysconfig.get_platform())
        if complete.is_file():
            if json.loads(complete.read_text(encoding="utf-8")) != expected:
                raise ValueError("completed runtime identity differs; not repaired")
            return python, readback(python, source)
        if not prepare:
            raise RuntimeError("runtime is not prepared; start a new Claude session")
        uv = shutil.which("uv")
        if uv is None:
            raise FileNotFoundError("uv is required to prepare the locked MindIE runtime")
        env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(runtime), UV_NO_PROGRESS="1",
                   GIT_TERMINAL_PROMPT="0")
        env.pop("PYTHONPATH", None)
        subprocess.run([uv, "sync", "--project", str(source), "--locked", "--no-editable",
                        "--no-dev", "--python", sys.executable],
                       env=env, stdout=sys.stderr, stderr=sys.stderr, check=True)
        scripts = readback(python, source)
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
    os.execve(str(python), command, env)


if __name__ == "__main__":
    main()
