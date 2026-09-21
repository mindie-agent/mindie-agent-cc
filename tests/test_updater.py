import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

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
        adapter = json.loads(self.config.read_text())
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
        adapter = json.loads(self.config.read_text())
        sha = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        self.genstate.atomic_write(
            self.genstate.failed_path(adapter),
            {"sha": sha, "error": "boom", "at": 1},
        )

        def fake_resolve(remote, deadline):
            return sha

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

    def test_host_package_stamps_unique_version(self):
        adapter = json.loads(self.config.read_text())
        root = Path(__file__).resolve().parents[1]
        package = self.updater.build_host_package(
            root, dict(adapter, python=sys.executable), "c" * 40,
            package_dir=self.tmp / "pkg",
        )
        manifest = json.loads((package / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(manifest["version"], "0.1.0+mindie." + ("c" * 12))
        self.assertTrue((package / "hooks" / "hooks.json").is_file())
        hooks = json.loads((package / "hooks" / "hooks.json").read_text())
        command = hooks["hooks"]["Stop"][0]["hooks"][0]["command"]
        self.assertIn("mindie_launch.py", command)
        self.assertIn("hook stop", command)
        self.assertIn("--config", command)
        self.assertIn(str(self.config), command)
        mcp = json.loads((package / ".mcp.json").read_text())
        args = mcp["mcpServers"]["knowledge"]["args"]
        self.assertIn("--config", args)
        self.assertIn(str(self.config), args)


if __name__ == "__main__":
    unittest.main()
