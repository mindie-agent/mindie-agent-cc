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
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bounded
import paths
import receipt_layout
import updater

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
    def _switch_fixture(self, root):
        import genstate

        engine = _engine(root)
        adapter = dict(_adapter(root), python=sys.executable, engine_config=str(engine))
        config = root / "current.adapter.json"
        config.write_text(json.dumps(adapter), encoding="utf-8")
        previous = root / "previous"
        previous.mkdir()
        current = dict(generation=str(previous), python=sys.executable,
                       adapter_config=str(config), sha="a" * 40)
        genstate.write_current(current, adapter)
        generation = root / "candidate"
        scripts = generation / "scripts"
        scripts.mkdir(parents=True)
        for name in ("state_compatibility.py", "receipt_layout.py", "capture_config.py"):
            shutil.copy2(SCRIPTS / name, scripts / name)
        gen_adapter = generation / "adapter.json"
        gen_adapter.write_text(json.dumps(adapter), encoding="utf-8")
        return adapter, current, generation, gen_adapter

    def test_switch_prepares_store_after_idle_and_preserves_paid_and_uncertain_results(self):
        import capture_config
        import genstate
        from mindie_knowledge.loop.store import Store
        from mindie_knowledge.state_layout import state_root

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter, current, generation, gen_adapter = self._switch_fixture(root)
            store = Store(root / "data", "vllm-ascend")
            store.close()
            database = state_root(root / "data", "vllm-ascend") / "state-v4.sqlite3"
            with sqlite3.connect(database) as db:
                db.execute(
                    "INSERT INTO material_summary_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("attempt", "task", "batch", "body", "input", "policy", "[]",
                     "outcome_unknown", '{"paid_result":"retained"}', "interrupted", 1, 2),
                )
                before = db.execute("SELECT * FROM material_summary_attempts").fetchall()
            remote, job = _session(root, body="external-result", state="uncertain")
            _layout(remote)
            before_job = job.read_bytes()
            order = []
            real_prepare = capture_config.prepare_store

            def idle(*_):
                order.append("idle")
                return {"idle": True, "service": "absent"}

            def prepare(python, scripts, engine):
                order.append("prepare")
                self.assertEqual(Path(scripts), generation / "scripts")
                self.assertEqual(engine["root"], str(root / "data"))
                with self.assertRaises(genstate.LockTimeout):
                    with genstate.OperationLock(adapter).shared(timeout=0.01):
                        self.fail("switch lock was not held")
                return real_prepare(python, scripts, engine)

            def install(*_):
                order.append("install")
                self.assertEqual(genstate.read_current(adapter), current)

            with patch.object(capture_config, "prepare_store", side_effect=prepare):
                updater.switch(adapter, current, generation, CURRENT, gen_adapter,
                               "b" * 40, time.monotonic() + 180,
                               idle, install, lock_timeout=1)
            self.assertEqual(order, ["idle", "prepare", "install"])
            with sqlite3.connect(database) as db:
                self.assertEqual(db.execute("SELECT * FROM material_summary_attempts").fetchall(), before)
            self.assertEqual(job.read_bytes(), before_job)
            self.assertEqual(genstate.read_current(adapter)["sha"], "b" * 40)

    def test_store_failure_prevents_native_mutation_and_restores_stopped_service(self):
        import genstate
        from mindie_knowledge.loop.store import Store
        from mindie_knowledge.state_layout import state_root

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter, current, generation, gen_adapter = self._switch_fixture(root)
            store = Store(root / "data", "vllm-ascend")
            store.close()
            database = state_root(root / "data", "vllm-ascend") / "state-v4.sqlite3"
            database.write_bytes(b"do-not-replace-damaged-paid-store")
            remote, job = _session(root, body="uncertain-external-result", state="uncertain")
            _layout(remote)
            before = _snapshot(root)
            # Core lock owner metadata may change during an attempted open;
            # durable layout, paid results and receipt bytes must not.
            before_files = {name: raw for name, raw in before[2].items()
                            if not name.endswith(".lock")}
            with patch.object(updater, "_restore_stopped_service") as restore, \
                    patch.object(updater, "native_install") as install:
                with self.assertRaisesRegex(RuntimeError, "knowledge store preparation failed"):
                    updater.switch(
                        adapter, current, generation, CURRENT, gen_adapter, "b" * 40,
                        time.monotonic() + 180,
                        lambda *_: {"idle": True, "service": "stopped"}, install, lock_timeout=1,
                    )
                install.assert_not_called()
                self.assertEqual(restore.call_args.args[:2], (adapter, current))
            self.assertEqual(genstate.read_current(adapter), current)
            after = _snapshot(root)
            self.assertEqual(after[:2], before[:2])
            self.assertEqual({name: raw for name, raw in after[2].items()
                              if not name.endswith(".lock")}, before_files)
            self.assertIn(b"uncertain-external-result", job.read_bytes())

    def test_busy_switch_does_not_prepare_store(self):
        import capture_config
        import genstate

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter, current, generation, gen_adapter = self._switch_fixture(root)
            with patch.object(capture_config, "prepare_store") as prepare, \
                    patch.object(updater, "native_install") as install:
                with self.assertRaisesRegex(updater.Deferred, "active work"):
                    updater.switch(
                        adapter, current, generation, CURRENT, gen_adapter, "b" * 40,
                        time.monotonic() + 180,
                        lambda *_: (_ for _ in ()).throw(updater.Deferred("active work")),
                        install, lock_timeout=1,
                    )
                prepare.assert_not_called()
                install.assert_not_called()
            self.assertEqual(genstate.read_current(adapter), current)
            self.assertFalse((root / "data").exists())

    def test_configured_remote_base_is_the_adapter_session_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter = _adapter(root)
            base = updater.remote_state_base(adapter)
            self.assertEqual(base, paths.state_dir(adapter) / "remote")
            self.assertTrue(base.is_absolute())
            self.assertNotIn("remote-receipts", base.parts)
            self.assertFalse(any(part.startswith("gate-v") for part in base.parts))

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

    def test_rejection_happens_before_retirement_or_install(self):
        """The no-touch check is the gate in front of service retirement."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = _knowledge(root, fmt=2, release="0.0.1", body="do-not-reset")
            remote, job = _session(root, body="uncertain-receipt", state="uncertain")
            _layout(remote, fmt=9, release="0.0.1")
            engine = _engine(root)
            adapter = _adapter(root)
            adapter["engine_config"] = str(engine)
            generation = root / "generation"
            (generation / "scripts").mkdir(parents=True)
            for name in ("state_compatibility.py", "receipt_layout.py"):
                target = generation / "scripts" / name
                target.write_bytes((SCRIPTS / name).read_bytes())
            def run_helper(argv, data="", **kwargs):
                completed = subprocess.run(
                    argv, input=data, capture_output=True, text=True,
                    timeout=kwargs.get("timeout") or 30, cwd=ROOT,
                    env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
                    check=False,
                )
                if completed.returncode != 0:
                    error = subprocess.CalledProcessError(completed.returncode, argv, completed.stdout, completed.stderr)
                    error.returncode = completed.returncode
                    error.mindie_output = completed.stdout
                    raise error
                return completed.stdout

            before = _snapshot(root)
            with patch.object(bounded, "run", side_effect=run_helper), \
                    patch.object(updater, "stop_if_idle") as retire, \
                    patch.object(updater, "native_install") as install:
                with self.assertRaisesRegex(updater.CheckFailed, "unconfirmed"):
                    updater.switch(
                        adapter, {}, generation, CURRENT, generation / "adapter.json",
                        "b" * 40, time.monotonic() + 120,
                        retire, install, lock_timeout=1,
                    )
                retire.assert_not_called()
                install.assert_not_called()
            self.assertEqual(_snapshot(root), before)
            self.assertEqual(database.read_bytes().count(b"do-not-reset"), 1)
            self.assertIn(b"uncertain", job.read_bytes())


if __name__ == "__main__":
    unittest.main()
