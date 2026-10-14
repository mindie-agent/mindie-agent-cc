import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import transcript  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "transcript.jsonl"
SESSION = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / f"{SESSION}.jsonl"
        shutil.copy(FIXTURE, self.path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_identify_and_public_records(self):
        ident = transcript.identify(self.path)
        self.assertIsNotNone(ident)
        result = transcript.read_material(str(self.path), 0, session_id=SESSION)
        self.assertEqual(result["status"], "ok")
        self.assertGreater(result["records"], 0)
        self.assertIn("No matching notes yet", result["text"])
        self.assertNotIn("knowledge_query", result["text"])
        self.assertNotIn("call_00_abc", result["text"])
        self.assertNotIn("omitted-placeholder", result["text"])
        self.assertNotIn("subagent hidden", result["text"])
        self.assertNotIn("Base directory for this skill", result["text"])
        self.assertNotIn("/mindie-agent:init", result["text"])

    def test_not_before_excludes_inherited(self):
        result = transcript.read_material(
            str(self.path), 0, session_id=SESSION, not_before=1e18
        )
        self.assertEqual(result["records"], 0)

    def test_wrong_session_file(self):
        other = self.tmp / "bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl"
        shutil.copy(self.path, other)
        result = transcript.read_material(str(other), 0, session_id=SESSION)
        self.assertEqual(result["status"], "wrong-task")

    def test_public_blocks_exclude_native_tool_payloads(self):
        public = "The echo request completed; its reported result still needs review."
        records = [
            {"type": "assistant", "sessionId": SESSION, "message": {"content": [
                {"type": "thinking", "thinking": "PRIVATE_THINKING", "signature": "PRIVATE_SIGNATURE"},
                {"type": "tool_use", "id": "PRIVATE_CALL", "name": "mcp__mindie_probe__echo",
                 "input": {"value": "PRIVATE_INPUT"}},
                {"type": "text", "text": public},
            ]}},
            {"type": "user", "sessionId": SESSION, "message": {"content": [
                {"type": "tool_result", "tool_use_id": "PRIVATE_CALL", "content": "PRIVATE_OUTPUT"},
            ]}},
        ]
        self.path.write_text(
            "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
        )
        result = transcript.read_material(str(self.path), 0, session_id=SESSION)
        self.assertEqual(result["status"], "ok")
        self.assertIn(public, result["text"])
        self.assertNotIn("PRIVATE_", result["text"])
        self.assertNotIn("mcp__mindie_probe__echo", result["text"])

    def test_established_session_file_keeps_an_unrecognized_page(self):
        path = self.tmp / f"{SESSION}.jsonl"
        path.write_text("\n".join('{"type":"noise","n":%d}' % i for i in range(20)) + "\n", encoding="utf-8")
        with path.open("a", encoding="utf-8") as stream:
            stream.write('{"type":"user","sessionId":"%s","message":{"role":"user","content":"later public"}}\n' % SESSION)
        identity = transcript.identify(str(path))
        result = transcript.read_material(
            str(path), 0, session_id=SESSION, expected=identity, max_scan_bytes=1024,
        )
        self.assertNotEqual(result["status"], "unknown-format")
        self.assertTrue(result["more"] or "later public" in result["text"])

    def test_partial_line_does_not_consume(self):
        with self.path.open("ab") as stream:
            stream.write(b'{"type":"assistant","sessionId":"' + SESSION.encode())
        size = self.path.stat().st_size
        result = transcript.read_material(str(self.path), 0, session_id=SESSION)
        self.assertTrue(result["partial"] or result["end"] < size)


if __name__ == "__main__":
    unittest.main()
