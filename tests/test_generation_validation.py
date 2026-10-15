"""Prepared generations retain exact receipts and revalidate cached runtimes.

Only publication fetch and a changed-runtime failure are controlled by the
fixture validator. Product identities, runtime imports, subprocess receipts,
generation config writes and cached source checks use the real implementation.
"""

import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from support import make_config, store_file

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import genstate
import product_contract
import updater

SHA = "b" * 40
CURRENT = Path(sys.executable)


class PreparedGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = make_config(self.root / "installed")
        self.adapter = json.loads(self.config.read_text(encoding="utf-8"))
        self.adapter.update(base_config=str(self.config), update_channel="main")
        self.engine_path = Path(self.adapter["engine_config"])
        engine = json.loads(self.engine_path.read_text(encoding="utf-8"))
        declaration, _ = product_contract.product(ROOT)
        previous = dict(declaration["publication"], contract_sha256="0" * 64, ref="old")
        engine["product_validation"] = dict(publication=previous)
        engine["feeds"] = [
            dict(repository=previous["repository"], domain=previous["domain"],
                 ref="old", contract_sha256="0" * 64, interval_seconds=900),
            dict(repository="custom/knowledge", domain="custom", ref="private-branch"),
        ]
        self.adapter["product_validation"] = dict(publication=previous)
        self.engine_path.write_text(json.dumps(engine), encoding="utf-8")
        self.config.write_text(json.dumps(self.adapter), encoding="utf-8")
        previous_generation = self.root / "previous"
        previous_generation.mkdir()
        self.current = dict(generation=str(previous_generation), python=str(CURRENT),
                            adapter_config=str(self.config), sha="a" * 40)
        genstate.write_current(self.current, self.adapter)
        from mindie_knowledge.loop.store import Store
        store = Store(engine["root"], engine["domain"])
        store.close()
        self.job = Path(self.adapter["state_dir"]) / "remote" / "native-session" / "jobs" / "paid.json"
        import receipt_layout
        with receipt_layout.prepare_layout(self.job.parents[2]):
            self.job.parent.mkdir(parents=True)
            self.job.write_text('{"submission_state":"uncertain","result":"paid-result"}')
        policy = self.root / "diagnostics.json"
        policy.write_text('{"decision":"disabled"}')
        environment = patch.dict(os.environ, {
            "MINDIE_CC_CONFIG": str(self.config),
            "MINDIE_DIAGNOSTICS_CONFIG": str(policy),
            "MINDIE_DIAGNOSTICS_ROOT": str(self.root / "diagnostics"),
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(self._close_diagnostics)
        self.source = self.root / "source"
        updater.snapshot_source(ROOT, self.source)
        scripts = self.source / "scripts"
        shutil.copy2(scripts / "candidate_validate.py", scripts / "fixture_validator.py")
        (scripts / "candidate_validate.py").write_text(
            "import sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0,str(Path(__file__).resolve().parent))\n"
            "import fixture_validator as real\n"
            "state=Path(__file__).resolve().parents[1]/'.venv'\n"
            "state.mkdir(exist_ok=True)\n"
            "def publication(_):\n"
            "    with (state/'publication-count').open('a') as stream: stream.write('fetch\\n')\n"
            "real.publication_contract=publication\n"
            "if (state/'changed-runtime').exists():\n"
            "    def incompatible(_):\n"
            "        raise real.CheckFailure('runtime_pins','revision_mismatch','mindie-knowledge')\n"
            "    real.installed_revisions=incompatible\n"
            "raise SystemExit(real.main())\n",
            encoding="utf-8",
        )
        venv_python = patch.object(updater, "_venv_python", return_value=CURRENT)
        venv_python.start()
        self.addCleanup(venv_python.stop)

    def _close_diagnostics(self):
        from mindie_diagnostics import logging

        # Negative checks use the real process-cached writer. Release only
        # this fixture's file handle before TemporaryDirectory removes it.
        key = ("mindie-agent-cc", str((self.root / "diagnostics").absolute()))
        with logging._LOCK:
            recorder = logging._FAILURE_RECORDERS.pop(key, None)
        if recorder is not None:
            recorder.close()

    def _stage(self):
        def local_git(args, **kwargs):
            if args[0] == "checkout":
                updater.snapshot_source(self.source, Path(kwargs["cwd"]))
            return ""

        with patch.object(updater, "_git", side_effect=local_git):
            return updater.stage_generation(
                SHA, "fixture-local-source", self.adapter, time.monotonic() + 180,
                build=lambda *_: CURRENT, release_version="0.0.1",
            )

    def _protected(self):
        return tuple(path.read_bytes() for path in (
            self.config, self.engine_path, store_file(self.config.parent),
            self.job, genstate.current_path(self.adapter),
        ))

    def _check_rejected(self, expected):
        before = self._protected()
        with patch.object(updater, "resolve_main", return_value=SHA), \
                patch.object(updater, "stop_if_idle") as idle, \
                patch.object(updater, "native_install") as install, \
                redirect_stdout(io.StringIO()) as output:
            code = updater._check_once(
                self.adapter, time.monotonic() + 180, force=True,
                build=lambda *_: self.fail("cached runtime was rebuilt"),
                idle=idle, install=install, lock_timeout=1,
            )
        self.assertEqual(code, 1, output.getvalue())
        self.assertIn(expected, output.getvalue())
        idle.assert_not_called()
        install.assert_not_called()
        self.assertEqual(self._protected(), before)
        self.assertTrue((genstate.generations_dir(self.adapter) / SHA / updater.COMPLETE).is_file())

    def test_final_source_receipt_and_feed_are_persisted_then_reused_without_fetch(self):
        generation, python, adapter_path = self._stage()
        selected = genstate.read_json(adapter_path)
        engine = genstate.read_json(Path(selected["engine_config"]))
        self.assertEqual(
            engine["service_launch_context"]["paths"]["MINDIE_CC_CONFIG"], str(adapter_path),
        )
        receipt = selected["product_validation"]
        self.assertTrue((generation / "scripts" / "diagnostic-build.json").is_file())
        self.assertEqual(receipt, dict(product_contract.identity(generation, SHA), status="validated"))
        self.assertEqual(engine["product_validation"], receipt)
        feed = engine["feeds"][0]
        self.assertEqual(feed["contract_sha256"], receipt["publication"]["contract_sha256"])
        self.assertEqual(feed["ref"], receipt["publication"]["ref"])
        self.assertEqual(feed["interval_seconds"], 900)
        self.assertEqual(engine["feeds"][1],
                         dict(repository="custom/knowledge", domain="custom", ref="private-branch"))
        counter = generation / ".venv" / "publication-count"
        self.assertEqual(counter.read_text(), "fetch\n")
        before = self._protected()
        cached = self._stage()
        self.assertEqual(cached, (generation, python, adapter_path))
        self.assertEqual(counter.read_text(), "fetch\n")
        self.assertEqual(self._protected(), before)

    def test_cached_source_change_is_rejected_without_retiring_current(self):
        generation, _, _ = self._stage()
        with (generation / "scripts" / "organizer.py").open("a") as stream:
            stream.write("\n# changed after preparation\n")
        self._check_rejected("receipt does not match")
        self.assertEqual((generation / ".venv" / "publication-count").read_text(), "fetch\n")

    def test_cached_runtime_is_rechecked_without_publication_refetch(self):
        generation, _, _ = self._stage()
        (generation / ".venv" / "changed-runtime").touch()
        self._check_rejected("runtime_pins: revision_mismatch")
        self.assertEqual((generation / ".venv" / "publication-count").read_text(), "fetch\n")

    def test_cached_receipt_disagreement_is_not_treated_as_first_install(self):
        _, _, adapter_path = self._stage()
        selected = genstate.read_json(adapter_path)
        selected["product_validation"]["source_sha256"] = "0" * 64
        genstate.atomic_write(adapter_path, selected)
        self._check_rejected("missing or inconsistent")

    def test_cached_feed_contract_change_is_rejected(self):
        _, _, adapter_path = self._stage()
        selected = genstate.read_json(adapter_path)
        engine_path = Path(selected["engine_config"])
        engine = genstate.read_json(engine_path)
        engine["feeds"][0]["contract_sha256"] = "0" * 64
        genstate.atomic_write(engine_path, engine)
        self._check_rejected("publication feed contract changed")

    def test_cached_native_package_change_is_rejected(self):
        generation, _, _ = self._stage()
        skill = next((generation / "host-package" / "skills").rglob("SKILL.md"))
        skill.write_text("changed cached skill")
        self._check_rejected("prepared native package changed")

    def test_completed_cache_missing_config_is_not_silently_rebuilt(self):
        _, _, adapter_path = self._stage()
        adapter_path.unlink()
        self._check_rejected("missing its runtime or adapter configuration")

    def test_generation_config_preserves_public_feed_opt_out(self):
        engine = json.loads(self.engine_path.read_text(encoding="utf-8"))
        engine.pop("feeds")
        self.engine_path.write_text(json.dumps(engine), encoding="utf-8")
        _, _, adapter_path = self._stage()
        selected = genstate.read_json(adapter_path)
        self.assertNotIn("feeds", genstate.read_json(Path(selected["engine_config"])))

    def test_failed_final_validation_does_not_leave_a_complete_stage(self):
        before = self._protected()
        real_probe = updater.probe_runtime

        def reject(python, deadline, generation, **kwargs):
            (generation / ".venv").mkdir(exist_ok=True)
            (generation / ".venv" / "changed-runtime").touch()
            return real_probe(python, deadline, generation, **kwargs)

        with patch.object(updater, "probe_runtime", side_effect=reject):
            with self.assertRaisesRegex(updater.CheckFailed, "runtime_pins: revision_mismatch"):
                self._stage()
        self.assertFalse((genstate.generations_dir(self.adapter) / SHA).exists())
        self.assertFalse((genstate.launch_dir(self.adapter) / SHA).exists())
        self.assertEqual(self._protected(), before)


if __name__ == "__main__":
    unittest.main()
