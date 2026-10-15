import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import make_config

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.config = make_config(self.tmp)
        os.environ["MINDIE_CC_CONFIG"] = str(self.config)
        import genstate
        import updater

        self.genstate = genstate
        self.updater = updater
        adapter = json.loads(self.config.read_text(encoding="utf-8"))
        genstate.write_current(
            {
                "generation": str(Path(__file__).resolve().parents[1]),
                "python": sys.executable,
                "adapter_config": str(self.config),
                "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            },
            adapter,
        )

    def tearDown(self):
        os.environ.pop("MINDIE_CC_CONFIG", None)
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_failed_sha_is_suppressed(self):
        adapter = json.loads(self.config.read_text(encoding="utf-8"))
        sha = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        self.genstate.atomic_write(
            self.genstate.failed_path(adapter),
            {"sha": sha, "error": "boom", "at": 1},
        )

        def fake_resolve(remote, deadline):
            return sha

        adapter["update_channel"] = "main"
        self.updater.resolve_main = fake_resolve
        code = self.updater._check_once(
            adapter,
            __import__("time").monotonic() + 30,
            force=False,
            build=None,
            idle=None,
            install=None,
            lock_timeout=1,
        )
        self.assertEqual(code, 0)
        status = self.genstate.read_status(adapter)
        self.assertEqual(status["result"], "suppressed-known-failed")

    def test_candidate_callback_keeps_captured_failure_receipt(self):
        import product_contract
        import time

        failure = RuntimeError("command failed (1)")
        failure.returncode = 1
        failure.mindie_output = '{"status":"failed","stage":"fixture"}\n'
        seen = {}

        def probe(python, scripts, command, **_):
            def on_failure(output, code):
                seen.update(output=output, code=code)
                return ValueError("candidate receipt retained")
            command(["synthetic-validator"], on_failure=on_failure)

        with patch.object(product_contract, "probe", side_effect=probe), \
                patch.object(self.updater, "bounded_run", side_effect=failure):
            with self.assertRaisesRegex(self.updater.CheckFailed, "candidate receipt retained"):
                self.updater.probe_runtime(Path(sys.executable), time.monotonic() + 180, SCRIPTS.parent)
        self.assertEqual(seen, dict(output=failure.mindie_output, code=1))

    def test_candidate_validator_uses_remaining_check_budget(self):
        import product_contract

        expected = dict(product_contract.identity(SCRIPTS.parent), status="validated")
        deadline = 100 + self.updater.RESERVE + 37
        with patch.object(self.updater.time, "monotonic", return_value=100), \
                patch.object(self.updater, "bounded_run",
                             return_value=json.dumps(expected)) as run:
            actual = self.updater.probe_runtime(Path(sys.executable), deadline, SCRIPTS.parent)
        self.assertEqual(actual, expected)
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs["timeout"], 37)
        self.assertEqual(run.call_args.kwargs["max_output"], 65536)

    def test_candidate_validator_does_not_start_after_check_budget_is_exhausted(self):
        deadline = 100 + self.updater.RESERVE + 4
        with patch.object(self.updater.time, "monotonic", return_value=100), \
                patch.object(self.updater, "bounded_run") as run:
            with self.assertRaisesRegex(self.updater.CheckFailed, "whole-check deadline exhausted"):
                self.updater.probe_runtime(Path(sys.executable), deadline, SCRIPTS.parent)
        run.assert_not_called()

    def test_host_package_stamps_unique_version(self):
        adapter = json.loads(self.config.read_text(encoding="utf-8"))
        source = Path(__file__).resolve().parents[1]
        root = self.tmp / "generation"
        self.updater.snapshot_source(source, root)
        package = self.updater.build_host_package(
            root, dict(adapter, python=sys.executable), "c" * 40,
            package_dir=self.tmp / "pkg",
        )
        manifest = json.loads((package / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0.0.1+mindie." + ("c" * 12))
        self.assertTrue((package / "hooks" / "hooks.json").is_file())
        metadata = json.loads((root / "scripts" / "diagnostic-build.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["revision"], "c" * 40)
        launcher = self.genstate.launch_dir(adapter) / ("c" * 40)
        for name in ("diagnostic_support.py", "diagnostic_fallback.py"):
            self.assertEqual((launcher / name).read_bytes(), (source / "scripts" / name).read_bytes())
        hooks = json.loads((package / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        command = hooks["hooks"]["Stop"][0]["hooks"][0]["command"]
        self.assertIn("mindie_launch.py", command)
        self.assertIn("hook stop", command)
        self.assertIn("--config", command)
        self.assertIn(str(self.config), command)
        mcp = json.loads((package / ".mcp.json").read_text(encoding="utf-8"))
        args = mcp["mcpServers"]["knowledge"]["args"]
        self.assertIn("--config", args)
        self.assertIn(str(self.config), args)

    def test_package_version_comes_from_candidate_not_running_updater(self):
        source = self.tmp / "candidate"
        manifest = source / ".claude-plugin/plugin.json"
        manifest.parent.mkdir(parents=True)
        for version in ("0.0.2", "0.0.2+mindie.old", "0.0.2+mindie.old.pkg.123"):
            manifest.write_text(json.dumps({"version": version}), encoding="utf-8")
            with self.subTest(version=version):
                self.assertEqual(
                    self.updater.stamp_version("d" * 40, source),
                    "0.0.2+mindie." + "d" * 12,
                )
        for version in (None, "main", "0.0.2+", "0.0.2+mindie..old", "0.0.2-beta.1"):
            manifest.write_text(json.dumps({"version": version}), encoding="utf-8")
            with self.subTest(version=version), self.assertRaisesRegex(
                self.updater.CheckFailed, "candidate plugin"
            ):
                self.updater.stamp_version("d" * 40, source)


if __name__ == "__main__":
    unittest.main()
