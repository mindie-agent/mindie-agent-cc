import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import explicit_local_candidate_args

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def run_setup(python, *args, source=ROOT, real_product=False, env=None):
    """Config cases isolate publication fetches only in this test process."""
    scripts = source / "scripts"
    command = [str(python), str(scripts / "setup.py")]
    if not real_product:
        helper = (
            "import sys; sys.path.insert(0, sys.argv.pop(1)); "
            "import setup,updater,product_contract; "
            "updater.probe_runtime=lambda python,deadline,generation,**kw: "
            "dict(product_contract.identity(generation,kw.get('revision')),status='validated'); "
            "setup.main()"
        )
        command = [str(python), "-c", helper, str(scripts)]
    return subprocess.run(
        [*command, "--knowledge-python", str(python), *map(str, args)],
        text=True, capture_output=True, timeout=60, env=env,
    )


class SetupTests(unittest.TestCase):
    def test_setup_no_native_no_schedule(self):
        tmp = Path(tempfile.mkdtemp())
        config = tmp / "cc.json"
        try:
            proc = run_setup(
                sys.executable,
                "--config",
                str(config),
                "--root",
                str(tmp / "data"),
                "--no-schedule",
                "--no-native-install",
                "--no-public-feed",
                *explicit_local_candidate_args(),
            )
            self.assertEqual(proc.returncode, 0, proc.stderr[:1000] + proc.stdout[:1000])
            report = json.loads(proc.stdout)
            self.assertTrue(Path(report["config"]).is_file())
            engine = json.loads(Path(report["engine_config"]).read_text(encoding="utf-8"))
            self.assertIn("admission_path", engine)
            self.assertNotIn("session_activation", engine)
            community = json.loads(Path(report["community_config"]).read_text(encoding="utf-8"))
            self.assertFalse(community["enabled"])
            self.assertFalse(Path(report["admission_path"]).exists())
            from mindie_knowledge.state_layout import state_root
            state = state_root(tmp / "data" / "cc", "vllm-ascend")
            self.assertTrue((state / "state-v4.sqlite3").is_file())
            self.assertFalse((tmp / "data" / "cc" / "vllm-ascend" / "connection.json").exists())
            adapter = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(adapter["product_validation"], engine["product_validation"])
            self.assertEqual(report["product_validation"], engine["product_validation"])
            self.assertEqual(report["product_validation"]["status"], "validated")
            import product_contract
            generation = Path(report["generation"])
            selected = json.loads((generation / "config" / "cc.adapter.json").read_text())
            self.assertEqual(selected["product_validation"], report["product_validation"])
            expected = product_contract.identity(
                generation, report["product_validation"]["candidate_revision"],
            )
            self.assertEqual(report["product_validation"], dict(expected, status="validated"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_setup_rejects_wrong_commit_before_writing_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "candidate"
            (source / "scripts").mkdir(parents=True)
            for name in ("setup.py", "paths.py", "bounded.py", "runtime_probe.py"):
                shutil.copy2(ROOT / "scripts" / name, source / "scripts" / name)
            # Valid official requirement shape, deliberately wrong exact commit.
            import re
            requirements = (ROOT / "runtime-requirements.txt").read_text(encoding="utf-8")
            requirements = re.sub(r"@[0-9a-f]{40}", "@" + "0" * 40, requirements, count=1)
            (source / "runtime-requirements.txt").write_text(requirements, encoding="utf-8")
            config = root / "installation" / "cc.json"
            data = root / "data"
            proc = subprocess.run(
                [sys.executable, str(source / "scripts" / "setup.py"),
                 "--config", str(config), "--root", str(data),
                 "--knowledge-python", sys.executable,
                 "--no-schedule", "--no-native-install", "--no-public-feed"],
                text=True, capture_output=True, timeout=30,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("does not have the exact required commits", proc.stderr)
            self.assertFalse(config.parent.exists())
            self.assertFalse(data.exists())

    def test_product_failure_is_reported_before_configuration_or_store_write(self):
        import updater

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "candidate"
            updater.snapshot_source(ROOT, source)
            # The real product runner receives a candidate-owned failure
            # envelope; no publication fetch or model request is involved.
            (source / "scripts" / "candidate_validate.py").write_text(
                "import json,sys\n"
                "expected=json.loads(sys.argv[sys.argv.index('--identity')+1])\n"
                "expected.update(status='failed',failure={'stage':'publication_contract',"
                "'code':'contract_mismatch'})\n"
                "print(json.dumps(expected))\n"
                "raise SystemExit(1)\n",
                encoding="utf-8",
            )
            config = root / "installation" / "cc.json"
            data = root / "data"
            proc = run_setup(
                sys.executable, "--config", config, "--root", data,
                "--no-schedule", "--no-native-install", "--no-public-feed",
                *explicit_local_candidate_args(), source=source, real_product=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("publication_contract: contract_mismatch", proc.stderr)
            self.assertIn("not retried", proc.stderr)
            self.assertNotIn("process: nonzero_exit", proc.stderr)
            self.assertFalse(config.parent.exists())
            self.assertFalse(data.exists())

    def test_corrupt_store_blocks_configuration_and_preserves_existing_state(self):
        from mindie_knowledge.loop.store import Store
        from mindie_knowledge.state_layout import state_root

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            store = Store(data / "cc", "vllm-ascend")
            store.close()
            state = state_root(data / "cc", "vllm-ascend")
            (state / "state-v4.sqlite3").write_bytes(b"corrupt state sentinel")
            before = {str(path.relative_to(data)): path.read_bytes()
                      for path in data.rglob("*") if path.is_file() and path.suffix != ".lock"}
            config = root / "installation" / "cc.json"
            proc = run_setup(
                sys.executable, "--config", config, "--root", data,
                "--no-schedule", "--no-native-install", "--no-public-feed",
                *explicit_local_candidate_args(),
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("knowledge store preparation failed", proc.stderr)
            self.assertIn("database", proc.stderr)
            self.assertFalse(config.parent.exists())
            after = {str(path.relative_to(data)): path.read_bytes()
                     for path in data.rglob("*") if path.is_file() and path.suffix != ".lock"}
            self.assertEqual(after, before)

    def test_final_candidate_failure_preserves_state_and_allows_ordinary_retry(self):
        import setup
        import updater
        from mindie_knowledge.loop.store import Store
        from mindie_knowledge.state_layout import state_root

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "candidate"
            updater.snapshot_source(ROOT, source)
            scripts = source / "scripts"
            shutil.copy2(scripts / "candidate_validate.py", scripts / "fixture_validator.py")
            fail_publication = root / "fail-final-publication"
            fail_publication.touch()
            # Only the publication transport is controlled. Both validations
            # still check the actual candidate bytes, pins, API and receipts.
            (scripts / "candidate_validate.py").write_text(
                "import sys\n"
                "from pathlib import Path\n"
                "sys.path.insert(0,str(Path(__file__).resolve().parent))\n"
                "import fixture_validator as real\n"
                "def publication(_):\n"
                f"    if Path({str(fail_publication)!r}).exists() and "
                "Path(__file__).with_name('diagnostic-build.json').is_file():\n"
                "        raise real.CheckFailure('publication_fetch','fetch_failed')\n"
                "real.publication_contract=publication\n"
                "raise SystemExit(real.main())\n",
                encoding="utf-8",
            )
            config = root / "installation" / "cc.json"
            community = config.with_name("mindie-community.json")
            setup.write_community(community, None, python=sys.executable)
            community_before = community.read_bytes()
            data = root / "data"
            store = Store(data / "cc", "vllm-ascend")
            store.close()
            database = state_root(data / "cc", "vllm-ascend") / "state-v4.sqlite3"
            with sqlite3.connect(database) as db:
                db.execute(
                    "INSERT INTO material_summary_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("attempt", "task", "batch", "body", "input", "policy", "[]",
                     "outcome_unknown", '{"paid_result":"retained"}', "interrupted", 1, 2),
                )
                attempts_before = db.execute("SELECT * FROM material_summary_attempts").fetchall()
            remote = data / "cc" / "adapter-state" / "remote" / "task" / "jobs" / "paid.json"
            remote.parent.mkdir(parents=True)
            remote.write_text('{"submission_state":"uncertain","result":"retained"}')
            remote_before = remote.read_bytes()
            args = [
                "--config", config, "--root", data, "--no-schedule",
                "--no-native-install", "--no-public-feed", *explicit_local_candidate_args(),
            ]

            failed = run_setup(sys.executable, *args, source=source, real_product=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("publication_fetch: fetch_failed", failed.stderr)
            self.assertFalse(config.exists())
            self.assertFalse(config.with_name("cc.engine.json").exists())
            self.assertFalse((data / "cc" / "adapter-state" / "update" / "current.json").exists())
            self.assertEqual(community.read_bytes(), community_before)
            with sqlite3.connect(database) as db:
                self.assertEqual(db.execute("SELECT * FROM material_summary_attempts").fetchall(),
                                 attempts_before)
            self.assertEqual(remote.read_bytes(), remote_before)

            fail_publication.unlink()
            retry = run_setup(sys.executable, *args, source=source, real_product=True)
            self.assertEqual(retry.returncode, 0, retry.stderr[:1500])
            report = json.loads(retry.stdout)
            self.assertEqual(report["product_validation"]["status"], "validated")
            self.assertTrue(config.is_file())
            generation = Path(report["generation"])
            self.assertTrue((generation / updater.COMPLETE).is_file())
            self.assertTrue((data / "cc" / "adapter-state" / "update" / "current.json").is_file())
            with sqlite3.connect(database) as db:
                self.assertEqual(db.execute("SELECT * FROM material_summary_attempts").fetchall(),
                                 attempts_before)
            self.assertEqual(remote.read_bytes(), remote_before)

    def test_existing_configuration_update_does_not_reinstall_candidate(self):
        from support import make_config

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = make_config(root / "installed")
            project = root / "project"
            project.mkdir()
            before = json.loads(config.read_text(encoding="utf-8"))
            helper = (
                "import sys; sys.path.insert(0, sys.argv.pop(1)); "
                "import setup,updater,capture_config; "
                "fail=lambda *a,**kw: (_ for _ in ()).throw(AssertionError('must not install')); "
                "updater.probe_runtime=fail; capture_config.prepare_store=fail; setup.main()"
            )
            proc = subprocess.run(
                [sys.executable, "-c", helper, str(ROOT / "scripts"),
                 "--knowledge-python", sys.executable, "--config", str(config),
                 "--root", str(root / "unused"), "--community-repository", "owner/repo",
                 "--community-project-root", str(project), "--community-visibility", "public",
                 *explicit_local_candidate_args()],
                text=True, capture_output=True, timeout=60,
                env=dict(os.environ, MINDIE_CC_CONFIG=str(config)),
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["updated"], "community")
            after = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(
                {key: value for key, value in after.items() if key != "community_config"},
                {key: value for key, value in before.items() if key != "community_config"},
            )
            self.assertEqual(after["community_config"],
                             str(config.with_name("mindie-community.json")))
            self.assertFalse((root / "unused").exists())


if __name__ == "__main__":
    unittest.main()
