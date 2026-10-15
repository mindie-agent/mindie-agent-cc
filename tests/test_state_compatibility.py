"""Durable state is checked before a switch and is not rewritten on failure.

The selected interpreter, sys.executable, imports the installed knowledge
layout. CI installs the pinned runtime there. Fixtures are synthetic and
use the adapter path that mcp_server.remote_call writes:

    paths.state_dir(adapter) / "remote" / <native session>

A consumed job and an uncertain receipt stay at that path. The check runs
before any service retirement or install, and a rejection creates no
replacement directory. No shared environment, user profile, or receipt is
modified outside the temporary directory. Native host acceptance is not
claimed.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bounded
import paths
import receipt_layout

CURRENT = Path(sys.executable)
HELPER = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import state_compatibility
try:
    print(json.dumps(state_compatibility.check(sys.argv[2], sys.argv[3])))
except Exception as exc:
    print(json.dumps({"status": "rejected", "error": type(exc).__name__, "detail": str(exc)[:300]}))
    raise SystemExit(1)
"""


def _run(engine, remote_base):
    completed = subprocess.run(
        [str(CURRENT), "-c", HELPER, str(SCRIPTS), str(engine), str(remote_base)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
    )
    payload = json.loads(completed.stdout)
    return completed.returncode, payload


def _engine(root, **extra):
    value = dict(root=str(root / "data"), domain="vllm-ascend")
    value.update(extra)
    path = root / "engine.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _adapter(root):
    state = root / "adapter-state"
    return dict(
        state_dir=str(state),
        engine_config=str(root / "engine.json"),
    )


def _knowledge(root, *, fmt=1, release="0.0.1", body="kept-body"):
    base = root / "data" / "vllm-ascend"
    state = base / ("state-v" + str(fmt))
    state.mkdir(parents=True)
    database = state / "state-v4.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE bodies(body TEXT)")
    connection.execute("INSERT INTO bodies VALUES (?)", (body,))
    connection.commit()
    connection.close()
    layout = dict(schema="mindie-knowledge-layout/1", format=fmt, release_version=release)
    (base / "state-layout.json").write_text(json.dumps(layout, sort_keys=True) + "\n", encoding="utf-8")
    return database


def _session(root, *, body="paid-result", state="consumed"):
    """One in-place native session, exactly where remote_call writes it."""
    base = Path(_adapter(root)["state_dir"]) / "remote"
    session = base / "native-session"
    jobs = session / "jobs"
    jobs.mkdir(parents=True)
    job = jobs / "job-1.json"
    job.write_text(json.dumps({
        "schema_version": "remote-dev.job.v1",
        "job_id": "job-1",
        "result": body,
        "state": state,
        "submission_state": "uncertain" if state == "uncertain" else "acknowledged",
    }), encoding="utf-8")
    return base, job


def _layout(base, *, fmt=1, release="0.0.1"):
    value = dict(schema="mindie-cc-remote-layout/1", format=fmt, release_version=release)
    path = base / "remote-layout.json"
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _snapshot(root):
    data = root / "data"
    remote = Path(_adapter(root)["state_dir"]) / "remote"
    return (
        sorted(path.relative_to(root).as_posix() for path in data.rglob("*")),
        sorted(path.relative_to(root).as_posix() for path in remote.rglob("*")),
        {path.relative_to(root).as_posix(): path.read_bytes()
         for path in list(data.rglob("*")) + list(remote.rglob("*")) if path.is_file()},
    )


class StateCompatibilityTests(unittest.TestCase):






    def test_same_released_format_is_compatible_and_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = _knowledge(root)
            remote, job = _session(root, body="paid-result", state="consumed")
            layout = _layout(remote)
            before = (database.read_bytes(), job.read_bytes(), layout.read_bytes())
            code, payload = _run(_engine(root), remote)
            self.assertEqual(code, 0, payload)
            self.assertEqual(payload, {"status": "compatible"})
            self.assertEqual((database.read_bytes(), job.read_bytes(), layout.read_bytes()), before)
            self.assertEqual(job.parent.parent.name, "native-session")

    def test_released_foreign_format_fails_before_any_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = _knowledge(root, fmt=2, release="0.0.1", body="do-not-reset")
            remote, job = _session(root, body="uncertain-receipt", state="uncertain")
            _layout(remote, fmt=9, release="0.0.1")
            before = _snapshot(root)
            code, payload = _run(_engine(root), remote)
            self.assertEqual(code, 1)
            self.assertEqual(payload["status"], "rejected")
            self.assertIn("migration", payload["detail"])
            self.assertEqual(_snapshot(root), before)
            self.assertEqual(job.read_text(encoding="utf-8").count("uncertain-receipt"), 1)
            self.assertFalse((remote / "session-v1").exists())
            self.assertFalse((remote / "gate-v1").exists())
            self.assertFalse((root / "remote-receipts").exists())

    def test_unknown_layout_preserves_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = _knowledge(root)
            remote, job = _session(root)
            (root / "data" / "vllm-ascend" / "state-layout.json").write_text("{", encoding="utf-8")
            before = (database.read_bytes(), job.read_bytes())
            code, payload = _run(_engine(root), remote)
            self.assertEqual(code, 1)
            self.assertIn("preserved", payload["detail"])
            self.assertEqual((database.read_bytes(), job.read_bytes()), before)
            self.assertFalse((remote / "remote-layout.json").exists())

    def test_missing_layout_rejects_an_occupied_session_without_moving_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _knowledge(root)
            remote, job = _session(root, body="still-here", state="uncertain")
            before = job.read_bytes()
            code, payload = _run(_engine(root), remote)
            self.assertEqual(code, 1)
            self.assertIn("missing", payload["detail"])
            self.assertEqual(job.read_bytes(), before)
            self.assertEqual(job.parent.parent, remote / "native-session")

    def test_development_null_release_same_format_is_compatible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _knowledge(root, release=None)
            remote, _job = _session(root)
            _layout(remote, release=None)
            code, payload = _run(_engine(root), remote)
            self.assertEqual(code, 0, payload)

    def test_layout_is_durable_before_an_uncertain_remote_operation(self):
        with tempfile.TemporaryDirectory() as tmp:
            remote = Path(tmp) / "remote"
            with self.assertRaisesRegex(RuntimeError, "submission uncertain"):
                with receipt_layout.prepare_layout(remote) as selected:
                    self.assertEqual(selected, remote)
                    self.assertEqual(
                        json.loads((remote / "remote-layout.json").read_text())["release_version"],
                        "0.0.1",
                    )
                    jobs = remote / "native-session" / "jobs"
                    jobs.mkdir(parents=True)
                    (jobs / "job.json").write_text('{"submission_state":"uncertain"}')
                    raise RuntimeError("submission uncertain")
            self.assertEqual(receipt_layout.receipt_root(remote), remote)
            self.assertIn("uncertain", (jobs / "job.json").read_text())

    def test_foreign_development_layout_is_not_relabelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            remote, job = _session(Path(tmp), state="uncertain")
            layout = _layout(remote, fmt=2, release=None)
            before = (layout.read_bytes(), job.read_bytes())
            with self.assertRaisesRegex(ValueError, "migration"):
                with receipt_layout.prepare_layout(remote):
                    self.fail("foreign layout accepted")
            self.assertEqual((layout.read_bytes(), job.read_bytes()), before)



if __name__ == "__main__":
    unittest.main()
