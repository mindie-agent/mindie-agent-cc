"""Real local frontend processes; no native model or remote execution."""
import json
import os
from pathlib import Path
import selectors
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import process_probe

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
CHILD = r'''
import json,os,subprocess,sys,time
from pathlib import Path
root=Path(__file__).resolve().parents[2]
message=json.loads(sys.stdin.readline())
large=message.get('params',{}).get('arguments',{}).get('large')
grand=None if large else subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
(root/'pids.json').write_text(json.dumps([os.getpid(),grand.pid if grand else None]))
if not large: time.sleep(30)
print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':{'content':[{'type':'text','text':'x'*220000 if large else 'done'}]}}),flush=True)
'''


@unittest.skipUnless(os.name == "posix", "real POSIX pipe/process ownership")
class FrontControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        generation = self.root / "generation"
        (generation / "scripts").mkdir(parents=True)
        (generation / "scripts/mcp_server.py").write_text(CHILD, encoding="utf-8")
        shutil.copyfile(SCRIPTS.parent / "harness-compatibility.json",
                        generation / "harness-compatibility.json")
        native = self.root / "fixture-claude"
        native.write_text("#!/bin/sh\nprintf '2.1.269 (Claude Code)\\n'\n", encoding="utf-8")
        native.chmod(0o700)
        (self.root / "engine.json").write_text(json.dumps({
            "root": str(self.root / "domain"), "domain": "fixture",
            "redactor_executable": str(native),
            "transcript_adapter": str(generation / "scripts" / "capture_source.py"),
            "service_launch_context": {
                "paths": {"MINDIE_CC_BIN": str(native)}, "environment": [],
            },
        }), encoding="utf-8")
        config = self.root / "adapter.json"
        config.write_text(json.dumps({
            "schema": "mindie-cc-installation/1", "facade_protocol": 1,
            "state_dir": str(self.root / "state"), "base_config": str(config),
            "adapter_config": str(config), "runtime_scripts": str(generation / "scripts"),
            "python": sys.executable, "engine_config": str(self.root / "engine.json"),
        }), encoding="utf-8")
        for path in SCRIPTS.glob("*.py"):
            if path.name != "mcp_server.py":
                shutil.copyfile(path, generation / "scripts" / path.name)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("MINDIE_", "PYTHONPATH", "REMOTE_DEV_"))}
        env["MINDIE_DIAGNOSTICS_ROOT"] = str(self.root / "diagnostics")
        env["MINDIE_DIAGNOSTICS_CONFIG"] = str(self.root / "diagnostics.json")
        self.proc = subprocess.Popen(
            [sys.executable, str(generation / "scripts" / "mindie_launch.py"), "--config",
             str(config), "mcp", "remote"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            start_new_session=True)

    def send(self, value):
        self.proc.stdin.write((json.dumps(value) + "\n").encode())
        self.proc.stdin.flush()

    def start_call(self, large=False):
        self.send({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "remote_job_status",
                              "arguments": {"job_id": "original-job", "large": large}}})
        until = time.monotonic() + 3
        while not (self.root / "pids.json").exists() and time.monotonic() < until:
            time.sleep(.01)
        self.assertTrue((self.root / "pids.json").exists())

    def read_response(self):
        selector = selectors.DefaultSelector()
        selector.register(self.proc.stdout, selectors.EVENT_READ)
        try:
            self.assertTrue(selector.select(3), "MCP response was not delivered")
            return json.loads(self.proc.stdout.readline())
        finally:
            selector.close()

    def tearDown(self):
        if self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait(timeout=2)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            stream.close()
        pidfile = self.root / "pids.json"
        if pidfile.exists():
            for pid in json.loads(pidfile.read_text(encoding="utf-8")):
                if pid is None:
                    continue
                result = process_probe.status(pid)
                state = result
                if state and not state.startswith("Z"):
                    os.kill(pid, signal.SIGKILL)
                    self.fail("owned child remains alive")
        self.tmp.cleanup()

    def test_cancel_keeps_ping_responsive_and_preserves_original_job(self):
        self.start_call()
        start = time.monotonic()
        self.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                   "params": {"requestId": 1}})
        self.send({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        data = b""
        selector = selectors.DefaultSelector()
        selector.register(self.proc.stdout, selectors.EVENT_READ)
        try:
            while data.count(b"\n") < 2 and time.monotonic() - start < 2:
                if selector.select(.05):
                    data += os.read(self.proc.stdout.fileno(), 8192)
        finally:
            selector.close()
        responses = {x["id"]: x for x in map(json.loads, data.splitlines())}
        self.assertEqual(responses[2]["result"], {})
        self.assertTrue(responses[1]["result"]["isError"])
        self.assertEqual(responses[1]["result"]["structuredContent"], {
            "stage": "cancelled", "remote_outcome": "unconfirmed", "job_id": "original-job"})
        self.assertLess(time.monotonic() - start, 1)
        self.proc.stdin.close()
        self.proc.wait(timeout=3)

    def test_eof_cancels_owned_process_tree(self):
        self.start_call()
        self.proc.stdin.close()
        self.proc.wait(timeout=3)

    def test_eof_with_unread_large_response_is_bounded(self):
        self.start_call(large=True)
        time.sleep(.15)
        self.proc.stdin.close()
        self.proc.wait(timeout=3)

    def test_existing_mcp_uses_new_complete_binding_after_inflight_cancellation(self):
        sys.path.insert(0, str(SCRIPTS))
        from install_state import LockTimeout, OperationLock, atomic_write

        self.start_call()
        config = self.root / "adapter.json"
        old = json.loads(config.read_text(encoding="utf-8"))
        with self.assertRaises(LockTimeout):
            with OperationLock(old).exclusive(timeout=0):
                self.fail("an in-flight operation allowed activation")
        self.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                   "params": {"requestId": 1}})
        cancelled = self.read_response()
        self.assertTrue(cancelled["result"]["isError"])
        self.assertEqual(cancelled["result"]["structuredContent"]["job_id"], "original-job")

        scripts = self.root / "selected-runtime" / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "mcp_server.py").write_text(
            "import json,os,sys\n"
            "request=json.loads(sys.stdin.readline())\n"
            "value={'helper':__file__,'adapter':os.environ['MINDIE_CC_CONFIG']}\n"
            "print(json.dumps({'jsonrpc':'2.0','id':request['id'],"
            "'result':{'structuredContent':value,'content':[]}}))\n",
            encoding="utf-8",
        )
        fixed = self.root / "selected.adapter.json"
        selected = dict(old, adapter_config=str(fixed), runtime_scripts=str(scripts))
        fixed.write_text(json.dumps(selected), encoding="utf-8")
        with OperationLock(old).exclusive(timeout=1):
            atomic_write(config, selected)
        native_pid = self.proc.pid
        self.send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                   "params": {"name": "remote_job_status",
                              "arguments": {"job_id": "original-job"}}})
        response = self.read_response()
        self.assertEqual(response["result"]["structuredContent"], {
            "helper": str(scripts / "mcp_server.py"), "adapter": str(fixed),
        })
        self.assertEqual(self.proc.pid, native_pid)
        self.assertIsNone(self.proc.poll())
        self.proc.stdin.close()
        self.proc.wait(timeout=3)
