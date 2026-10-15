"""Finite migration of the retired updater; never install, retry, or select code."""

import json
import os
from pathlib import Path
import plistlib
import shlex
import stat
import subprocess
import sys

from install_state import _Held


def _read(path):
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 1024 * 1024:
        raise ValueError("legacy update state must be bounded regular metadata")
    with path.open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("legacy update state exceeds its metadata bound")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("legacy update state is not an object; retained for inspection")
    return value


def status(adapter):
    root = Path(adapter["state_dir"]) / "update"
    values = {name: _read(root / (name + ".json")) for name in ("current", "status", "failed")}
    if not any(value is not None for value in values.values()):
        return None
    receipt = (values["status"] or {}).get("native_install")
    return dict(status="retained", automatic_retry=False, records=str(root),
                native_install=receipt, previous_generation=(values["current"] or {}).get("sha"))


def _owned(argv, adapter, config):
    root = Path(adapter["state_dir"]) / "update" / "launch"
    if len(argv) != 6 or argv[2:] != ["--config", str(config), "updater", "check"]:
        raise ValueError("legacy task does not match this profile; not removed")
    launcher = Path(argv[1]).resolve()
    if launcher.name != "mindie_launch.py" or root.resolve() not in launcher.parents:
        raise ValueError("legacy task executable is not this profile's retained launcher")


def _native(argv):
    return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=30)


def remove_schedule(adapter, config, *, label="agent.mindie.cc-update", plist_path=None,
                    task="MindIECCUpdate"):
    if sys.platform == "darwin":
        path = Path(plist_path or Path.home() / "Library/LaunchAgents" / (label + ".plist"))
        if not path.is_file():
            probe = _native(["launchctl", "print", f"gui/{os.getuid()}/{label}"])
            if probe.returncode != 113:
                raise RuntimeError("legacy task ownership cannot be established without its plist")
            return "absent"
        value = plistlib.loads(path.read_bytes())
        if value.get("Label") != label:
            raise ValueError("legacy task label differs; not removed")
        _owned(value.get("ProgramArguments", []), adapter, config)
        target = f"gui/{os.getuid()}/{label}"
        before = _native(["launchctl", "print", target])
        if before.returncode not in {0, 113}:
            raise RuntimeError("legacy task state is unavailable; not removed")
        if before.returncode == 0:
            _native(["launchctl", "bootout", target])
        after = _native(["launchctl", "print", target])
        if after.returncode != 113:
            raise RuntimeError("legacy task removal is unconfirmed; do not repeat")
        path.unlink()
        return "removed"
    if os.name == "nt":
        name = task.replace("'", "''")
        query = (
            "$ErrorActionPreference='Stop';"
            "$s=New-Object -ComObject Schedule.Service;$s.Connect();$f=$s.GetFolder('\\');"
            f"try{{$t=$f.GetTask('{name}')}}catch{{"
            "if($_.Exception.HResult -eq -2147024894){Write-Output 'null';exit 0};throw};"
            "$a=$t.Definition.Actions.Item(1);"
            "@{execute=$a.Path;arguments=$a.Arguments;count=$t.Definition.Actions.Count}|ConvertTo-Json -Compress"
        )
        before = _native(["powershell", "-NoProfile", "-NonInteractive", "-Command", query])
        if before.returncode:
            raise RuntimeError("legacy task query failed; no removal attempted")
        row = json.loads(before.stdout)
        if row is None:
            return "absent"
        if row["count"] != 1:
            raise ValueError("legacy task has unexpected actions; not removed")
        # The old registrar always quoted every argument with double quotes.
        args = [part[1:-1] if part.startswith('"') and part.endswith('"') else part
                for part in shlex.split(row["arguments"], posix=False)]
        _owned([row["execute"], *args], adapter, config)
        mutation = query.split("try{", 1)[0] + f"$f.DeleteTask('{name}',0)"
        _native(["powershell", "-NoProfile", "-NonInteractive", "-Command", mutation])
        after = _native(["powershell", "-NoProfile", "-NonInteractive", "-Command", query])
        if after.returncode or json.loads(after.stdout) is not None:
            raise RuntimeError("legacy task removal is unconfirmed; do not repeat")
        return "removed"
    # The old product never installed a Linux task.
    return "not-applicable"


def migrate(adapter, config):
    lock = Path(adapter["state_dir"]) / "update" / "check.lock"
    with _Held(lock, True, 0):
        view = status(adapter)
        if view is None:
            return dict(status="not-needed")
        removed = remove_schedule(adapter, config)
        receipt = view.get("native_install")
        if isinstance(receipt, dict) and receipt.get("outcome") == "unknown":
            raise RuntimeError("legacy native mutation remains unknown; scheduler is "
                               "disabled, original records retained; inspect native plugin list")
    return dict(view, scheduler=removed)
