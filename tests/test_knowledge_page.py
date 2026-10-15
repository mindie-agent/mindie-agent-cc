"""Schema and immutable-block wire. No native host is attached.

A call without a locally published PreToolUse bind fails closed. Real current
material packages exercise navigation and exact block reads through the
adapter's duplicated text/structured-content response.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import SCRIPTS, env_for, make_config

SESSION = "645fae2e-ef23-4e7d-9559-f4dd0cb9db6a"
NATIVE = "mcp__plugin_mindie-agent_knowledge__knowledge_explain"

HELPER = r"""
import json, os, sys
sys.path.insert(0, os.environ["SCRIPTS"])
os.environ["MINDIE_CC_CONFIG"] = os.environ["CONFIG"]
import mcp_server

page = json.loads(open(os.environ["PAGE"], encoding="utf-8").read())

def knowledge_call(name, args, session):
    if name != "knowledge_explain":
        raise AssertionError(name)
    if session != os.environ["SESSION"]:
        raise AssertionError(session)
    if args.get("ref") != os.environ["REF"]:
        raise AssertionError("ref")
    if set(args) != {"ref"}:
        raise AssertionError("unexpected block-read arguments")
    if "session_id" in args or "_session_id" in args or "request_nonce" in args:
        raise AssertionError("identity leaked into the knowledge call")
    return page

mcp_server.knowledge_call = knowledge_call
response = mcp_server.handle("knowledge", json.loads(sys.stdin.read()))
if response is None:
    raise SystemExit("helper returned no response")
sys.stdout.write(mcp_server.canonical(response) + "\n")
"""

STUB = r"""
import json, sys
message = json.loads(sys.stdin.read())
blob = "\u6d4b" * 88000
body = {
    "jsonrpc": "2.0",
    "id": message["id"],
    "result": {"content": [{"type": "text", "text": blob}], "isError": False},
}
sys.stdout.write(json.dumps(body, ensure_ascii=False))
"""


def _bodies():
    from mindie_knowledge.materials.store import MAX_BLOCK_BYTES
    return {
        "ascii": "A" * MAX_BLOCK_BYTES,
        "chinese": "\u6d4b" * (MAX_BLOCK_BYTES // 3),
        "nonbmp": "\U00020000" * (MAX_BLOCK_BYTES // 4),
        "escape": '\\"' * (MAX_BLOCK_BYTES // 2),
    }


class KnowledgePageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.config = make_config(self.tmp)
        self.diag = self.tmp / "diag"
        (self.diag / "logs").mkdir(parents=True)
        (self.diag / "off.json").write_text('{"decision":"disabled"}\n', encoding="utf-8")
        self._saved = {
            key: os.environ.get(key)
            for key in (
                "MINDIE_CC_CONFIG",
                "MINDIE_DIAGNOSTICS_CONFIG",
                "MINDIE_DIAGNOSTICS_ROOT",
            )
        }
        os.environ["MINDIE_CC_CONFIG"] = str(self.config)
        os.environ["MINDIE_DIAGNOSTICS_CONFIG"] = str(self.diag / "off.json")
        os.environ["MINDIE_DIAGNOSTICS_ROOT"] = str(self.diag / "logs")
        sys.path.insert(0, str(SCRIPTS))

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_explain_block_schema_and_related_query_continuation(self):
        import mcp_server

        explain = next(item for item in mcp_server.KNOWLEDGE_TOOL_DEFS if item["name"] == "knowledge_explain")
        query = next(item for item in mcp_server.KNOWLEDGE_TOOL_DEFS if item["name"] == "knowledge_query")
        self.assertEqual(set(explain["inputSchema"]["properties"]), {"ref"})
        self.assertEqual(explain["inputSchema"]["required"], ["ref"])
        self.assertNotIn("request_nonce", explain["inputSchema"]["properties"])
        self.assertIn("next_block_ref", explain["description"])
        self.assertIn("slice", explain["description"])
        self.assertNotIn("offset", query["inputSchema"]["properties"])
        self.assertEqual(query["inputSchema"]["properties"]["limit"]["maximum"], 20)
        self.assertEqual(set(query["inputSchema"]["properties"]),
                         {"query", "limit", "conditions", "continuation"})
        from jsonschema import validate
        validate({"query": "synthetic material"}, query["inputSchema"])
        validate({"continuation": "opaque-next-page", "limit": 3}, query["inputSchema"])
        for unsupported in ("oneOf", "anyOf", "allOf"):
            self.assertNotIn(unsupported, query["inputSchema"])

    def test_query_rejects_invalid_arguments_before_starting_service(self):
        import mcp_server
        from unittest.mock import patch

        for invalid in ({}, {"query": "x", "continuation": "opaque-next-page"},
                        {"continuation": "malformed"}, {"query": "x", "limit": True}):
            with self.subTest(invalid=invalid), \
                    patch("knowledge_service.existing_service") as existing, \
                    patch("knowledge_service.ensure_service") as start, \
                    patch("mindie_knowledge.loop.cli.rpc") as rpc:
                with self.assertRaises(ValueError):
                    mcp_server.knowledge_call("knowledge_query", invalid, SESSION)
                existing.assert_not_called()
                start.assert_not_called()
                rpc.assert_not_called()

    def test_query_does_not_time_out_a_cold_native_read(self):
        import mcp_server
        import threading
        import time
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from unittest.mock import patch

        result = {"results": [{"ref": "synthetic-result"}]}
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                time.sleep(5.25)
                body = json.dumps({"ok": True, "result": result}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except BrokenPipeError:
                    pass

        with HTTPServer(("127.0.0.1", 0), Handler) as server:
            server.timeout = 10
            worker = threading.Thread(target=server.handle_request)
            worker.start()
            connection = {"url": f"http://127.0.0.1:{server.server_port}", "token": "synthetic"}
            try:
                with patch("knowledge_service.existing_service", return_value=connection):
                    self.assertEqual(
                        mcp_server.knowledge_call("knowledge_query", {"query": "synthetic"}, SESSION),
                        result,
                    )
            finally:
                worker.join(timeout=10)
            self.assertFalse(worker.is_alive())
        self.assertEqual(len(calls), 1)

    def test_explain_without_host_bind_fails_closed(self):
        message = {
            "jsonrpc": "2.0",
            "id": "no-host",
            "method": "tools/call",
            "params": {
                "name": "knowledge_explain",
                "arguments": {"ref": "mindie://vllm-ascend/missing"},
                "_meta": {"claudecode/toolUseId": "call-no-host"},
            },
        }
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "mcp_server.py"), "knowledge", "--once"],
            input=(json.dumps(message) + "\n").encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env_for(self.config),
            timeout=15,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        response = json.loads(proc.stdout.decode())
        text = response["result"]["content"][0]["text"]
        self.assertTrue(response["result"]["isError"])
        self.assertIn("host did not authorize", text)
        self.assertNotIn("content_offset", text)

    def test_store_navigation_and_blocks_fit_knowledge_bound(self):
        import bounded
        import mindie_launch
        from mindie_knowledge.loop.store import Store
        from mindie_knowledge.materials import MaterialStore
        from mindie_knowledge.materials.references import task_ref

        knowledge_bound = mindie_launch.KNOWLEDGE_MAX_OUTPUT

        helper = self.tmp / "helper.py"
        helper.write_text(HELPER, encoding="utf-8")
        store = Store(self.tmp / "store", "vllm-ascend")
        author = MaterialStore(self.tmp / "author", "vllm-ascend")
        sizes = {}
        try:
            for name, body in _bodies().items():
                task = hashlib.sha256(name.encode()).hexdigest()
                second = "Later correction; reference material only.\n"
                author.append_batch(
                    task,
                    [dict(block_id=hashlib.sha256(f"{name}-{i}".encode()).hexdigest(),
                          text=text, source_range=dict(part=i),
                          title=f"Observation {i}", summary="Synthetic component fixture.")
                     for i, text in enumerate((body, second))],
                    "Two synthetic observations.", title="Local block fixture",
                    domain="vllm-ascend", status="complete", promote=True,
                )
                store.install_feed([author.export_task(task)], feed_ident="f" * 64)
                ref = task_ref("vllm-ascend", task)
                previous = None
                for position in range(3):
                    page = store.explain(ref)
                    out = self._helper(bounded, helper, name, ref, position, page)
                    response = json.loads(out)
                    result = response["result"]
                    self.assertIs(result["isError"], False)
                    text = json.loads(result["content"][0]["text"])
                    structured = result["structuredContent"]
                    self.assertEqual(text, page)
                    self.assertEqual(structured, page)
                    self.assertEqual(structured["ref"], ref)
                    nbytes = len(out.encode())
                    self.assertLessEqual(nbytes, knowledge_bound, f"{name} {nbytes}")
                    self.assertNotIn("next_offset", structured)
                    if position == 0:
                        self.assertEqual(structured["kind"], "task")
                        self.assertNotIn("content", structured)
                        ref = structured["first_block_ref"]
                    else:
                        self.assertEqual(structured["kind"], "block")
                        self.assertEqual(structured["content"], body if position == 1 else second)
                        self.assertEqual(structured["previous_block_ref"], previous)
                        previous, ref = ref, structured["next_block_ref"]
                        if position == 1:
                            sizes[name] = nbytes
                self.assertIsNone(ref)
        finally:
            store.close()
            author.close()
        self.assertEqual(set(sizes), set(_bodies()))

    def _helper(self, bounded, helper, name, ref, offset, page):
        import identity
        import mindie_launch

        path = self.tmp / f"{name}-{offset}.json"
        path.write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
        call_id = f"call-{name}-{offset}"
        arguments = {"ref": ref}
        identity.publish_bind(SESSION, call_id, NATIVE, arguments)
        message = {
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {
                "name": "knowledge_explain",
                "arguments": arguments,
                "_meta": {"claudecode/toolUseId": call_id},
            },
        }
        env = env_for(
            self.config,
            extra={
                "SCRIPTS": str(SCRIPTS),
                "PYTHONIOENCODING": "utf-8",
                "PAGE": str(path),
                "REF": ref,
                "OFFSET": str(offset),
                "SESSION": SESSION,
                "CONFIG": str(self.config),
                "MINDIE_DIAGNOSTICS_CONFIG": os.environ["MINDIE_DIAGNOSTICS_CONFIG"],
                "MINDIE_DIAGNOSTICS_ROOT": os.environ["MINDIE_DIAGNOSTICS_ROOT"],
            },
        )
        return bounded.run(
            [sys.executable, str(helper)],
            json.dumps(message),
            timeout=30,
            max_output=mindie_launch.KNOWLEDGE_MAX_OUTPUT,
            env=env,
        )

    def test_dispatch_raises_knowledge_bound_only(self):
        import bounded
        import mindie_launch

        generation = self.tmp / "generation"
        scripts = generation / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "mcp_server.py").write_text(STUB, encoding="utf-8")
        state = Path(json.loads(self.config.read_text(encoding="utf-8"))["state_dir"])
        current = state / "update" / "current.json"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_text(json.dumps({
            "generation": str(generation),
            "python": sys.executable,
            "adapter_config": str(self.config),
            "sha": "b" * 40,
        }), encoding="utf-8")
        raw = json.dumps({"jsonrpc": "2.0", "id": "cap-1", "method": "tools/list"}).encode()
        response = mindie_launch._dispatch("knowledge", raw, "cap-1")
        text = response["result"]["content"][0]["text"]
        self.assertGreater(len(text.encode()), 256 * 1024)
        self.assertLess(len(text.encode()), mindie_launch.KNOWLEDGE_MAX_OUTPUT)
        self.assertEqual(bounded.MAX_OUTPUT, 256 * 1024)
        with self.assertRaises(mindie_launch.DispatchFailure) as caught:
            mindie_launch._dispatch("remote", raw, "cap-1")
        self.assertEqual(caught.exception.stage, "helper_failed")

    def test_knowledge_bound_accepts_output_past_512kib(self):
        import bounded
        import mindie_launch

        self.assertEqual(mindie_launch.KNOWLEDGE_MAX_OUTPUT, 1024 * 1024)
        self.assertEqual(bounded.MAX_OUTPUT, 256 * 1024)
        payload = "x" * (512 * 1024 + 1)
        bounded.run(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
            payload,
            timeout=10,
            max_output=mindie_launch.KNOWLEDGE_MAX_OUTPUT,
        )
        with self.assertRaises(bounded.OutputLimitExceeded):
            bounded.run(
                [sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
                payload,
                timeout=10,
                max_output=512 * 1024,
            )


if __name__ == "__main__":
    unittest.main()
