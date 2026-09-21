"""Focused feed-sync result folding for the Claude Code updater."""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from support import make_config

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import genstate  # noqa: E402
import updater  # noqa: E402


def _row(repository, status, **extra):
    item = dict(repository=repository, status=status, detail=extra.get("detail", ""))
    item.update(extra)
    return item


class FoldTests(unittest.TestCase):
    def test_mixed_preserves_retained_commit(self):
        payload = [
            _row("alpha", "synced", commit="aaa"),
            _row("beta", "unavailable", retained_commit="old", detail="gone"),
        ]
        aggregate, rows, summary = updater.fold_feed_results(json.dumps(payload))
        self.assertEqual(aggregate, "degraded")
        self.assertEqual(rows[1]["retained_commit"], "old")
        self.assertIn("beta:unavailable", summary)

    def test_empty_and_all_good(self):
        self.assertEqual(updater.fold_feed_results("[]")[0], "ok")
        payload = [_row("a", "synced"), _row("b", "unchanged")]
        self.assertEqual(updater.fold_feed_results(json.dumps(payload))[0], "ok")

    def test_pending_is_deferred_not_failed(self):
        payload = [_row("a", "deferred"), _row("b", "busy")]
        aggregate, rows, summary = updater.fold_feed_results(json.dumps(payload))
        self.assertEqual(aggregate, "deferred")
        self.assertEqual([row["status"] for row in rows], ["deferred", "busy"])
        self.assertIn("a:deferred", summary)
        self.assertEqual(
            updater.fold_feed_results(json.dumps([_row("a", "deferred")]))[0],
            "deferred",
        )
        self.assertEqual(
            updater.fold_feed_results(json.dumps([_row("a", "busy")]))[0],
            "deferred",
        )

    def test_mix_good_and_pending_is_degraded(self):
        payload = [_row("a", "synced"), _row("b", "busy")]
        self.assertEqual(
            updater.fold_feed_results(json.dumps(payload))[0], "degraded"
        )

    def test_no_good_with_error_is_sync_failed(self):
        payload = [_row("a", "unavailable"), _row("b", "deferred")]
        aggregate, rows, _ = updater.fold_feed_results(json.dumps(payload))
        self.assertEqual(aggregate, "sync_failed")
        self.assertEqual(len(rows), 2)
        with self.assertRaises(ValueError):
            updater.fold_feed_results(json.dumps([_row("a", "mystery")]))

    def test_malformed(self):
        for raw in ("", "{}", json.dumps([{"status": "synced"}]), "junk\n[]"):
            with self.assertRaises(ValueError):
                updater.fold_feed_results(raw)


class FeedSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.config = make_config(self.tmp)
        os.environ["MINDIE_CC_CONFIG"] = str(self.config)
        self.adapter = json.loads(self.config.read_text())
        genstate.write_current(
            {
                "generation": str(Path(__file__).resolve().parents[1]),
                "python": sys.executable,
                "adapter_config": str(self.config),
                "sha": "a" * 40,
            },
            self.adapter,
        )
        genstate.write_status(
            {
                "feed_sync": "sync_failed",
                "feed_error": "old",
                "feed_results": [_row("stale", "synced")],
                "result": "current",
            },
            self.adapter,
        )

    def tearDown(self):
        os.environ.pop("MINDIE_CC_CONFIG", None)
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _sync_with(self, output):
        real = updater.bounded_run

        def spy(argv, stdin="", **kw):
            return output

        updater.bounded_run = spy
        try:
            return updater._feed_sync(self.adapter, time.monotonic() + 60)
        finally:
            updater.bounded_run = real

    def test_empty_success_clears_prior_error(self):
        self.assertTrue(self._sync_with("[]"))
        status = genstate.read_status(self.adapter)
        self.assertEqual(status["feed_sync"], "ok")
        self.assertNotIn("feed_error", status)
        self.assertEqual(status["result"], "current")

    def test_malformed_clears_stale_results(self):
        self.assertFalse(self._sync_with(""))
        status = genstate.read_status(self.adapter)
        self.assertEqual(status["feed_sync"], "sync_failed")
        self.assertNotIn("feed_results", status)
        self.assertEqual(status["result"], "current")

    def test_budget_skip_is_not_success(self):
        ok = updater._feed_sync(self.adapter, time.monotonic() - 1)
        self.assertFalse(ok)
        status = genstate.read_status(self.adapter)
        self.assertTrue(status["feed_sync"].startswith("skipped:"))

    def test_check_runs_feed_even_if_plugin_failed(self):
        calls = []

        def feed(adapter, deadline):
            calls.append("feed")
            return False

        def boom(*a, **k):
            genstate.write_status(
                dict(genstate.read_status(self.adapter), result="failed"),
                self.adapter,
            )
            print(json.dumps({"result": "failed"}))
            return 1

        with patch.object(updater, "_feed_sync", feed):
            with patch.object(updater, "_check_once", boom):
                code = updater.check(self.adapter, lock_timeout=1)
        self.assertEqual(code, 1)
        self.assertEqual(calls, ["feed"])
        status = genstate.read_status(self.adapter)
        self.assertEqual(status["result"], "failed")

    def test_check_returns_plugin_code_when_feed_not_ok(self):
        def feed(adapter, deadline):
            return False

        def current(*a, **k):
            genstate.write_status(
                dict(genstate.read_status(self.adapter), result="current"),
                self.adapter,
            )
            print(json.dumps({"result": "current"}))
            return 0

        with patch.object(updater, "_feed_sync", feed):
            with patch.object(updater, "_check_once", current):
                code = updater.check(self.adapter, lock_timeout=1)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
