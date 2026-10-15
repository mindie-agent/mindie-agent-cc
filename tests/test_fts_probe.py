"""The runtime probe checks the current worker contract.

SQLite FTS5 contentless_delete is not a requirement of the current core.
These checks use the selected interpreter, sys.executable. CI installs the
pinned runtime there, so the current-core check runs instead of skipping.
An old core is a synthetic interpreter whose modules omit the current
contract. No private filesystem path is required. These checks do not claim
host acceptance.
"""

import os
from contextlib import redirect_stdout
import io
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

import runtime_probe
import candidate_validate

CURRENT = Path(sys.executable)


def _child_env():
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run(python, source, extra_env=None):
    env = _child_env()
    if extra_env:
        env.update(extra_env)
        # The old-core fixture is the only caller that sets PYTHONPATH.
        # _child_env removes it so ordinary checks cannot inherit one.
        if "PYTHONPATH" in extra_env:
            env["PYTHONPATH"] = extra_env["PYTHONPATH"]
    return subprocess.run(
        [str(python), "-c", source],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def _missing_contract(directory):
    """Packages that import and omit the current worker symbols.

    The selected interpreter still runs the probe. These modules hide the
    pinned runtime for that one process, so rejection does not depend on a
    private older virtualenv.
    """
    root = Path(directory)
    packages = {
        "mindie_knowledge/__init__.py": "",
        "mindie_knowledge/loop/__init__.py": "",
        "mindie_knowledge/materials/__init__.py": "",
        "mindie_knowledge/community/__init__.py": "",
        "remote_dev/__init__.py": "",
        "remote_dev/mcp/__init__.py": "",
        "langmem/__init__.py": "",
        "langmem/short_term.py": "def summarize_messages(*args, **kwargs):\n    return None\n",
    }
    for relative, body in packages.items():
        path = root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


class WorkerProbeTests(unittest.TestCase):
    def test_git_layout_preflight_is_required_before_install(self):
        from mindie_knowledge import git_layout

        script = runtime_probe.build_probe_script(str(SCRIPTS / "transcript.py"))
        with patch.object(git_layout, "validate_config_feed_layout", None), \
                redirect_stdout(io.StringIO()) as output:
            exec(script, {})
        self.assertIn("knowledge Git layout preflight is unavailable", output.getvalue())
        self.assertTrue(output.getvalue().startswith("MISSING:"))

    def test_summary_scratch_contract_is_required_before_install(self):
        from mindie_knowledge.loop import summary_scratch

        script = runtime_probe.build_probe_script(str(SCRIPTS / "transcript.py"))
        for name, value, message in (
            ("create_profile", None, "summary scratch profile API is unavailable"),
            ("create_profile", lambda: None, "summary scratch profile API is unavailable"),
            ("ENVIRONMENT_KEY", "", "summary scratch profile API is unavailable"),
            ("ScratchOwnershipError", ValueError,
             "summary scratch ownership failure contract is unavailable"),
        ):
            with self.subTest(name=name, value=value), \
                    patch.object(summary_scratch, name, value), \
                    redirect_stdout(io.StringIO()) as output:
                exec(script, {})
            self.assertIn(message, output.getvalue())
            self.assertTrue(output.getvalue().startswith("MISSING:"))

    def test_service_launch_apis_are_required_before_install(self):
        from mindie_knowledge.loop import cli, service_context

        script = runtime_probe.build_probe_script(str(SCRIPTS / "transcript.py"))
        for module, name, message in (
            (cli, "prepare_service", "service lifecycle is unavailable"),
            (cli, "ensure_client_service", "service lifecycle is unavailable"),
            (cli, "prepare_client_service", "service lifecycle is unavailable"),
            (service_context, "validate", "service launch context validation is unavailable"),
        ):
            with self.subTest(name=name), patch.object(module, name, None), \
                    redirect_stdout(io.StringIO()) as output:
                exec(script, {})
            self.assertIn(message, output.getvalue())
            self.assertTrue(output.getvalue().startswith("MISSING:"))

    def test_probe_does_not_require_retired_fts_capability(self):
        script = runtime_probe.build_probe_script(str(SCRIPTS / "transcript.py"))
        self.assertNotIn("contentless_delete", script)
        self.assertNotIn("fts5", script.lower())
        self.assertIn("usage_totals", script)
        self.assertIn("validate_identity", script)
        self.assertIn("validate_outcome", script)

    def test_current_core_satisfies_worker_probe(self):
        script = runtime_probe.build_probe_script(str(SCRIPTS / "transcript.py"))
        completed = _run(CURRENT, script)
        self.assertEqual(completed.returncode, 0, completed.stderr[-500:])
        self.assertEqual(completed.stdout.strip(), "OK")

    def test_old_core_is_not_accepted_as_worker_compatible(self):
        script = runtime_probe.build_probe_script(str(SCRIPTS / "transcript.py"))
        with tempfile.TemporaryDirectory() as tmp:
            completed = _run(CURRENT, "import sys;sys.path.insert(0," + repr(str(_missing_contract(tmp))) + ")\n" + script)
        self.assertNotEqual(completed.stdout.strip(), "OK")
        self.assertIn("MISSING:", completed.stdout)

    def test_exit1_after_ok_is_rejected_before_writes(self):
        fault = "print('OK', flush=True)\nraise SystemExit(1)\n"
        continued = []
        with patch.object(candidate_validate, "build_probe_script", return_value=fault):
            with self.assertRaises(SystemExit) as caught:
                candidate_validate.runtime_check(SCRIPTS)
                continued.append("write")
        self.assertEqual(caught.exception.code, 1)
        self.assertEqual(continued, [])


if __name__ == "__main__":
    unittest.main()
