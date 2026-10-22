"""Real POSIX worker tests with a synthetic native Claude executable.

The contract classes stay in ``test_organizer.py``; this module owns the
process-level scenarios and imports its shared helpers from that module.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from mindie_knowledge.loop.process import bounded_run  # noqa: E402
from mindie_knowledge.materials import summarizer  # noqa: E402
from test_organizer import ORGANIZER, make_payload, native_envelope  # noqa: E402
import process_probe  # noqa: E402



def run_worker(payload=None, env=None, args=()):
    return subprocess.run(
        [sys.executable, str(ORGANIZER), *args],
        input=json.dumps(payload).encode() if payload is not None else b"",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, timeout=30,
    )


@unittest.skipUnless(os.name == "posix", "owned native POSIX process tree")
class WorkerNativeProcessTests(unittest.TestCase):
    """The real worker subprocess against a synthetic executable `claude`."""

    def _profile(self, root):
        import support

        config = support.make_config(Path(root) / "cfg", sharing=False)
        claude_home = Path(root) / "claude-profile"
        claude_home.mkdir()
        (claude_home / "settings.json").write_text(json.dumps({
            "env": {"ANTHROPIC_MODEL": "fixture-model"},
        }), encoding="utf-8")
        adapter = json.loads(Path(config).read_text(encoding="utf-8"))
        adapter["claude_config_dir"] = str(claude_home)
        Path(config).write_text(json.dumps(adapter, indent=2) + "\n", encoding="utf-8")
        return config

    def _fake_claude(self, root, tail="", before_result=""):
        fake = Path(root) / "fake-claude"
        fake.write_text(
            "#!" + sys.executable + "\n"
            "import json, re, sys, time\n"
            "prompt = sys.stdin.buffer.read().decode()\n"
            "ids = list(dict.fromkeys(re.findall(r'[0-9a-f]{64}', prompt)))\n"
            "metadata = dict(blocks=[dict(block_id=i, title='T', summary='S') for i in ids],\n"
            "                navigation=dict(title='N', summary='NS'))\n"
            + before_result
            + "print(json.dumps(dict(type='result', subtype='success', is_error=False, num_turns=1,\n"
            "      result='', structured_output=metadata,\n"
            "      usage=dict(input_tokens=10, cache_creation_input_tokens=3,\n"
            "                 cache_read_input_tokens=2, output_tokens=5))), flush=True)\n"
            + tail,
            encoding="utf-8",
        )
        fake.chmod(0o700)
        return fake

    def test_managed_profile_outlives_result_until_descendant_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._profile(tmp)
            info = Path(tmp) / "writer-info.json"
            inner = (
                "import os, pathlib, time\n"
                "profile = pathlib.Path.cwd()\n"
                "(profile / 'writer-ready').write_text(str(os.getpid()))\n"
                "while True:\n"
                "    (profile / 'writer-active').write_text(str(time.monotonic()))\n"
                "    if (profile / 'result-saved').exists():\n"
                "        (profile / 'writer-ack').write_text('observed saved result')\n"
                "    time.sleep(0.01)\n"
            )
            before_result = (
                "import os, pathlib, subprocess\n"
                "writer = subprocess.Popen([sys.executable, '-c', " + repr(inner) + "])\n"
                "profile = pathlib.Path.cwd()\n"
                "deadline = time.monotonic() + 5\n"
                "while not (profile / 'writer-ready').exists():\n"
                "    if writer.poll() is not None or time.monotonic() > deadline:\n"
                "        raise RuntimeError('fixture writer did not become ready')\n"
                "    time.sleep(0.01)\n"
                "pathlib.Path(" + repr(str(info)) + ").write_text(json.dumps(dict(\n"
                "    pid=writer.pid, profile=str(profile), group=os.getpgrp())))\n"
            )
            fake = self._fake_claude(tmp, "time.sleep(30)\n", before_result)
            env = dict(MINDIE_CC_CONFIG=str(config), MINDIE_CC_BIN=str(fake))
            with patch.dict(os.environ, env):
                payload = make_payload()
            saved = []
            cleanup = {}

            def save(line):
                saved.append(json.loads(line))
                writer = json.loads(info.read_text(encoding="utf-8"))
                profile = Path(writer["profile"])
                self.assertTrue((profile / "claude-config").is_dir())
                self.assertEqual(os.getpgid(writer["pid"]), writer["group"])
                (profile / "result-saved").touch()
                deadline = time.monotonic() + 3
                while not (profile / "writer-ack").exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue((profile / "writer-ack").exists(),
                                "descendant must still write when the result is saved")

            started = time.monotonic()
            with patch.dict(os.environ, env):
                raw = bounded_run([sys.executable, str(ORGANIZER)], json.dumps(payload),
                                  timeout=None, max_output=summarizer.MAX_RESPONSE_BYTES * 3,
                                  on_result=save,
                                  cleanup_receipt=cleanup)
            value = json.loads(raw)
            self.assertLess(time.monotonic() - started, 10)
            self.assertEqual(value["status"], "returned")
            self.assertEqual(saved[0]["status"], "returned")
            self.assertEqual(value["usage"],
                             dict(input_tokens=13, cached_input_tokens=2, output_tokens=5))
            self.assertTrue(value["usage_known"])
            self.assertFalse(value["cleanup_failed"])
            self.assertFalse(cleanup.get("cleanup_failed", False))
            summarizer.validate_outcome(value, payload)
            writer = json.loads(info.read_text(encoding="utf-8"))
            self.assertFalse(Path(writer["profile"]).exists())
            self.assertFalse(Path(writer["profile"]).parent.exists())
            state = process_probe.status(writer["pid"])
            self.assertTrue(not state or state.startswith("Z"), state)

    def test_incomplete_managed_ownership_prevents_the_native_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._profile(tmp)
            marker = Path(tmp) / "native-started"
            fake = self._fake_claude(tmp, "open(%r, 'w').write('called')\n" % str(marker))
            env = dict(
                MINDIE_CC_CONFIG=str(config),
                MINDIE_CC_BIN=str(fake),
                # A maintenance declaration alone cannot transfer profile
                # cleanup to an owner without the complete shared protocol.
                MINDIE_MAINTENANCE_GROUP="1",
                MINDIE_MAINTENANCE_OWNER="1",
            )
            with patch.dict(os.environ, env):
                payload = make_payload()
                proc = run_worker(payload)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:400])
            value = json.loads(proc.stdout.decode())
            self.assertEqual(value["status"], "failed")
            self.assertEqual(value["error"], "configuration")
            self.assertEqual(value["error_reason"], "configuration_invalid")
            self.assertEqual(value["model_calls"], 0)
            self.assertFalse(marker.exists())
            summarizer.validate_outcome(value, payload)

    def test_terminal_failure_precedes_nonzero_native_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._profile(tmp)
            native = json.loads(native_envelope(None, is_error=True, num_turns=2))
            native.update(subtype="error_max_turns", terminal_reason="max_turns",
                          errors=["PRIVATE_NATIVE_ERROR"])
            fake = Path(tmp) / "fake-claude"
            fake.write_text(
                "#!" + sys.executable + "\nimport sys\n"
                "sys.stdin.buffer.read()\n"
                "print(" + repr(json.dumps(native)) + ", flush=True)\n"
                "sys.exit(7)\n", encoding="utf-8")
            fake.chmod(0o700)
            env = dict(MINDIE_CC_CONFIG=str(config), MINDIE_CC_BIN=str(fake))
            with patch.dict(os.environ, env):
                payload = make_payload()
                proc = run_worker(payload)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:400])
            value = json.loads(proc.stdout)
            self.assertEqual(value["status"], "failed")
            self.assertEqual(value["error_reason"], "native_max_turns")
            self.assertEqual(value["model_calls"], 1)
            self.assertTrue(value["usage_known"])
            self.assertEqual(value["usage"]["input_tokens"], 13)
            self.assertEqual(json.loads(value["raw_result"])["subtype"], "error_max_turns")
            self.assertNotIn("PRIVATE_NATIVE_ERROR", json.dumps(value))
            summarizer.validate_outcome(value, payload)


if __name__ == "__main__":
    unittest.main()
