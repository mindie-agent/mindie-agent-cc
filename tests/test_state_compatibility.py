"""Durable state is checked before a switch and is not rewritten on failure.

The current-core interpreter is used only to import its state declaration.
Fixtures are synthetic. No shared environment, user profile, or receipt is
modified outside the temporary directory.
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CURRENT = Path("/Users/maoxx241/code/mindie-release-20261014/test-venv/bin/python")
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
HELPER = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import state_compatibility
try:
    print(json.dumps(state_compatibility.check(sys.argv[2])))
except Exception as exc:
    print(json.dumps({"status": "rejected", "error": type(exc).__name__, "detail": str(exc)[:300]}))
    raise SystemExit(1)
"""


def _run(root, engine):
    completed = subprocess.run(
        [str(CURRENT), "-c", HELPER, str(SCRIPTS), str(engine)],
        cwd=root, capture_output=True, text=True, timeout=30,
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


def _receipts(root, *, fmt=1, release="0.0.1", body="paid-result"):
    base = root / "receipts"
    directory = base / ("gate-v" + str(fmt))
    directory.mkdir(parents=True)
    receipt = directory / "job.json"
    receipt.write_text(json.dumps({"result": body, "cursor": 7}), encoding="utf-8")
    layout = dict(schema="mindie-remote-layout/1", format=fmt, release_version=release)
    (base / "gate-layout.json").write_text(json.dumps(layout, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


@unittest.skipUnless(CURRENT.is_file(), "current core interpreter is absent")
class StateCompatibilityTests(unittest.TestCase):
    def test_same_released_format_is_compatible_and_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = _knowledge(root)
            receipt = _receipts(root)
            before = (database.read_bytes(), receipt.read_bytes())
            code, payload = _run(root, _engine(root, remote_receipt_dir=str(root / "receipts")))
            self.assertEqual(code, 0, payload)
            self.assertEqual(payload, {"status": "compatible"})
            self.assertEqual((database.read_bytes(), receipt.read_bytes()), before)

    def test_released_foreign_format_fails_before_any_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = _knowledge(root, fmt=2, release="0.0.1", body="do-not-reset")
            receipt = _receipts(root, fmt=9, release="0.0.1", body="uncertain-receipt")
            before = (
                database.read_bytes(), receipt.read_bytes(),
                list((root / "data").rglob("*")), list((root / "receipts").rglob("*")),
            )
            code, payload = _run(root, _engine(root, remote_receipt_dir=str(root / "receipts")))
            self.assertEqual(code, 1)
            self.assertEqual(payload["status"], "rejected")
            self.assertIn("migration", payload["detail"])
            after = (
                database.read_bytes(), receipt.read_bytes(),
                list((root / "data").rglob("*")), list((root / "receipts").rglob("*")),
            )
            self.assertEqual(after[0], before[0])
            self.assertEqual(after[1], before[1])
            self.assertEqual([path.name for path in after[2]], [path.name for path in before[2]])
            self.assertEqual([path.name for path in after[3]], [path.name for path in before[3]])

    def test_unknown_layout_preserves_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = _knowledge(root)
            (root / "data" / "vllm-ascend" / "state-layout.json").write_text("{", encoding="utf-8")
            before = database.read_bytes()
            code, payload = _run(root, _engine(root, remote_receipt_dir=str(root / "empty-receipts")))
            self.assertEqual(code, 1)
            self.assertIn("preserved", payload["detail"])
            self.assertEqual(database.read_bytes(), before)
            self.assertFalse((root / "empty-receipts").exists())

    def test_development_null_release_same_format_is_compatible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _knowledge(root, release=None)
            code, payload = _run(root, _engine(root, remote_receipt_dir=str(root / "empty")))
            self.assertEqual(code, 0, payload)


if __name__ == "__main__":
    unittest.main()
