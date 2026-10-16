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

import maintenance  # noqa: E402


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
        aggregate, rows, summary = maintenance.fold_feed_results(json.dumps(payload))
        self.assertEqual(aggregate, "degraded")
        self.assertEqual(rows[1]["retained_commit"], "old")
        self.assertIn("beta:unavailable", summary)

    def test_empty_and_all_good(self):
        self.assertEqual(maintenance.fold_feed_results("[]")[0], "ok")
        payload = [_row("a", "synced"), _row("b", "unchanged")]
        self.assertEqual(maintenance.fold_feed_results(json.dumps(payload))[0], "ok")

    def test_pending_is_deferred_not_failed(self):
        payload = [_row("a", "deferred"), _row("b", "busy")]
        aggregate, rows, summary = maintenance.fold_feed_results(json.dumps(payload))
        self.assertEqual(aggregate, "deferred")
        self.assertEqual([row["status"] for row in rows], ["deferred", "busy"])
        self.assertIn("a:deferred", summary)
        self.assertEqual(
            maintenance.fold_feed_results(json.dumps([_row("a", "deferred")]))[0],
            "deferred",
        )
        self.assertEqual(
            maintenance.fold_feed_results(json.dumps([_row("a", "busy")]))[0],
            "deferred",
        )

    def test_mix_good_and_pending_is_degraded(self):
        payload = [_row("a", "synced"), _row("b", "busy")]
        self.assertEqual(
            maintenance.fold_feed_results(json.dumps(payload))[0], "degraded"
        )

    def test_no_good_with_error_is_sync_failed(self):
        payload = [_row("a", "unavailable"), _row("b", "deferred")]
        aggregate, rows, _ = maintenance.fold_feed_results(json.dumps(payload))
        self.assertEqual(aggregate, "sync_failed")
        self.assertEqual(len(rows), 2)
        with self.assertRaises(ValueError):
            maintenance.fold_feed_results(json.dumps([_row("a", "mystery")]))

    def test_malformed(self):
        for raw in ("", "{}", json.dumps([{"status": "synced"}]), "junk\n[]"):
            with self.assertRaises(ValueError):
                maintenance.fold_feed_results(raw)

    def test_committed_snapshot_cleanup_or_receipt_failure_is_not_success(self):
        for field in ("cleanup_status", "receipt_status"):
            with self.subTest(field=field):
                payload = [_row("a", "synced", commit="known-installed", **{field: "failed"})]
                aggregate, rows, summary = maintenance.fold_feed_results(json.dumps(payload))
                self.assertEqual(aggregate, "degraded")
                self.assertEqual(rows, payload)
                self.assertIn(field, summary)
                self.assertIn("failed", summary)

    def test_partial_promotion_preserves_committed_effect_and_failed_stage(self):
        partial = _row("a", "partial", commit="known-metadata",
                       metadata_committed=True, failed_stage="material-promotion",
                       detail="metadata committed; file promotion failed")
        for payload in ([partial], [_row("b", "synced"), partial],
                        [partial, _row("c", "unavailable")]):
            with self.subTest(payload=payload):
                aggregate, rows, summary = maintenance.fold_feed_results(json.dumps(payload))
                self.assertEqual(aggregate, "degraded")
                self.assertEqual(rows, payload)
                self.assertIn("a:partial", summary)

    def test_unknown_late_status_and_incomplete_partial_are_rejected(self):
        for row in (
            _row("a", "synced", receipt_status="mystery"),
            _row("a", "synced", cleanup_status=False),
            _row("a", "partial", metadata_committed=False),
            _row("a", "partial"),
        ):
            with self.subTest(row=row), self.assertRaises(ValueError):
                maintenance.fold_feed_results(json.dumps([row]))


class SetupFeedSyncTests(unittest.TestCase):
    def test_committed_failures_preserve_rows_and_do_not_replay(self):
        import maintenance as setup

        for row in (
            _row("a", "synced", commit="known-installed", cleanup_status="failed"),
            _row("a", "synced", commit="known-installed", receipt_status="failed"),
            _row("a", "partial", commit="known-metadata", metadata_committed=True,
                 failed_stage="material-promotion"),
        ):
            with self.subTest(row=row), patch.object(setup, "run",
                                                     return_value=json.dumps([row])) as runner:
                result = setup.sync_public_feed("python", Path("engine.json"))
            self.assertEqual(result["result"], "degraded")
            self.assertEqual(result["results"], [row])
            self.assertTrue(result["error"])
            runner.assert_called_once()

    def test_sync_has_no_outer_deadline_and_preserves_business_results(self):
        import maintenance as setup

        rows = [_row("a", "synced", commit="new"),
                _row("b", "unavailable", retained_commit="old", detail="gone")]
        with patch.object(setup, "run", return_value=json.dumps(rows)) as runner:
            result = setup.sync_public_feed("python", Path("engine.json"))
        self.assertNotIn("timeout", runner.call_args.kwargs)
        self.assertEqual(runner.call_args.args[0][1:4],
                         ["-m", "mindie_knowledge.loop.cli", "sync"])
        self.assertEqual(result["result"], "degraded")
        self.assertEqual(result["results"], rows)
        self.assertIn("b:unavailable", result["error"])
        runner.assert_called_once()

    def test_malformed_output_and_unknown_status_fail(self):
        import maintenance as setup

        for output in ("", "{}", "not JSON", json.dumps([_row("a", "mystery")])):
            with self.subTest(output=output), patch.object(setup, "run", return_value=output):
                result = setup.sync_public_feed("python", Path("engine.json"))
            self.assertEqual(result["result"], "sync_failed")
            self.assertIn("ValueError", result["error"])
            self.assertNotIn("results", result)

    def test_process_failure_preserves_completed_rows(self):
        import maintenance as setup

        rows = [_row("a", "synced", commit="completed")]
        failure = RuntimeError("sync process exited with status 1")
        failure.mindie_output = json.dumps(rows)
        with patch.object(setup, "run", side_effect=failure) as runner:
            result = setup.sync_public_feed("python", Path("engine.json"))
        self.assertEqual(result["result"], "sync_failed")
        self.assertEqual(result["results"], rows)
        self.assertIn("exited with status 1", result["error"])
        runner.assert_called_once()

    def test_cleanup_failure_does_not_erase_successful_sync(self):
        import maintenance as setup

        rows = [_row("a", "synced", commit="completed")]
        cleanup = [dict(stage="group_cleanup", type="OSError")]

        def completed(argv, stdin, *, receipt):
            receipt.update(cleanup_failed=True, cleanup=cleanup)
            return json.dumps(rows)

        with patch.object(setup, "run", side_effect=completed) as runner:
            result = setup.sync_public_feed("python", Path("engine.json"))
        self.assertEqual(result["result"], "ok")
        self.assertEqual(result["results"], rows)
        self.assertTrue(result["cleanup_failed"])
        self.assertEqual(result["cleanup"], cleanup)
        self.assertIn("completed", result["error"])
        runner.assert_called_once()



def test_late_cancel_preserves_completed_feed_output():
    import threading
    import bounded
    cancel = threading.Event()
    rows = [_row("fixture/public", "synced", commit="committed-before-cancel")]
    reader = "_run_posix" if bounded.POSIX else "_run_windows"
    real_reader = getattr(bounded, reader)

    def late_cancel(*args, **kwargs):
        result = real_reader(*args, **kwargs)
        cancel.set()
        return result

    def harmless(*args, **kwargs):
        return bounded.run([sys.executable, "-c", "print(" + repr(json.dumps(rows)) + ")"],
                           receipt=kwargs["receipt"], cancel=cancel)

    with patch.object(bounded, reader, side_effect=late_cancel), \
            patch.object(maintenance, "run", side_effect=harmless):
        result = maintenance.sync_public_feed(sys.executable, Path("fixture.json"))
    assert result["results"] == rows
    assert result["operation_completed"] is True
    assert result["returncode"] == 0
    assert result["cancelled_after_completion"] is True
    assert result["result"] == "sync_failed"


def test_session_recording_failure_keeps_known_feed_and_diagnostic_outcomes(tmp_path):
    from install_state import atomic_write
    rows = [_row("fixture/public", "synced", commit="committed")]
    def run(*args, **kwargs):
        kwargs["receipt"].update(returncode=0, cleanup_failed=False)
        return '{"status":"ok"}'
    with patch.object(maintenance, "sync_public_feed", return_value={"result": "ok", "results": rows}), \
            patch.object(maintenance, "run", side_effect=run), \
            patch("paths.engine_config_path", return_value=tmp_path / "engine.json"), \
            patch("paths.state_dir", return_value=tmp_path), \
            patch("diagnostic_support.reporting_config_path", return_value=tmp_path / "reporting.json"), \
            patch("install_state.atomic_write", side_effect=PermissionError("fixture")):
        result = maintenance.session_start()
    assert result["feed"]["results"] == rows
    assert result["diagnostics"]["status"] == "ok"
    assert result["status"] == "incomplete"
    assert result["recording_error"] == "PermissionError"
