from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures"


def env_for(config=None, extra=None):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    if config is not None:
        env["MINDIE_CC_CONFIG"] = str(config)
    if extra:
        env.update(extra)
    return env


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")
    return path


def make_config(tmp: Path, *, sharing=False, roots=None):
    config = tmp / "cc.json"
    engine = tmp / "cc.engine.json"
    community = tmp / "cc.community.json"
    root = tmp / "domain"
    admission = root / "admission.sqlite3"
    write_json(
        engine,
        dict(
            root=str(root),
            domain="vllm-ascend",
            admission_path=str(admission),
            transcript_adapter=str(SCRIPTS / "transcript.py"),
            community_config=str(community),
        ),
    )
    write_json(
        community,
        dict(
            schema="mindie-community-config/1",
            enabled=sharing,
            generation="gen-test",
            enabled_at=1.0 if sharing else None,
            repository="owner/repo" if sharing else "local/unconfigured",
            branch="main",
            project_roots=roots or ([str(tmp / "project")] if sharing else []),
            idle_seconds=300,
        ),
    )
    if sharing:
        (tmp / "project").mkdir(parents=True, exist_ok=True)
    write_json(
        config,
        dict(
            python=sys.executable,
            engine_config=str(engine),
            community_config=str(community),
            state_dir=str(tmp / "state"),
        ),
    )
    return config
