"""Outer launcher diagnostics. Not native acceptance."""

import json
import os
import subprocess
import sys
from pathlib import Path

from support import env_for, make_config

ROOT = Path(__file__).resolve().parents[1]
LAUNCH = ROOT / "scripts" / "mindie_launch.py"


def _run(tmp, env, payload=b""):
    env = dict(env)
    env["MINDIE_DIAGNOSTICS_ROOT"] = str(tmp / "diag")
    proc = subprocess.run(
        [sys.executable, str(LAUNCH), "hook", "stop"],
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=5,
    )
    assert proc.returncode == 0, proc.stderr.decode()
    assert json.loads(proc.stdout.decode() or "{}") == {}
    events = list((tmp / "diag").rglob("*.jsonl"))
    return "\n".join(path.read_text() for path in events)


def test_missing_config_and_explicit_off_stay_quiet(tmp_path):
    env = os.environ.copy()
    env.pop("MINDIE_CC_CONFIG", None)
    env.pop("PYTHONPATH", None)
    env["XDG_CONFIG_HOME"] = str(tmp_path / "empty-config")
    assert _run(tmp_path, env) == ""

    config = make_config(tmp_path / "off", sharing=False)
    assert _run(tmp_path / "off", env_for(config)) == ""


def test_existing_bad_community_and_missing_generation_are_recorded(tmp_path):
    bad = tmp_path / "bad"
    config = make_config(bad, sharing=True)
    (bad / "cc.community.json").write_text("{")
    text = _run(bad, env_for(config))
    assert "configuration" in text
    assert "forwarded" not in text

    armed = tmp_path / "armed"
    config = make_config(armed, sharing=True)
    text = _run(armed, env_for(config))
    assert "generation-unavailable" in text
    assert "forwarded" not in text
