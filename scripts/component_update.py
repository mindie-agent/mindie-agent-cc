#!/usr/bin/env python3
"""Legacy pre-0.1.30 component-override handoff shim for `bootstrap.ensure_runtime`.

[Role] Compares `current_python` component versions against `source/uv.lock`; returns `null` when `source` already meets or exceeds all floors, or builds a floor-preserving runtime under `root`.
[Invariants] Pure Python stdlib (invoked under `v0.1.29`'s interpreter before `mindie_harness` is guaranteed installed); every child spawn sets `CREATE_NO_WINDOW`.
[Timeouts & Locks] Bounded `60s` inventory probe and `540s` `uv` sync/override when a higher floor exists.
[Side-Effect Policy] Read-only returning `null` in normal upgrades; writes only under `root` when preserving a newer floor.
"""
import argparse, hashlib, json, os, shutil, subprocess, sys, tomllib
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_INV = (
    "import importlib.metadata as m,json;r={};"
    "[r.__setitem__((d.metadata.get('Name') or '').strip().lower(),{'version':d.version,'direct':json.loads(d.read_text('direct_url.json') or '{}')}) "
    "for d in m.distributions() if (d.metadata.get('Name') or '').strip().lower().startswith('mindie-') or (d.metadata.get('Name') or '').strip().lower() in ('remote-dev','npu-top')];"
    "print(json.dumps(r))"
)


def _ver(raw: str) -> tuple[int, ...]:
    return tuple(int("".join(ch for ch in part if ch.isdigit()) or 0) for part in str(raw).split(".")[:3])


def _spec(name: str, row: dict) -> str:
    direct = row.get("direct") if isinstance(row.get("direct"), dict) else {}
    url = direct.get("url")
    rev = (direct.get("vcs_info") or {}).get("commit_id") if isinstance(direct.get("vcs_info"), dict) else None
    if isinstance(url, str) and url and isinstance(rev, str) and rev:
        return f"{name} @ git+{url}@{rev}"
    if isinstance(url, str) and url.endswith(".whl"):
        return f"{name} @ {url}"
    return f"{name}>={row['version']}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="component_update.py")
    parser.add_argument("source", type=Path)
    parser.add_argument("python", type=Path)
    parser.add_argument("root", type=Path)
    parser.add_argument("current_python", nargs="?")
    args = parser.parse_args(argv)
    source = args.source.resolve()
    if not (source / "pyproject.toml").is_file():
        raise FileNotFoundError("candidate pyproject.toml is missing")
    if not args.python.is_file():
        raise FileNotFoundError("candidate python interpreter is missing")
    cur_py = Path(args.current_python) if args.current_python else args.python
    locked = {pkg["name"].lower(): pkg["version"] for pkg in tomllib.loads((source / "uv.lock").read_text(encoding="utf-8")).get("package", [])}
    probe = subprocess.run([str(cur_py), "-I", "-c", _INV], capture_output=True, text=True, timeout=60, check=False, creationflags=CREATE_NO_WINDOW)
    installed = json.loads(probe.stdout) if probe.returncode == 0 and probe.stdout.strip() else {}
    higher = {name: row for name, row in installed.items() if name != "mindie-agent-cc" and name in locked and _ver(row.get("version", "0")) > _ver(locked[name])}
    if not higher:
        print(json.dumps(None))
        return 0
    uv = shutil.which("uv")
    if not uv:
        raise FileNotFoundError("uv is required to preserve component floors")
    key = hashlib.sha256(json.dumps({"source": str(source), "higher": {k: v.get("version") for k, v in higher.items()}}, sort_keys=True).encode()).hexdigest()[:16]
    venv = args.root.resolve() / key / ".venv"
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["UV_PROJECT_ENVIRONMENT"] = str(venv)
    subprocess.run([uv, "sync", "--project", str(source), "--locked", "--no-editable", "--no-dev", "--python", sys.executable], env=env, stdin=subprocess.DEVNULL, stdout=sys.stderr, stderr=sys.stderr, timeout=540, check=True, creationflags=CREATE_NO_WINDOW)
    for name, row in sorted(higher.items()):
        subprocess.run([uv, "pip", "install", "--python", str(py), "--no-deps", _spec(name, row)], env=env, stdin=subprocess.DEVNULL, stdout=sys.stderr, stderr=sys.stderr, timeout=240, check=True, creationflags=CREATE_NO_WINDOW)
    scripts_probe = subprocess.run([str(py), "-I", "-c", "import importlib.util,pathlib;print(pathlib.Path(importlib.util.find_spec('mindie_cc').origin).parent)"], capture_output=True, text=True, timeout=30, check=True, creationflags=CREATE_NO_WINDOW)
    print(json.dumps({"python": str(py), "scripts": scripts_probe.stdout.strip(), "source": str(source)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
