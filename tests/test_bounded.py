import os
import subprocess
import sys
import time
import unittest

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


if __name__ == "__main__":
    unittest.main()
