import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "scripts"))
import bounded  # noqa: E402


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "state="],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    state = result.stdout.strip()
    return bool(state) and not state.startswith("Z")


class BoundedTests(unittest.TestCase):
    def test_entry_cleanup_does_not_replace_spawn_failure(self):
        from unittest.mock import Mock

        failure = PermissionError("assignment failed")
        process = Mock()
        with patch.object(bounded, "POSIX", False), \
                patch.object(bounded.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, create=True), \
                patch.object(bounded.subprocess, "CREATE_NO_WINDOW", 0x8000000, create=True), \
                patch.object(bounded.subprocess, "Popen", return_value=process), \
                patch.object(bounded, "_own_windows_tree", side_effect=failure), \
                patch.object(bounded, "_kill_tree", side_effect=OSError("cleanup failed")), \
                patch.object(bounded, "_resume_windows_process") as resume:
            with self.assertRaises(PermissionError) as caught:
                bounded.exec_or_wait(["python", "-c", "pass"], {})
        self.assertIs(caught.exception, failure)
        self.assertIn("terminate_owned_tree", caught.exception.__notes__[0])
        resume.assert_not_called()
        process.wait.assert_called_once_with(timeout=5)

    def test_core_managed_call_does_not_rejudge_the_shared_owner(self):
        with patch.object(bounded, "_maintenance_owner", side_effect=AssertionError("second owner")), \
                patch.object(bounded.os, "getppid", return_value=1):
            output = bounded.run([sys.executable, "-c", "print('owned')"],
                                 core_managed=True, timeout=3)
        self.assertEqual(output, "owned" + os.linesep)

    def test_unmanaged_call_keeps_owner_failure_before_spawn(self):
        with patch.object(bounded, "_maintenance_owner", return_value=42), \
                patch.object(bounded.os, "getppid", return_value=1), \
                patch.object(bounded, "_spawn") as spawn:
            with self.assertRaises(bounded.OwnerExited):
                bounded.run([sys.executable, "-c", "print('not reached')"])
        spawn.assert_not_called()

    def test_managed_flag_rejects_unvalidated_truthy_text(self):
        with patch.object(bounded, "_spawn") as spawn:
            with self.assertRaisesRegex(ValueError, "core_managed"):
                bounded.run([sys.executable, "-c", "pass"], core_managed="true")
        spawn.assert_not_called()

    def _windows_reader(self, program, *, wait_for_exit=False):
        # Exercise the actual pipe reader strategy on every runner. Native
        # Windows additionally uses the real suspended spawn and owned Job.
        with tempfile.TemporaryFile() as stdin:
            process = bounded._spawn([sys.executable, "-c", program], stdin, None, None)
            if wait_for_exit:
                process.wait(timeout=5)
            receipt = {}
            output = bounded._run_windows(
                process, 2, 65536, process.pid, True, None,
                receipt=receipt, terminal=lambda raw: b'"type": "result"' in raw,
            )
        return output, receipt

    def test_windows_reader_accepts_small_terminal_before_exit(self):
        started = time.monotonic()
        output, receipt = self._windows_reader(
            'import time; print(\'{"type": "result", "result": "done"}\', flush=True); time.sleep(30)'
        )
        self.assertIn('"result": "done"', output)
        self.assertLess(time.monotonic() - started, 2)
        self.assertFalse(receipt.get("cleanup_failed", False))

    def test_windows_reader_preserves_final_error_envelope(self):
        output, _receipt = self._windows_reader(
            'import sys; print(\'{"type": "result", "is_error": true}\', flush=True); sys.exit(1)',
            wait_for_exit=True,
        )
        self.assertIn('"is_error": true', output)

    def test_cleanup_failure_without_receipt_preserves_completed_output(self):
        with patch.object(bounded, "_kill_tree", side_effect=PermissionError("fixture")):
            with self.assertRaises(bounded.CommandCleanupError) as caught:
                bounded.run([sys.executable, "-c", "print('completed')"], timeout=3)
        self.assertEqual(caught.exception.stdout, "completed" + os.linesep)
        self.assertTrue(caught.exception.operation_completed)
        self.assertEqual(caught.exception.cleanup,
                         [dict(stage="terminate_owned_tree", error_type="PermissionError")])
        self.assertNotIn("fixture", str(caught.exception))

    def test_cleanup_failure_with_receipt_keeps_result_for_protocol_owner(self):
        receipt = {}
        with patch.object(bounded, "_kill_tree", side_effect=PermissionError("fixture")):
            output = bounded.run([sys.executable, "-c", "print('completed')"],
                                 timeout=3, receipt=receipt)
        self.assertEqual(output, "completed" + os.linesep)
        self.assertTrue(receipt["cleanup_failed"])
        self.assertEqual(receipt["cleanup"][0]["stage"], "terminate_owned_tree")

    def test_cleanup_failure_does_not_replace_execution_failure(self):
        with patch.object(bounded, "_kill_tree", side_effect=PermissionError("fixture")):
            with self.assertRaisesRegex(RuntimeError, r"command failed \(3\)") as caught:
                bounded.run([sys.executable, "-c", "import sys;sys.exit(3)"], timeout=3)
        self.assertTrue(caught.exception.cleanup_failed)
        self.assertEqual(caught.exception.cleanup[0]["stage"], "terminate_owned_tree")

    @unittest.skipUnless(bounded.POSIX, "POSIX process group receipt")
    def test_successful_group_cleanup_is_not_signalled_twice(self):
        process = unittest.mock.Mock(pid=12345)
        process._mindie_inherited_group = False
        process._mindie_tree_terminated = False
        with patch.object(bounded.os, "killpg") as kill:
            bounded._kill_tree(process)
            bounded._kill_tree(process)
        kill.assert_called_once_with(12345, bounded.signal.SIGKILL)

    @unittest.skipUnless(bounded.POSIX, "POSIX process group receipt")
    def test_failed_group_cleanup_does_not_record_success(self):
        process = unittest.mock.Mock(pid=12345)
        process._mindie_inherited_group = False
        process._mindie_tree_terminated = False
        with patch.object(bounded.os, "killpg", side_effect=PermissionError("fixture")):
            with self.assertRaises(PermissionError):
                bounded._kill_tree(process)
        self.assertFalse(process._mindie_tree_terminated)

    def test_timeout_kills_process_group(self):
        started = time.monotonic()
        with self.assertRaises(RuntimeError):
            bounded.run(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                "",
                timeout=0.4,
            )
        self.assertLess(time.monotonic() - started, 3)

    def test_output_bound_kills_runaway_during_execution(self):
        started = time.monotonic()
        with self.assertRaises(RuntimeError) as ctx:
            bounded.run(
                [sys.executable, "-c",
                 "import sys\nwhile True: sys.stdout.write('x' * 65536); "
                 "sys.stdout.flush()"],
                "",
                timeout=30,
                max_output=128 * 1024,
            )
        self.assertIn("output exceeds the bound", str(ctx.exception))
        self.assertLess(time.monotonic() - started, 10)

    def test_timeout_kills_spawned_child_tree(self):
        started = time.monotonic()
        with self.assertRaises(RuntimeError):
            bounded.run(
                [sys.executable, "-c",
                 "import subprocess, sys, time\n"
                 "subprocess.Popen([sys.executable, '-c', "
                 "'import time; time.sleep(30)'])\n"
                 "time.sleep(30)"],
                "",
                timeout=0.5,
            )
        self.assertLess(time.monotonic() - started, 5)

    def test_empty_later_argument_is_passed(self):
        output = bounded.run(
            [sys.executable, "-c", "import sys; print(repr(sys.argv[1]))", ""],
            "",
            timeout=2,
        )
        self.assertEqual(output.strip(), "''")
        with self.assertRaises(ValueError):
            bounded.run(["", "-c", "print(1)"], "", timeout=1)
        with self.assertRaises(ValueError):
            bounded.run([sys.executable, "--depth", 1], "", timeout=1)

    def test_non_reading_stdin_does_not_hang_parent(self):
        started = time.monotonic()
        output = bounded.run(
            [sys.executable, "-c", "print('ok')"],
            "x" * 1024,
            timeout=2,
        )
        self.assertIn("ok", output)
        self.assertLess(time.monotonic() - started, 3)

    def test_no_deadline_completes_and_rejects_nonpositive(self):
        output = bounded.run(
            [sys.executable, "-c", "import time; time.sleep(0.2); print('ok')"],
            "",
            timeout=None,
        )
        self.assertIn("ok", output)
        with self.assertRaises(ValueError):
            bounded.run([sys.executable, "-c", "print(1)"], "", timeout=0)
        with self.assertRaises(ValueError):
            bounded.run([sys.executable, "-c", "print(1)"], "", timeout=-1)

    def test_no_deadline_still_cancels_and_kills(self):
        import threading

        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(bounded.CommandCancelled):
            bounded.run(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                "",
                timeout=None,
                cancel=cancel,
            )

    def test_terminal_evidence_ends_the_read_and_reaps_the_tree(self):
        # The child prints a complete terminal line, then lingers with a
        # spawned descendant; the run must end on the evidence, not the linger.
        program = (
            "import subprocess, sys, time\n"
            "print('{\"done\": true}', flush=True)\n"
            "subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'])\n"
            "time.sleep(30)\n"
        )
        started = time.monotonic()
        receipt = {}
        output = bounded.run(
            [sys.executable, "-c", program],
            "",
            timeout=None,
            receipt=receipt,
            terminal=lambda raw: b'"done": true' in raw,
        )
        self.assertLess(time.monotonic() - started, 5)
        self.assertIn('"done": true', output)
        self.assertTrue(receipt["started"])
        self.assertFalse(receipt["cleanup_failed"])

    def test_owner_death_stops_before_spawn(self):
        parent = os.getppid()
        with patch.dict(
            os.environ,
            {"MINDIE_MAINTENANCE_GROUP": "1", "MINDIE_MAINTENANCE_OWNER": "1"},
        ):
            with self.assertRaises(bounded.OwnerExited):
                bounded.run(
                    [sys.executable, "-c", "print('never')"],
                    "",
                    timeout=None,
                )
        with patch.dict(
            os.environ,
            {"MINDIE_MAINTENANCE_GROUP": "1", "MINDIE_MAINTENANCE_OWNER": str(parent)},
        ):
            output = bounded.run(
                [sys.executable, "-c", "print('ok')"],
                "",
                timeout=None,
            )
            self.assertIn("ok", output)
        with patch.dict(
            os.environ,
            {"MINDIE_MAINTENANCE_GROUP": "1", "MINDIE_MAINTENANCE_OWNER": "not-a-pid"},
        ):
            with self.assertRaises(bounded.OwnerExited):
                bounded.run([sys.executable, "-c", "print('never')"], "", timeout=None)


if __name__ == "__main__":
    unittest.main()
