"""The runtime probe checks the current worker contract.

SQLite FTS5 contentless_delete is not a requirement of the current core.
These checks name the interpreter they execute and do not claim host acceptance.
"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import inspect

import setup
from support import explicit_local_candidate_args

CURRENT = Path("/Users/maoxx241/code/mindie-release-20261014/test-venv/bin/python")
OLD = Path("/Users/maoxx241/code/mindie-release-20261014/cc-test-venv/bin/python")


def _child_env():
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run(python, source):
    return subprocess.run(
        [str(python), "-c", source],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        env=_child_env(),
    )


class WorkerProbeTests(unittest.TestCase):
    def test_probe_does_not_require_retired_fts_capability(self):
        script = setup.probe_script(str(SCRIPTS / "transcript.py"))
        self.assertNotIn("contentless_delete", script)
        self.assertNotIn("fts5", script.lower())
        self.assertIn("usage_totals", script)
        self.assertIn("validate_identity", script)
        self.assertIn("validate_outcome", script)
        self.assertEqual(setup._FTS_PROBE, "")

    @unittest.skipUnless(CURRENT.is_file(), "current core interpreter is absent")
    def test_current_core_satisfies_worker_probe(self):
        script = setup.probe_script(str(SCRIPTS / "transcript.py"))
        completed = _run(CURRENT, script)
        self.assertEqual(completed.returncode, 0, completed.stderr[-500:])
        self.assertEqual(completed.stdout.strip(), "OK")

    @unittest.skipUnless(OLD.is_file(), "old core interpreter is absent")
    def test_old_core_is_not_accepted_as_worker_compatible(self):
        script = setup.probe_script(str(SCRIPTS / "transcript.py"))
        completed = _run(OLD, script)
        self.assertNotEqual(completed.stdout.strip(), "OK")
        self.assertIn("MISSING:", completed.stdout)

    def test_exit1_after_ok_is_rejected_before_writes(self):
        fault = "print('OK', flush=True)\nraise SystemExit(1)\n"
        continued = []
        real = setup.run

        def wrapped(argv, data="", timeout=15, **kwargs):
            script = argv[-1] if argv else ""
            if "commit_id" in script:
                return "OK\n"
            return real(argv, data, timeout=timeout, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            direct = subprocess.run(
                [sys.executable, "-c", fault], cwd=tmp, capture_output=True,
                text=True, timeout=15, env=_child_env(),
            )
            self.assertEqual(direct.returncode, 1)
            self.assertEqual(direct.stdout, "OK\n")
            with patch.object(setup, "run", wrapped), patch.object(setup, "PROBE_SCRIPT", fault):
                with self.assertRaises(SystemExit) as caught:
                    kwargs = {}
                    declared = explicit_local_candidate_args()
                    if declared and "local_candidates" in inspect.signature(setup.probe_runtime).parameters:
                        kwargs["local_candidates"] = {
                            "mindie-knowledge": declared[-1].split("=", 1)[1]
                        }
                    setup.probe_runtime(sys.executable, **kwargs)
                    continued.append("write")
        self.assertEqual(continued, [])
        self.assertIn("knowledge runtime probe failed", str(caught.exception.code))


if __name__ == "__main__":
    unittest.main()
