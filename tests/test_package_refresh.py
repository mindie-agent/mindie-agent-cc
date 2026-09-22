"""Focused package-refresh checks. Installer is mocked; no native Claude."""

import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from support import make_config

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

SHA = "a" * 40


def _touch(path: Path, text="{}\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class PackageRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.config = make_config(self.tmp)
        os.environ["MINDIE_CC_CONFIG"] = str(self.config)
        import genstate
        import native_claude
        import updater

        self.genstate = genstate
        self.native = native_claude
        self.updater = updater
        self.addCleanup(setattr, self.native, "mcp_list", self.native.mcp_list)
        self.addCleanup(setattr, self.updater, "resolve_main", self.updater.resolve_main)
        diagnostics_env = patch.dict(os.environ, {
            "MINDIE_DIAGNOSTICS_ROOT": str(self.tmp / "logs"),
            "MINDIE_DIAGNOSTICS_CONFIG": str(self.tmp / "diagnostics.json"),
        })
        diagnostics_env.start()
        self.addCleanup(diagnostics_env.stop)
        self.adapter = json.loads(self.config.read_text())
        self.adapter["base_config"] = str(self.config)
        self.config.write_text(json.dumps(self.adapter))
        self.adapter = json.loads(self.config.read_text())

    def tearDown(self):
        os.environ.pop("MINDIE_CC_CONFIG", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _commit(self, generation, sha=SHA, **extra):
        value = {
            "generation": str(generation),
            "python": sys.executable,
            "adapter_config": str(self.config),
            "sha": sha,
        }
        value.update(extra)
        self.genstate.write_current(value, self.adapter)
        return value

    def test_tuple_preserves_native_package(self):
        generation = self.genstate.generations_dir(self.adapter) / SHA
        generation.mkdir(parents=True)
        package = self.genstate.update_dir(self.adapter) / "native-packages" / (SHA + "-" + "b" * 12)
        package.mkdir(parents=True)
        self._commit(generation, native_package=str(package))
        read = self.genstate.read_current(self.adapter)
        self.assertEqual(read["native_package"], str(package))
        self.assertEqual(read["sha"], SHA)
        bad = dict(read, native_package=str(self.tmp / "missing"))
        with self.assertRaisesRegex(ValueError, "committed native_package"):
            self.genstate._valid_tuple(bad)
        self.genstate.atomic_write(self.genstate.current_path(self.adapter), bad)
        with self.assertRaisesRegex(ValueError, "committed native_package"):
            self.genstate.read_current(self.adapter)
        for other in (self.tmp / "outside", package.parent / ("c" * 40 + "-" + "b" * 12)):
            other.mkdir()
            with self.assertRaisesRegex(ValueError, "committed native_package"):
                self.genstate._valid_tuple(dict(read, native_package=str(other)))
        omitted = dict(read)
        omitted.pop("native_package")
        parsed = self.genstate._valid_tuple(omitted)
        self.assertNotIn("native_package", parsed)

    def _install_tree(self, package: Path, hooks: dict, mcp: dict, version: str):
        _touch(package / ".claude-plugin" / "plugin.json", json.dumps({
            "name": "mindie-agent", "version": version,
        }))
        _touch(package / "hooks" / "hooks.json", json.dumps(hooks))
        _touch(package / ".mcp.json", json.dumps(mcp))

    def _readback(self, package, install, version, launcher):
        self.native.mcp_list = lambda *args, **kwargs: (
            "plugin:mindie-agent:knowledge\nplugin:mindie-agent:remote\n"
        )
        return self.native.verify_readback(
            self.adapter,
            package,
            version,
            listing=[{
                "id": "mindie-agent@mindie-agent-cc",
                "version": version,
                "enabled": True,
                "installPath": str(install),
            }],
            markets=[{
                "name": "mindie-agent-cc",
                "source": "directory",
                "path": str(package),
            }],
            launcher=str(launcher),
            config_file=str(self.config),
            timeout=1,
        )

    def test_readback_rejects_missing_reporting_matcher(self):
        launcher = self.tmp / "launch" / "mindie_launch.py"
        _touch(launcher, "")
        python = sys.executable
        config_file = str(self.config)
        hooks = self.native.render_hooks(python, str(launcher), config_file)
        mcp = self.native.render_mcp(python, str(launcher), config_file)
        version = "0.1.0+mindie." + SHA[:12]
        target = self.tmp / "target"
        install = self.tmp / "install"
        self._install_tree(target, hooks, mcp, version)
        stale = json.loads(json.dumps(hooks))
        stale["hooks"]["UserPromptExpansion"] = [
            row for row in stale["hooks"]["UserPromptExpansion"]
            if row["matcher"] != "mindie-agent:reporting-status"
        ]
        self._install_tree(install, stale, mcp, version)
        with self.assertRaises(RuntimeError) as caught:
            self._readback(target, install, version, launcher)
        message = str(caught.exception)
        self.assertIn("missing UserPromptExpansion matchers", message)
        self.assertIn("mindie-agent:reporting-status", message)
        self.assertNotIn('"hooks"', message)

    def test_old_package_readback_accepts_its_own_declarations(self):
        launcher = self.tmp / "launch" / "mindie_launch.py"
        _touch(launcher, "")
        python = sys.executable
        config_file = str(self.config)
        hooks = self.native.render_hooks(python, str(launcher), config_file)
        hooks["hooks"]["UserPromptExpansion"] = hooks["hooks"]["UserPromptExpansion"][:6]
        mcp = self.native.render_mcp(python, str(launcher), config_file)
        version = "0.1.0+mindie.old"
        package = self.tmp / "old"
        install = self.tmp / "installed-old"
        self._install_tree(package, hooks, mcp, version)
        self._install_tree(install, hooks, mcp, version)
        verified = self._readback(package, install, version, launcher)
        self.assertEqual(verified["plugin"]["version"], version)

    def _eligible_generation(self):
        generation = self.genstate.generations_dir(self.adapter) / SHA
        shutil.copytree(ROOT / ".claude-plugin", generation / ".claude-plugin")
        (generation / self.updater.COMPLETE).write_text(SHA + "\n")
        _touch(generation / "skills/reporting-status/SKILL.md", "read-only status")
        launcher = self.genstate.launch_dir(self.adapter) / SHA / "mindie_launch.py"
        _touch(launcher, "# launcher\n")
        host = generation / "host-package"
        old_hooks = {"description": "old", "hooks": {"Stop": []}}
        old_mcp = {"mcpServers": {}}
        self._install_tree(host, old_hooks, old_mcp, "0.1.0+mindie.old")
        before = (host / "hooks" / "hooks.json").read_bytes()
        self._commit(generation)
        return generation, host, before, launcher

    def _run_check(self, idle, install, force=False):
        self.updater.resolve_main = lambda remote, deadline: SHA
        previous_here = self.updater.HERE
        self.updater.HERE = Path(self.genstate.read_current(self.adapter)["generation"]) / "scripts"
        try:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = self.updater._check_once(
                    self.adapter,
                    time.monotonic() + 600,
                    force=force,
                    build=None,
                    idle=idle,
                    install=install,
                    lock_timeout=5,
                )
            return code, buffer.getvalue()
        finally:
            self.updater.HERE = previous_here

    def test_failure_intent_suppresses_second_install(self):
        generation, host, before, _launcher = self._eligible_generation()
        calls = []

        def install(adapter, package, deadline, reserve=None):
            calls.append(adapter.get("sha"))
            raise RuntimeError("native failed at " + str(package))

        def idle(adapter, current, deadline):
            return {"idle": True, "service": "absent"}

        code, output = self._run_check(idle, install)
        self.assertEqual(code, 1)
        self.assertIn("native failed", output)
        self.assertIn("package-refresh", output)
        failed = self.genstate.read_json(self.genstate.failed_path(self.adapter))
        self.assertEqual(failed["phase"], "package-refresh")
        self.assertEqual(failed["sha"], SHA)
        self.assertIn("native failed", failed["error"])
        self.assertLess(len(failed["error"]), 430)
        self.assertEqual((host / "hooks" / "hooks.json").read_bytes(), before)
        calls.clear()
        code, output = self._run_check(idle, install)
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertIn("suppressed-known-failed", output)

    def test_busy_without_side_effect_keeps_prior_failure(self):
        self._eligible_generation()
        prior = {"sha": "b" * 40, "error": "keep-me", "at": 1}
        self.genstate.atomic_write(self.genstate.failed_path(self.adapter), prior)

        def idle(adapter, current, deadline):
            raise self.updater.Deferred("service has actual active work")

        def install(*args, **kwargs):
            raise AssertionError("install must not run")

        code, output = self._run_check(idle, install)
        self.assertEqual(code, 0)
        self.assertIn("deferred", output)
        self.assertEqual(self.genstate.read_json(self.genstate.failed_path(self.adapter)), prior)

    def test_success_keeps_host_package_and_commits_pointer(self):
        _generation, host, before, _launcher = self._eligible_generation()
        seen = {}

        def idle(adapter, current, deadline):
            return {"idle": True, "service": "absent"}

        def install(adapter, package, deadline, reserve=None):
            seen["sha"] = adapter.get("sha")
            seen["package"] = str(Path(package).resolve())
            seen["calls"] = seen.get("calls", 0) + 1

        code, output = self._run_check(idle, install)
        self.assertEqual(code, 0)
        self.assertEqual(seen["calls"], 1)
        self.assertEqual(seen["sha"], SHA)
        self.assertNotEqual(Path(seen["package"]).resolve(), host.resolve())
        self.assertEqual((host / "hooks" / "hooks.json").read_bytes(), before)
        current = self.genstate.read_current(self.adapter)
        self.assertEqual(current["sha"], SHA)
        self.assertEqual(Path(current["native_package"]).resolve(), Path(seen["package"]).resolve())
        self.assertIn("package-refreshed", output)
        self.assertFalse(self.genstate.failed_path(self.adapter).exists())
        code, output = self._run_check(idle, install)
        self.assertEqual(code, 0)
        self.assertEqual(seen["calls"], 1)
        self.assertIn("current", output)

    def test_adopted_package_cleanup_failure_never_installs_again(self):
        self._eligible_generation()
        calls = []
        idle = lambda *_: {"idle": True, "service": "absent"}
        install = lambda *args, **kwargs: calls.append(str(args[1]))
        with patch.object(self.updater, "_clear_refresh_intent", side_effect=PermissionError("owned-marker")):
            code, output = self._run_check(idle, install)
        self.assertEqual(code, 1)
        status = self.genstate.read_status(self.adapter)
        self.assertEqual(status["error"], "state-cleanup-failed")
        self.assertEqual(status["native_package_result"], "adopted")
        self.assertIn("native_package", self.genstate.read_current(self.adapter))
        self.assertEqual(len(calls), 1)
        self._run_check(idle, install)
        self.assertEqual(len(calls), 1)

    def test_deferred_cleanup_failure_preserves_no_install_fact(self):
        self._eligible_generation()
        def idle(*_):
            raise self.updater.Deferred("busy")
        with patch.object(self.updater, "_restore_refresh_intent", side_effect=PermissionError("owned-marker")):
            code, output = self._run_check(idle, lambda *_: self.fail("native install ran"))
        self.assertEqual(code, 1)
        status = self.genstate.read_status(self.adapter)
        self.assertEqual(status["error"], "state-cleanup-failed")
        self.assertEqual(status["native_package_result"], "not-attempted")

    def test_incomplete_existing_package_is_not_installed(self):
        generation, _, _, _ = self._eligible_generation()
        current = self.genstate.read_current(self.adapter)
        plan = self.updater._plan_package_refresh(self.adapter, current, SHA)
        package = self.updater._materialize_package(current, plan)
        (package / "skills/reporting-status/SKILL.md").unlink()
        with self.assertRaisesRegex(self.updater.CheckFailed, "incomplete or corrupt"):
            self.updater._materialize_package(current, plan)

    def test_matching_declarations_do_not_hide_corrupt_manifest_or_skills(self):
        self._eligible_generation()
        idle = lambda *_: {"idle": True, "service": "absent"}
        self.assertEqual(self._run_check(idle, lambda *_args, **_kwargs: None)[0], 0)
        current = self.genstate.read_current(self.adapter)
        package = Path(current["native_package"])
        manifest = package / ".claude-plugin/plugin.json"
        original = manifest.read_bytes()
        manifest.write_text(json.dumps({"name": "mindie-agent", "version": "wrong"}))
        with self.assertRaisesRegex(self.updater.CheckFailed, "committed native package"):
            self.updater._plan_package_refresh(self.adapter, current, SHA)
        manifest.write_bytes(original)
        (package / "skills/reporting-status/SKILL.md").unlink()
        with self.assertRaisesRegex(self.updater.CheckFailed, "committed native package"):
            self.updater._plan_package_refresh(self.adapter, current, SHA)

    def test_missing_launcher_does_not_skip_validation(self):
        package = self.tmp / "pkg"
        _touch(package / ".claude-plugin" / "plugin.json", json.dumps({"version": "v"}))
        with self.assertRaises(self.updater.CheckFailed) as caught:
            self.updater.native_install(
                dict(self.adapter, sha=SHA),
                package,
                time.monotonic() + 30,
            )
        self.assertEqual(str(caught.exception), "expected launcher missing")


if __name__ == "__main__":
    unittest.main()
