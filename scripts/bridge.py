#!/usr/bin/env python3
"""Claude plugin hooks. Activation is UserPromptExpansion, not MCP.

Stop never exits 2 and never continues the model. With contribution enabled
and the task's own transcript inside the configured scope, Stop automatically
associates the task (an internal binding, never a consent prompt) and calls
the shared capture handoff; an explicit deactivate is never undone here.
Default-off sharing returns {} before any capture state.
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
            "Run python3 scripts/setup.py from the plugin checkout."
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
    except Exception:
        pass
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
        return
    stage = result.get("stage")
    if stage in {"inert", "organized", "no-new-material", "no-shareable-material",
                 "cancelled", "discarded", "processing", "accepted-runtime",
                 "apply-pending", "accepted-local"}:
        return
    if stage not in {"unavailable", "rejected", "failed"}:
        _record_stop("handoff", "internal")
        return
    reason = result.get("reason")
    if not isinstance(reason, str) or not reason.replace("-", "").replace("_", "").isalnum():
        reason = "handoff"
    if not reason[:1].isalpha():
        reason = "handoff"
    _record_stop(stage, reason)


def handle_stop():
    attempted = False
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
        try:
            settings = sharing_mod.load()
        except Exception as exc:
            _record_stop("configuration", "unavailable", exc)
            _print({})
            return 0
        if not settings.allows_capture():
            if settings.capture_block_kind() == "fault":
                _record_stop("authorization", "authority_unavailable")
            _print({})
            return 0
        turn = event.get("prompt_id")
        if not isinstance(turn, str) or not turn or len(turn) > 256:
            _record_stop("identity", "missing_identity")
            _print({})
            return 0
        transcript = event.get("transcript_path")
        if not isinstance(transcript, str) or not os.path.isabs(transcript):
            _record_stop("material", "no_capturable_material")
            _print({})
            return 0
        # Scope and the capture boundary come from the task's own transcript
        # artifact, never from the hook arguments.
        from transcript import capture_source

        try:
            source = capture_source(transcript, session)
        except (OSError, ValueError) as exc:
            _record_stop("material", "source_unavailable", exc)
            _print({})
            return 0
        if not settings.in_scope(source["project_root"]):
            _print({})
            return 0
        from admission import associate, gate

        admission = gate()
        inspected = admission.inspect(session)
        if inspected.get("status") == "unavailable":
            _record_stop("admission", "unavailable")
            _print({})
            return 0
        if inspected.get("status") == "inactive":
            # An explicit deactivation is a revocation; an ordinary Stop never
            # reactivates the task.
            _print({})
            return 0
        attempted = True
        boundary = max(settings.enabled_at or 0, source["created_at"])
        lease = associate(
            session,
            project_root=source["project_root"],
            not_before=boundary,
        )
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

        _observe_stop(capture_hook(str(engine_path()), forwarded))
    except Exception as exc:
        if attempted:
            _record_stop("handoff", "internal", exc)
    _print({})
    return 0


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
