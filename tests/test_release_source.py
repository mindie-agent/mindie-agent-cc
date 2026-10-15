"""Ordinary Release resolution and candidate version checks.

These tests use synthetic metadata and a local Git repository. They do not
call GitHub, install a host package, or establish native acceptance.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import updater
from support import make_config

SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER = "fedcba9876543210fedcba9876543210fedcba98"


class _Response:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode()

    def read(self, limit):
        return self._raw[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _release(tag="v0.0.1", **extra):
    payload = {"tag_name": tag, "draft": False, "prerelease": False}
    payload.update(extra)
    return payload


class ReleaseResolverTests(unittest.TestCase):
    def test_normal_release_resolves_exact_tag_commit(self):
        seen = {}

        def opener(request, timeout):
            seen["url"] = request.full_url
            seen["accept"] = request.get_header("Accept")
            return _Response(_release())

        def git(args, *, deadline, cwd=None, transport=False):
            self.assertEqual(args[:2], ["ls-remote", "origin"])
            self.assertIn("refs/tags/v0.0.1", args)
            self.assertNotIn("refs/heads/main", args)
            self.assertTrue(transport)
            return SHA + "\trefs/tags/v0.0.1\n"

        with patch.object(updater, "_git", git):
            commit, version = updater.resolve_release("origin", time.monotonic() + 30, opener)
        self.assertEqual((commit, version), (SHA, "0.0.1"))
        self.assertTrue(seen["url"].endswith("/releases/latest"))
        self.assertEqual(seen["accept"], "application/vnd.github+json")

    def test_annotated_tag_uses_peeled_commit(self):
        def opener(_request, timeout):
            return _Response(_release("v1.2.3"))

        def git(args, *, deadline, cwd=None, transport=False):
            return (
                OTHER + "\trefs/tags/v1.2.3\n"
                + SHA + "\trefs/tags/v1.2.3^{}\n"
            )

        with patch.object(updater, "_git", git):
            commit, version = updater.resolve_release("origin", time.monotonic() + 30, opener)
        self.assertEqual(commit, SHA)
        self.assertEqual(version, "1.2.3")

    def test_prerelease_beta_and_draft_are_rejected(self):
        def git(*_args, **_kwargs):
            raise AssertionError("git must not run")

        cases = (
            _release(prerelease=True),
            _release(draft=True),
            _release("v0.0.1-beta.1"),
            _release("v0.0.1-rc.1"),
            {"tag_name": 1},
        )
        for payload in cases:
            with patch.object(updater, "_git", git):
                with self.assertRaises(updater.CheckFailed):
                    updater.resolve_release(
                        "origin", time.monotonic() + 30,
                        lambda _request, timeout, payload=payload: _Response(payload),
                    )

    def test_missing_tag_is_a_failed_resolve(self):
        def git(args, *, deadline, cwd=None, transport=False):
            return ""

        with patch.object(updater, "_git", git):
            with self.assertRaises(updater.CheckFailed) as caught:
                updater.resolve_release(
                    "origin", time.monotonic() + 30,
                    lambda _request, timeout: _Response(_release()),
                )
        self.assertIn("did not resolve", str(caught.exception))

    def test_default_channel_is_release_and_main_stays_explicit(self):
        self.assertEqual(updater.update_channel({}), "release")
        self.assertEqual(updater.update_channel({"update_channel": "main"}), "main")
        with patch.dict(os.environ, {"MINDIE_CC_UPDATE_CHANNEL": "main"}):
            self.assertEqual(updater.update_channel({"update_channel": "release"}), "main")
        with self.assertRaises(updater.CheckFailed):
            updater.update_channel({"update_channel": "beta"})

    def test_manifest_must_match_release_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = root / ".claude-plugin"
            plugin.mkdir()
            (plugin / "plugin.json").write_text(
                json.dumps({"name": "mindie-agent", "version": "0.0.2"}),
                encoding="utf-8",
            )
            with self.assertRaises(updater.CheckFailed) as caught:
                updater.validate_release_manifest(root, "0.0.1")
            self.assertIn("must match", str(caught.exception))
            (plugin / "plugin.json").write_text(
                json.dumps({"version": "0.0.1+mindie.abc"}), encoding="utf-8",
            )
            updater.validate_release_manifest(root, "0.0.1")

    def test_release_resolve_failure_is_not_treated_as_current(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        config = make_config(tmp)
        os.environ["MINDIE_CC_CONFIG"] = str(config)
        self.addCleanup(os.environ.pop, "MINDIE_CC_CONFIG", None)
        adapter = json.loads(config.read_text(encoding="utf-8"))
        adapter["update_channel"] = "release"
        import genstate
        genstate.write_current({
            "generation": str(tmp),
            "python": sys.executable,
            "adapter_config": str(config),
            "sha": SHA,
        }, adapter)

        def opener(_request, timeout):
            raise updater.CheckFailed("release source unavailable")

        calls = {"stage": 0}

        def stage(*_args, **_kwargs):
            calls["stage"] += 1

        with patch.object(updater, "read_latest_release", side_effect=opener), \
                patch.object(updater, "stage_generation", stage):
            buffer = io.StringIO()
            with patch("sys.stdout", buffer):
                code = updater._check_once(
                    adapter, time.monotonic() + 30, force=True, build=None,
                    idle=None, install=None, lock_timeout=1,
                )
        self.assertEqual(code, 1)
        self.assertEqual(calls["stage"], 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["result"], "check-failed")
        self.assertNotEqual(payload["result"], "current")
        current = genstate.read_current(adapter)
        self.assertEqual(current["sha"], SHA)


class LocalTagResolutionTests(unittest.TestCase):
    def test_real_git_tag_resolves_without_tracking_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                       GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
            subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True, env=env)
            (root / "README").write_text("one\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "README"], check=True, env=env)
            subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "one"], check=True, env=env)
            first = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                capture_output=True, text=True, env=env,
            ).stdout.strip()
            subprocess.run(["git", "-C", str(root), "tag", "-a", "v0.0.1", "-m", "release"],
                           check=True, env=env)
            (root / "README").write_text("two\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "commit", "-aq", "-m", "two"], check=True, env=env)
            bare = root / "origin.git"
            subprocess.run(["git", "clone", "--bare", "-q", str(root), str(bare)], check=True, env=env)
            seen = []
            real = updater._git

            def wrapped(args, **kwargs):
                seen.append(args)
                return real(args, **kwargs)

            def opener(_request, timeout):
                return _Response(_release())

            with patch.object(updater, "_git", wrapped):
                commit, version = updater.resolve_release(
                    str(bare), time.monotonic() + 30, opener,
                )
            self.assertEqual(version, "0.0.1")
            self.assertEqual(commit, first)
            self.assertTrue(all("refs/heads/main" not in item for item in seen[0]))


if __name__ == "__main__":
    unittest.main()
