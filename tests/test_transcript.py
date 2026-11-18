"""Claude Code transcript identity, fork boundary, and public-read tests.

[Role]
Verifies `FileIdentity`/`identify`, profile-bound `capture_path`, the fork
`queue-operation` `enqueue` creation boundary, and public-record filtering in
`read_material`.
[Invariants]
- Transcripts are written only under the isolated `CLAUDE_CONFIG_DIR/projects`.
- Byte accounting is checked against the exact `[start, end)` prefix.
[Timeouts & Locks]
- Pure filesystem reads; no locks.
[Side-Effect Policy]
- Writes only inside the isolated `tmp_path`.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

SESSION = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SESSION_FILE = SESSION + ".jsonl"


def _projects() -> Path:
    root = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write(records: list[dict]) -> Path:
    path = _projects() / SESSION_FILE
    path.write_bytes(b"".join(json.dumps(record).encode("utf-8") + b"\n" for record in records))
    return path


def _fork_prefix() -> list[dict]:
    return [
        {"type": "queue-operation", "operation": "enqueue", "sessionId": SESSION,
         "timestamp": "2026-01-01T00:00:00Z"},
        {"type": "user", "sessionId": SESSION, "timestamp": "2026-01-01T00:00:01Z",
         "cwd": "/work/app", "message": {"content": "hello"}},
    ]


def test_identify_and_capture_path_are_profile_bound() -> None:
    import transcript

    path = _write(_fork_prefix())
    identity = transcript.identify(path)
    assert isinstance(identity, transcript.FileIdentity)
    assert identity is not None
    assert identity.path == str(path.resolve())
    assert identity.size == path.stat().st_size
    assert len(identity.anchor_digest) == 64
    assert transcript.capture_path(str(path), SESSION) == path.resolve()
    with pytest.raises(ValueError):
        transcript.capture_path(str(path), "bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
    with pytest.raises(ValueError):
        transcript.capture_path(str(Path(os.environ["CLAUDE_CONFIG_DIR"]) / SESSION_FILE), SESSION)


def test_capture_source_uses_fork_enqueue_boundary() -> None:
    import transcript

    records = _fork_prefix() + [
        {"type": "assistant", "sessionId": SESSION, "timestamp": "2026-01-01T00:00:02Z",
         "cwd": "/work/app", "message": {"content": [{"type": "text", "text": "done"}]}},
    ]
    path = _write(records)
    source = transcript.capture_source(str(path), SESSION)
    assert source["session_id"] == SESSION
    assert source["project_root"] == str(Path("/work/app").resolve())
    assert source["created_at"] == 1767225600.0
    binding = {"session": SESSION, "enabled": 1, "project_root": "/work/app",
               "activated_at": 99.0}
    assert transcript.capture_source(str(path), SESSION, binding=binding)["created_at"] == 1767225600.0
    with pytest.raises(ValueError):
        transcript.capture_source(str(path), SESSION, binding={**binding, "enabled": 0})


def test_read_material_excludes_sidechains_thinking_and_commands() -> None:
    import transcript

    records = _fork_prefix() + [
        {"type": "assistant", "sessionId": SESSION, "timestamp": "2026-01-01T00:00:02Z",
         "message": {"content": [{"type": "thinking", "thinking": "HIDDEN"},
                                 {"type": "text", "text": "world"}]}},
        {"type": "assistant", "sessionId": SESSION, "isSidechain": True,
         "timestamp": "2026-01-01T00:00:03Z", "message": {"content": [{"type": "text", "text": "SIDECHAIN"}]}},
        {"type": "user", "sessionId": SESSION, "timestamp": "2026-01-01T00:00:04Z",
         "message": {"content": "<command-foo>rm -rf /</command-foo>"}},
        {"type": "user", "sessionId": SESSION, "timestamp": "2026-01-01T00:00:05Z",
         "message": {"content": "<command-foo>ls</command-foo> real text"}},
    ]
    path = _write(records)
    payload = path.read_bytes()
    result = transcript.read_material(str(path), 0, session_id=SESSION)
    assert result["status"] == "ok"
    assert result["start"] == 0
    assert result["end"] == len(payload)
    assert result["digest"] == hashlib.sha256(payload[: result["end"]]).hexdigest()
    assert "hello" in result["text"] and "world" in result["text"] and "real text" in result["text"]
    for hidden in ("HIDDEN", "SIDECHAIN", "<command-"):
        assert hidden not in result["text"]
    assert result["records"] == 3

    offset = len(payload.splitlines(keepends=True)[0])
    tail = transcript.read_material(str(path), offset, session_id=SESSION)
    assert tail["start"] == offset
    assert tail["end"] == len(payload)
    assert tail["digest"] == hashlib.sha256(payload[offset:]).hexdigest()
    snap = transcript.read_material(str(path), 0, session_id=SESSION, scan_until=offset)
    assert snap["end"] == offset and snap["more"] is False
    ident = transcript.identify(str(path))
    path.write_bytes(payload.replace(b"hello", b"HELLO"))
    assert transcript.read_material(str(path), offset, session_id=SESSION, expected=ident)["status"] == "replaced"

    assert transcript.to_seconds("2026-01-01T00:00:00Z") == 1767225600.0
    assert transcript.to_seconds("2026-01-01T00:00:00.000Z") == 1767225600.0
    assert transcript.to_seconds(1767225600000) == 1767225600.0
    assert transcript.to_seconds("not-a-date") is None
