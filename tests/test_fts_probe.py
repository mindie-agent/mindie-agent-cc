"""The runtime probe checks the current worker contract."""

from contextlib import redirect_stdout
import io
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

import candidate_validate
import runtime_probe

CURRENT = Path(sys.executable)


def _child_env():
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run(python, source, extra_env=None):
    env = _child_env()
    if extra_env:
        env.update(extra_env)
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
    def test_service_launch_apis_are_required_before_install(self):
        from mindie_knowledge.loop import cli

        script = runtime_probe.build_probe_script(str(SCRIPTS / "transcript.py"))
        for name in ("prepare_service", "ensure_service"):
            with self.subTest(name=name), patch.object(cli, name, None), \
                    redirect_stdout(io.StringIO()) as output:
                exec(script, {})
            self.assertIn(f"{name} is unavailable", output.getvalue())
            self.assertTrue(output.getvalue().startswith("MISSING:"))

    def test_probe_does_not_require_retired_fts_capability(self):
        script = runtime_probe.build_probe_script(str(SCRIPTS / "transcript.py"))
        self.assertNotIn("contentless_delete", script)
        self.assertNotIn("fts5", script.lower())

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
