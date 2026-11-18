#!/usr/bin/env python3
"""Claude Code public-record parser (session JSONL), adapter-owned.

[Role] Exposes `FileIdentity`, `identify`, `to_seconds`, `native_config_dir`, `capture_path`, `capture_source`, and `read_material` for capture.
[Invariants] Own first `queue-operation` `enqueue` is the creation boundary; sidechains, meta, thinking, and `<command-...>` blocks are excluded; `read_material` consumes whole records with `[start, end)` SHA-256 accounting.
[Timeouts & Locks] Bounded head reads and a `2.0s` scan budget; no locks.
[Side-Effect Policy] Strictly read-only.
"""
import hashlib
import json
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

MAX_RECORDS, ANCHOR_BYTES, MS_THRESHOLD = 200, 512, 1e12
SOURCE_HEAD_BYTES, SOURCE_HEAD_LINES = 1024 * 1024, 64
PUBLIC_TYPES = {"user", "assistant"}
SKIP_TYPES = {"attachment", "system", "queue-operation", "atis-latch", "last-prompt"}
SKIP_BLOCKS = {"thinking", "redacted_thinking"}
COMMAND_BLOCK = re.compile(r"<command-[a-zA-Z0-9_-]+>.*?</command-[a-zA-Z0-9_-]+>", re.DOTALL)
SESSION_FILE = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl\Z")

def _loads(raw):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None

def _config_file() -> Path:
    explicit = os.environ.get("MINDIE_CC_CONFIG") or os.environ.get("MINDIE_AGENT_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("MINDIE_CONFIG_HOME") or Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "mindie-agent")
    else:
        base = Path(os.environ.get("MINDIE_CONFIG_HOME") or Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "mindie-agent")
    return base / "cc.json"

def native_config_dir() -> Path:
    """Selected native profile directory (adapter config, env, or default)."""
    path, selected = _config_file(), None
    if path.is_file():
        try:
            doc = json.loads(path.read_text(encoding="utf-8-sig"))
        except TimeoutError:
            raise
        except (OSError, ValueError):
            doc = None
        selected = doc.get("claude_config_dir") if isinstance(doc, dict) else None
    if selected is not None and (not isinstance(selected, str) or not os.path.isabs(selected)):
        raise ValueError("selected native profile must be an absolute directory")
    return Path(selected or os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")

def capture_path(path, session_id, *, must_exist: bool = True) -> Path:
    """Validate one exact native artifact, including a not-yet-written fork."""
    if not isinstance(session_id, str) or not SESSION_FILE.fullmatch(session_id + ".jsonl"):
        raise ValueError("native session identity is missing")
    if not isinstance(path, (str, Path)) or not Path(path).is_absolute():
        raise ValueError("capture transcript must be absolute")
    try:
        selected = Path(path).resolve(strict=must_exist)
    except TimeoutError:
        raise
    except (OSError, RuntimeError) as exc:
        raise ValueError("capture transcript is unavailable") from exc
    if not selected.is_relative_to((native_config_dir() / "projects").resolve()):
        raise ValueError("capture transcript is outside the active native profile")
    if selected.name != session_id + ".jsonl":
        raise ValueError("capture transcript does not name this task")
    return selected

def _peek_enqueue_boundary(stream, expected_session):
    """Return this task's initial `enqueue` timestamp when present at the head."""
    position = stream.tell()
    try:
        stream.seek(0)
        head = stream.read(min(SOURCE_HEAD_BYTES, 65536))
    finally:
        stream.seek(position)
    for raw in head.split(b"\n")[:SOURCE_HEAD_LINES]:
        record = _loads(raw)
        if record is None:
            continue
        rec_session = record.get("sessionId") or record.get("session_id")
        if rec_session is None:
            continue
        if expected_session and rec_session != expected_session:
            return None
        if record.get("type") == "queue-operation" and record.get("operation") == "enqueue":
            stamp = to_seconds(record.get("timestamp"))
            return stamp if stamp is not None and math.isfinite(stamp) else None
        if to_seconds(record.get("timestamp")) is not None:
            return None
    return None

def capture_source(path, session_id, *, binding=None):
    """Read bounded native attribution, never inherited fork scope."""
    selected = capture_path(path, session_id)
    if not selected.is_file():
        raise ValueError("capture source must be a regular transcript file")
    with selected.open("rb") as stream:
        lines = stream.read(SOURCE_HEAD_BYTES).split(b"\n")[:SOURCE_HEAD_LINES]
    created = None
    for raw in lines:
        record = _loads(raw)
        if record is None:
            continue
        rec_session = record.get("sessionId") or record.get("session_id")
        if rec_session is None:
            continue
        if rec_session != session_id:
            raise ValueError("capture transcript belongs to another task")
        if created is None:
            if record.get("type") != "queue-operation":
                if to_seconds(record.get("timestamp")) is not None:
                    raise ValueError("native creation queue must precede dated history")
                continue
            if record.get("operation") != "enqueue":
                raise ValueError("native task creation queue is invalid")
            created = to_seconds(record.get("timestamp"))
            if created is None or not math.isfinite(created):
                raise ValueError("native task creation boundary is unavailable")
            if binding is not None:
                scope, boundary = binding.get("project_root"), binding.get("activated_at")
                if (binding.get("session") != session_id or binding.get("enabled") != 1
                        or not isinstance(scope, str) or not os.path.isabs(scope)
                        or type(boundary) not in (int, float) or not math.isfinite(boundary) or boundary < 0):
                    raise ValueError("native task binding is invalid")
                return dict(session_id=session_id, project_root=scope, created_at=created, transcript_path=str(selected))
            continue
        cwd, stamp = record.get("cwd"), to_seconds(record.get("timestamp"))
        if isinstance(cwd, str) and os.path.isabs(cwd) and stamp is not None and math.isfinite(stamp) and stamp >= created:
            return dict(session_id=session_id, project_root=str(Path(cwd).resolve()), created_at=created, transcript_path=str(selected))
    raise ValueError("native task scope or creation boundary is unavailable")

@dataclass(frozen=True)
class FileIdentity:
    path: str
    dev: int
    ino: int
    size: int
    mtime_ns: int
    anchor_len: int = 0
    anchor_digest: str = ""

    @property
    def key(self) -> str:
        return f"{self.path}|{self.dev}:{self.ino}"

    def serialize(self) -> str:
        return json.dumps(dict(dev=self.dev, ino=self.ino, anchor_len=self.anchor_len,
                               anchor_digest=self.anchor_digest), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def unserialize(text, path):
        try:
            data = json.loads(text)
            if (type(data["anchor_len"]) is not int or not 0 < data["anchor_len"] <= ANCHOR_BYTES
                    or not isinstance(data["anchor_digest"], str) or len(data["anchor_digest"]) != 64):
                return None
            return FileIdentity(path, int(data["dev"]), int(data["ino"]), 0, 0,
                                data["anchor_len"], data["anchor_digest"])
        except (ValueError, KeyError, TypeError):
            return None

def identify(path) -> FileIdentity | None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return None
    try:
        stat, anchor = os.fstat(fd), os.read(fd, ANCHOR_BYTES)
    except TimeoutError:
        raise
    except OSError:
        return None
    finally:
        os.close(fd)
    return FileIdentity(str(Path(path).resolve()), stat.st_dev, stat.st_ino, stat.st_size,
                        stat.st_mtime_ns, len(anchor), hashlib.sha256(anchor).hexdigest())

def to_seconds(raw):
    if type(raw) in (int, float) and raw > 0:
        value = float(raw)
        return value / 1000.0 if value >= MS_THRESHOLD else value
    if not isinstance(raw, str) or not raw:
        return None
    text = raw.strip()
    text = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp()

def _session_in_path(path) -> str | None:
    match = SESSION_FILE.search(Path(path).name)
    return match.group(1) if match else None

def _extract(record):
    rtype = record.get("type")
    if rtype not in PUBLIC_TYPES or record.get("isSidechain") is True or record.get("isMeta") is True:
        return None
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else record.get("content")
    kind = "user" if rtype == "user" else "assistant"
    if isinstance(content, str):
        text = COMMAND_BLOCK.sub("", content).strip()
        return (kind, text) if text else None
    if not isinstance(content, list):
        return None
    chunks = [text for block in content if isinstance(block, dict) and block.get("type") == "text"
              and isinstance(block.get("text"), str) and (text := COMMAND_BLOCK.sub("", block["text"]).strip())]
    return (kind, "\n".join(chunks)) if chunks else None

def _allowed_roots() -> list[str] | None:
    cfg = _config_file()
    comm_p, cons_p = cfg.parent / "mindie-community.json", cfg.parent / "mindie-consent.json"
    if not comm_p.is_file() and not cons_p.is_file():
        return None
    try:
        comm = json.loads(comm_p.read_text(encoding="utf-8-sig")) if comm_p.is_file() else None
        cons = json.loads(cons_p.read_text(encoding="utf-8-sig")) if cons_p.is_file() else None
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return []
    raw = (comm or {}).get("project_roots") if isinstance(comm, dict) and isinstance(comm.get("project_roots"), list) else (cons or {}).get("project_roots") if isinstance(cons, dict) else None
    return [os.path.normcase(os.path.abspath(r)) for r in raw if isinstance(r, str) and os.path.isabs(r)] if isinstance(raw, list) else None

def _cwd_allowed(cwd: object, roots: list[str] | None) -> bool:
    if roots is None or not isinstance(cwd, str) or not os.path.isabs(cwd):
        return True
    cand = os.path.normcase(os.path.abspath(cwd))
    return any(os.path.splitdrive(cand)[0] == os.path.splitdrive(r)[0] and os.path.commonpath([cand, r]) == r for r in roots)

def read_material(path, start, *, session_id=None, not_before=None, expected=None,
                  max_scan_bytes=16777216, max_seconds=2.0, max_text_bytes=49152, scan_until=None):
    if type(start) is not int or start < 0 or max_scan_bytes <= 0 or max_seconds <= 0 or max_text_bytes <= 0:
        raise ValueError("invalid read budget or start offset")
    boundary = 0.0 if (type(not_before) in (int, float) and math.isfinite(not_before) and not_before == 0) else (
        to_seconds(not_before) if not_before is not None else None)
    result = dict(status="ok", start=start, end=start, digest=hashlib.sha256(b"").hexdigest(), text="",
                  records=0, skipped_records=0, oversize_records=0, partial=False, more=False,
                  timestamps_reliable=True, session_match=None, coverage_note=None, coverage=[])
    consumed, recognized, included, text_size, begun, roots = hashlib.sha256(), 0, [], 0, time.monotonic(), _allowed_roots()
    try:
        with open(path, "rb") as stream:
            stat, anchor = os.fstat(stream.fileno()), stream.read(ANCHOR_BYTES)
            current = FileIdentity(str(Path(path).resolve()), stat.st_dev, stat.st_ino, stat.st_size,
                                   stat.st_mtime_ns, len(anchor), hashlib.sha256(anchor).hexdigest())
            result["identity"], result["snapshot_size"] = current.serialize(), stat.st_size
            if expected is not None and (expected.path != current.path or expected.dev != stat.st_dev
                                         or expected.ino != stat.st_ino or not expected.anchor_len
                                         or hashlib.sha256(anchor[:expected.anchor_len]).hexdigest() != expected.anchor_digest):
                result.update(status="replaced", coverage_note="transcript changed before read")
                return result
            if stat.st_size < start:
                result.update(status="replaced", coverage_note="transcript truncated")
                return result
            owner = _session_in_path(path)
            if owner and session_id and owner != session_id:
                result.update(status="wrong-task", coverage_note="transcript belongs to another task")
                return result
            result["session_match"] = (owner == session_id) if owner else None
            if session_id and not owner:
                result.update(status="unknown-format", coverage_note="task path identity unavailable; no public read")
                return result
            if boundary is not None:
                floor = _peek_enqueue_boundary(stream, session_id or owner)
                if floor is not None and floor > boundary:
                    boundary = floor
            stream.seek(max(0, start - 1))
            if start > 0 and stream.read(1) != b"\n":
                result.update(status="invalid-boundary", coverage_note="cursor is not a whole-record boundary")
                return result
            stream.seek(start)
            limit = min(stat.st_size, scan_until) if scan_until is not None else stat.st_size
            end_limit = min(limit, start + max_scan_bytes)
            while stream.tell() < end_limit and time.monotonic() - begun < max_seconds:
                offset = stream.tell()
                raw = stream.readline(min(end_limit - offset, max_text_bytes * 4 + 65536))
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    result["partial"] = offset + len(raw) == stat.st_size
                    break
                record = _loads(raw)
                if record is None:
                    result.setdefault("discarded_records", []).append(dict(start=offset, end=stream.tell(), reason="invalid record"))
                    consumed.update(raw); result["end"] = stream.tell(); result["skipped_records"] += 1
                    continue
                if record.get("type") in PUBLIC_TYPES or record.get("type") in SKIP_TYPES:
                    recognized += 1
                stamp = to_seconds(record.get("timestamp"))
                rec_session = record.get("sessionId") or record.get("session_id")
                extracted = None if ((session_id and rec_session and rec_session != session_id) or not _cwd_allowed(record.get("cwd"), roots)) else _extract(record)
                if extracted and extracted[1]:
                    if boundary is not None and stamp is None:
                        result.setdefault("discarded_records", []).append(dict(start=offset, end=stream.tell(), reason="missing public timestamp"))
                        consumed.update(raw); result["end"] = stream.tell(); result["skipped_records"] += 1
                        continue
                    if boundary is not None and stamp < boundary:
                        extracted = None
                    else:
                        kind, text = extracted[0], extracted[1].replace("\r\n", "\n").replace("\r", "\n")
                        rendered = f"### {kind}\n{text}"
                        size = len(rendered.encode()) + 2
                        if size > max_text_bytes:
                            if included:
                                break
                            result["oversize_records"] += 1; extracted = None
                        elif text_size + size > max_text_bytes or len(included) >= MAX_RECORDS:
                            break
                        else:
                            included.append(rendered); text_size += size
                if not extracted or not extracted[1]:
                    result["skipped_records"] += 1
                consumed.update(raw); result["end"] = stream.tell()
            result["more"] = result["end"] < limit
    except TimeoutError:
        raise
    except OSError:
        result.update(status="missing", coverage_note="transcript unreadable")
        return result
    result.update(digest=consumed.hexdigest(), text="\n\n".join(included), records=len(included))
    if result["status"] != "ok":
        return result
    if result["end"] == start:
        result["status"] = "unchanged"
    elif not recognized and not (result.get("session_match") is True and result["more"] and result["end"] > start):
        result.update(status="unknown-format", text="", coverage_note="no recognized native Claude transcript signature")
    return result
