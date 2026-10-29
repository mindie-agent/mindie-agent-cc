#!/usr/bin/env python3
"""Claude plugin hooks. Activation is UserPromptExpansion, not MCP.

[Role]
Implements Claude Code `PreToolUse` and `Stop` hook handlers inside the locked
runtime.

[Invariants]
- `PreToolUse` records a one-shot `claudecode/toolUseId` bind for MindIE MCP
  tools and leaves non-MindIE tools untouched.
- `Stop` never exits 2 and never forces model continuation; default-off sharing
  returns `{}` before touching capture state.
- Only an explicitly activated session can hand off Stop capture, additionally
  bounded by contribution consent and project scope; passive sessions stay silent.

[Timeouts & Locks]
- Runs within `mindie_launch.py` hook budgets (`HOOK_TOTAL = 10.0s`) and uses
  SQLite WAL one-shot bind storage.

[Side-Effect Policy]
- Writes one-shot tool binds and enqueues redacted transcript captures; reports
  warnings via `systemMessage` without breaking the host turn.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from identity import (
    is_mindie_mcp_tool,
    is_subagent,
    parse_hook,
    publish_bind,
    require_session,
    session_from_hook,
)
from paths import config_path



def _print(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def _read_event():
    raw = sys.stdin.buffer.read()
    return parse_hook(raw)


def handle_expansion():
    try:
        event = _read_event()
        from entry import dispatch_event, render_context

        payload = dispatch_event(event)
        text = render_context(payload)
        _print(
            {
                "additionalContext": text,
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptExpansion",
                    "additionalContext": text,
                },
            }
        )
        return 0
    except FileNotFoundError as exc:
        text = (
            f"{exc}. This hook does not install packages. "
            "Start a new Claude session with the MindIE native plugin enabled."
        )
        _print(
            {
                "additionalContext": text,
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptExpansion",
                    "additionalContext": text,
                },
            }
        )
        return 0
    except Exception as exc:
        text = f"MindIE entry unavailable: {exc}. Continue independently."[:500]
        _print(
            {
                "additionalContext": text,
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptExpansion",
                    "additionalContext": text,
                },
            }
        )
        return 0


def handle_pretool():
    try:
        event = _read_event()
        name = event.get("tool_name")
        if not is_mindie_mcp_tool(name):
            _print({})
            return 0
        if is_subagent(event):
            _print({})
            return 0
        tool_input = event.get("tool_input") or {}
        session = session_from_hook(event)
        publish_bind(
            session,
            event.get("tool_use_id"),
            name,
            tool_input,
            cwd=event.get("cwd"),
            transcript_path=event.get("transcript_path"),
        )
    except Exception as exc:
        print(f"MindIE PreToolUse bind failed: {type(exc).__name__}", file=sys.stderr)
    _print({})
    return 0


def _record_stop(stage, category, exc=None):
    """Local diagnostic only. No transcript, token, or exception text."""
    try:
        import diagnostic_support

        diagnostic_support.failure(
            "capture.stop", stage=stage, category=category,
            exception=exc, reportable=False,
        )
    except Exception:
        pass


def _observe_stop(result):
    if not isinstance(result, dict):
        _record_stop("handoff", "internal")
        return True
    stage = result.get("stage")
    if stage in {"inert", "organized", "no-new-material", "no-shareable-material",
                 "cancelled", "discarded", "processing", "accepted-runtime",
                 "apply-pending", "accepted-local"}:
        if result.get("wake") == "failed" or result.get("reason") == "worker-dead":
            _record_stop("wake", "unavailable")
            return True
        return False
    if stage not in {"unavailable", "rejected", "failed"}:
        _record_stop("handoff", "internal")
        return True
    reason = result.get("reason")
    if not isinstance(reason, str) or not reason.replace("-", "").replace("_", "").isalnum():
        reason = "handoff"
    if not reason[:1].isalpha():
        reason = "handoff"
    _record_stop(stage, reason)
    return True


def _stop_output(failed=False):
    if failed:
        _print({
            "systemMessage": "MindIE background capture encountered an error. "
                             "Task completion is unaffected; details are in MindIE diagnostics."
        })
    else:
        _print({})
    return 0


def _stop_failure(stage, category, exc=None):
    _record_stop(stage, category, exc)
    return _stop_output(failed=True)


def handle_stop():
    attempted = False
    failed = False
    try:
        event = _read_event()
        if event.get("hook_event_name") not in {None, "Stop"}:
            _print({})
            return 0
        if event.get("stop_hook_active") not in {None, False}:
            _print({})
            return 0
        if is_subagent(event):
            _print({})
            return 0
        if not config_path().is_file():
            _print({})
            return 0
        import sharing as sharing_mod
        from paths import engine_config_path as engine_path

        session = require_session(event.get("session_id"))
        from admission import gate
        admission = gate()
        lease = admission.knowledge_lease(session)
        if lease is None:
            _print({})
            return 0
        try:
            settings = sharing_mod.load()
        except TimeoutError:
            raise
        except Exception as exc:
            return _stop_failure("configuration", "unavailable", exc)
        if not settings.allows_capture():
            if settings.capture_block_kind() == "fault":
                return _stop_failure("authorization", "authority_unavailable")
            _print({})
            return 0
        turn = event.get("prompt_id")
        if not isinstance(turn, str) or not turn or len(turn) > 256:
            return _stop_failure("identity", "missing_identity")
        transcript = event.get("transcript_path")
        if not isinstance(transcript, str) or not os.path.isabs(transcript):
            return _stop_failure("material", "no_capturable_material")
        from transcript import capture_source

        try:
            source = capture_source(transcript, session, binding=lease)
        except TimeoutError:
            raise
        except (OSError, ValueError) as exc:
            return _stop_failure("material", "source_unavailable", exc)
        if not settings.in_scope(source["project_root"]):
            _print({})
            return 0
        attempted = True
        forwarded = dict(
            hook_event_name="Stop",
            identity_kind="turn",
            session_id=session,
            turn_id=turn,
            mindie_activation=lease.get("token"),
            harness="claude",
            transcript_path=source["transcript_path"],
        )
        from mindie_knowledge.loop.cli import capture_hook

        failed = _observe_stop(capture_hook(str(engine_path()), forwarded))
    except TimeoutError as exc:
        return _stop_failure("handoff" if attempted else "configuration", "timeout", exc)
    except Exception as exc:
        return _stop_failure("handoff" if attempted else "configuration", "internal", exc)
    return _stop_output(failed)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        raise SystemExit("bridge operation required")
    op = argv[0]
    if op == "expansion":
        raise SystemExit(handle_expansion())
    if op == "pretool":
        raise SystemExit(handle_pretool())
    if op == "stop":
        raise SystemExit(handle_stop())
    _print({})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
