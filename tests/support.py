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
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return path


_REDACTOR = None


def redactor_path():
    """The real verified redactor binary, installed once per test process.

    The pinned scanner archive is hash-verified; the lane cache avoids a
    download per case. Capture tests exercise actual redaction, never a stub.
    """
    global _REDACTOR
    if _REDACTOR is None:
        from mindie_knowledge.loop.transcript_redaction import install_scanner

        cache = ROOT.parent / "state" / "component-tests" / "redactor-cache"
        _REDACTOR = install_scanner(cache=cache)
    return _REDACTOR


def store_file(tmp: Path) -> Path:
    """The current shared-core store location for a make_config domain root."""
    return tmp / "domain" / "vllm-ascend" / "state-v1" / "state-v4.sqlite3"


def make_config(tmp: Path, *, sharing=False, roots=None, summary_command=None):
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
            capture_mode="public-transcript",
            redactor_executable=redactor_path(),
            summary_command=summary_command or [sys.executable, str(SCRIPTS / "organizer.py")],
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
            consent_config=str(tmp / "mindie-consent.json"),
        ),
    )
    if sharing:
        (tmp / "project").mkdir(parents=True, exist_ok=True)
    write_json(
        config,
        dict(
            schema="mindie-cc-installation/1",
            facade_protocol=1,
            python=sys.executable,
            runtime_scripts=str(SCRIPTS),
            adapter_config=str(config),
            base_config=str(config),
            engine_config=str(engine),
            community_config=str(community),
            state_dir=str(tmp / "state"),
        ),
    )
    return config
