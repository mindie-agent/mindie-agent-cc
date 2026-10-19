"""Live A2 (910B4) and A3 (910C) NPU cluster acceptance tests for Claude Code."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid
import pytest

pytestmark = pytest.mark.live_npu

from mindie_knowledge import consent_store
from mindie_knowledge.loop.activation import Admission
from mindie_knowledge.loop.cli import load_transcript_adapter
from mindie_knowledge.loop.engine import Engine
from mindie_knowledge.loop.handoff import prepare_schema
from mindie_knowledge.loop.lifecycle import retire_service
from mindie_knowledge.loop.store import Store

from support import SCRIPTS, env_for, make_config, redactor_path

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import identity  # noqa: E402
import mcp_server  # noqa: E402
import receipt_layout  # noqa: E402

A2_ENDPOINT = {
    "host": "173.125.1.2",
    "port": 22,
    "user": "root",
    "container": "mindie-bench-a2-core-20261017",
}
A3_ENDPOINT = {
    "host": "192.168.13.165",
    "port": 22,
    "user": "root",
    "container": "vaws-offload-pd165-20261016",
}


class LiveNpuAcceptanceTests(unittest.TestCase):
    def setUp(self):
        real_home = Path.home()
        self.tmp = Path(tempfile.mkdtemp(prefix="cc-live-npu-")).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        real_ssh = real_home / ".ssh"
        if real_ssh.is_dir():
            os.symlink(real_ssh, self.home / ".ssh")
        self.config = make_config(self.tmp, sharing=True)
        binding = json.loads(self.config.read_text(encoding="utf-8"))
        binding["claude_config_dir"] = str(self.tmp / "claude")
        self.config.write_text(json.dumps(binding, indent=2) + "\n", encoding="utf-8")
        self.engine = self.tmp / "cc.engine.json"
        self.community = self.tmp / "cc.community.json"
        self.admission = self.tmp / "domain" / "admission.sqlite3"
        self.project = self.tmp / "project"
        self.project.mkdir(parents=True, exist_ok=True)
        self.session = str(uuid.uuid4())
        self.env = {
            "HOME": str(self.home),
            "CLAUDE_CONFIG_DIR": str(self.tmp / "claude"),
            "MINDIE_CC_CONFIG": str(self.config),
            "MINDIE_DIAGNOSTICS_ROOT": str(self.tmp / "diagnostics"),
            "MINDIE_DIAGNOSTICS_CONFIG": str(self.tmp / "reporting-off.json"),
        }
        self.env_patch = patch.dict(os.environ, self.env)
        self.env_patch.start()

    def tearDown(self):
        try:
            retire_service(str(self.engine), control_timeout=5)
        except Exception:
            pass
        finally:
            self.env_patch.stop()
            shutil.rmtree(self.tmp, ignore_errors=True)

    def _call_remote(self, req_id: str, tool_name: str, arguments: dict) -> dict:
        call_id = f"call_{req_id}_{uuid.uuid4().hex[:8]}"
        native_name = f"mcp__plugin_mindie-agent_remote__{tool_name}"
        identity.publish_bind(self.session, call_id, native_name, arguments)
        response = mcp_server.handle(
            "remote",
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments,
                    "_meta": {"claudecode/toolUseId": call_id},
                },
            },
        )
        return response["result"]

    @staticmethod
    def _result_text(result: dict) -> str:
        return "\n".join(
            item.get("text", "")
            for item in result.get("content", [])
            if isinstance(item, dict)
        )

    def test_live_remote_mcp_server_a2_and_a3_npu_inspection(self):
        remote_dir = f"/tmp/cc-live-test-{uuid.uuid4().hex[:12]}"
        a2_file = f"{remote_dir}/a2-npu.txt"
        a3_file = f"{remote_dir}/a3-npu.txt"
        try:
            a2_bash = self._call_remote(
                "req-a2-bash",
                "remote_bash",
                {
                    **A2_ENDPOINT,
                    "command": f"mkdir -p {remote_dir} && npu-smi info | tee {a2_file}",
                    "max_output_tokens": 4000,
                },
            )
            self.assertFalse(a2_bash.get("isError"), a2_bash)
            a2_text = self._result_text(a2_bash)
            self.assertIn("910B4", a2_text)
            type(self)._live_a2_npu_text = a2_text

            a2_read = self._call_remote(
                "req-a2-read",
                "remote_read",
                {**A2_ENDPOINT, "file_path": a2_file},
            )
            self.assertFalse(a2_read.get("isError"), a2_read)
            self.assertIn("910B4", self._result_text(a2_read))

            a3_bash = self._call_remote(
                "req-a3-bash",
                "remote_bash",
                {
                    **A3_ENDPOINT,
                    "command": f"mkdir -p {remote_dir} && npu-smi info | tee {a3_file}",
                    "max_output_tokens": 4000,
                },
            )
            self.assertFalse(a3_bash.get("isError"), a3_bash)
            a3_text = self._result_text(a3_bash)
            self.assertIn("Ascend910", a3_text)
            self.assertIn("65536", a3_text)
            self.assertIn("0000:83:00.0", a3_text)
            type(self)._live_a3_npu_text = a3_text

            a3_read = self._call_remote(
                "req-a3-read",
                "remote_read",
                {**A3_ENDPOINT, "file_path": a3_file},
            )
            self.assertFalse(a3_read.get("isError"), a3_read)
            self.assertIn("Ascend910", self._result_text(a3_read))

            remote_base = self.tmp / "state" / "remote"
            self.assertEqual(receipt_layout.receipt_root(remote_base), remote_base)
            self.assertTrue((remote_base / "remote-layout.json").is_file())
        finally:
            for idx, endpoint in enumerate((A2_ENDPOINT, A3_ENDPOINT), start=1):
                self._call_remote(
                    f"req-cleanup-{idx}",
                    "remote_bash",
                    {**endpoint, "command": f"rm -rf {remote_dir}"},
                )

    def test_live_stop_hook_to_admission_and_redacted_capture(self):
        a2_npu_text = getattr(type(self), "_live_a2_npu_text", None)
        a3_npu_text = getattr(type(self), "_live_a3_npu_text", None)
        if not a2_npu_text or not a3_npu_text:
            a2_probe = self._call_remote(
                "probe-a2",
                "remote_bash",
                {**A2_ENDPOINT, "command": "npu-smi info", "max_output_tokens": 4000},
            )
            self.assertFalse(a2_probe.get("isError"), a2_probe)
            a2_npu_text = self._result_text(a2_probe)
            a3_probe = self._call_remote(
                "probe-a3",
                "remote_bash",
                {**A3_ENDPOINT, "command": "npu-smi info", "max_output_tokens": 4000},
            )
            self.assertFalse(a3_probe.get("isError"), a3_probe)
            a3_npu_text = self._result_text(a3_probe)

        consent_store.record_choice(self.tmp / "mindie-consent.json", "contribute")
        consent_store.record_reporting(self.tmp / "mindie-consent.json", "disabled")
        prepare_schema(str(self.engine))

        transcript = self.tmp / "claude" / "projects" / "live" / f"{self.session}.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        secret_token = "sk-live-secret-token-1234567890abcdef"
        records = [
            {
                "type": "queue-operation",
                "operation": "enqueue",
                "sessionId": self.session,
                "timestamp": "2026-10-18T12:00:00Z",
            },
            {
                "type": "user",
                "sessionId": self.session,
                "cwd": str(self.project),
                "timestamp": "2026-10-18T12:00:05Z",
                "message": {
                    "role": "user",
                    "content": f"Inspect A2 NPU on root@173.125.1.2 with token {secret_token} and A3 on root@192.168.13.165",
                },
            },
            {
                "type": "assistant",
                "sessionId": self.session,
                "cwd": str(self.project),
                "timestamp": "2026-10-18T12:00:10Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"A2 910B4 observation:\n{a2_npu_text[:1200]}\n"
                                f"A3 Ascend910 observation:\n{a3_npu_text[:1200]}"
                            ),
                        }
                    ],
                },
            },
        ]
        transcript.write_text(
            "".join(json.dumps(r) + "\n" for r in records),
            encoding="utf-8",
        )

        stop_event = {
            "hook_event_name": "Stop",
            "session_id": self.session,
            "prompt_id": "live-stop-1",
            "transcript_path": str(transcript),
            "stop_hook_active": False,
        }
        res = subprocess.run(
            [sys.executable, str(SCRIPTS / "mindie_launch.py"), "hook", "stop"],
            input=json.dumps(stop_event).encode("utf-8"),
            capture_output=True,
            env=env_for(self.config, self.env),
            timeout=15,
        )
        self.assertEqual(res.returncode, 0, res.stderr.decode())
        self.assertEqual(json.loads(res.stdout.decode()), {})

        store = Store(self.tmp / "domain", "vllm-ascend")
        try:
            engine = Engine(
                store,
                capture_mode="public-transcript",
                redactor_executable=redactor_path(),
                summary_command=None,
                settings_path=str(self.community),
                admission=Admission(str(self.admission)),
                transcript_adapter=load_transcript_adapter(
                    {"transcript_adapter": str(SCRIPTS / "transcript.py")}
                ),
            )
            rows = store.db.execute(
                "SELECT id, status FROM captures ORDER BY id"
            ).fetchall()
            self.assertGreaterEqual(len(rows), 1)
            for row_id, status in rows:
                if status in {"queued", "pending", "deferred"}:
                    engine._process(row_id)
            drafts = "\n".join(doc["content"] for doc in store.drafts_changed())
        finally:
            store.close()

        self.assertIn("910B4", drafts)
        self.assertIn("Ascend910", drafts)
        self.assertIn("65536", drafts)
        self.assertNotIn(secret_token, drafts)
        self.assertNotIn("173.125.1.2", drafts)
        self.assertNotIn("192.168.13.165", drafts)


if __name__ == "__main__":
    unittest.main()
