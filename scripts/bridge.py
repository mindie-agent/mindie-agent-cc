#!/usr/bin/env python3
"""Claude plugin hooks: passive hints and opt-in Stop transcript capture.

[Role]
Implements Claude Code `PreToolUse`, `UserPromptSubmit`, and `Stop` hook handlers
inside the locked runtime.

[Invariants]
- `PreToolUse` and `UserPromptSubmit` are lightweight passive hooks: zero
  slash-command parsing, zero blocking `permissionDecision: "deny"` gates, and
  zero SQLite creation when unconfigured.
- `Stop` never exits 2 and never forces model continuation. It checks
  `mindie-consent.json` / community sharing consent and authorized `project_roots`
  BEFORE opening `admission.sqlite3` or spawning capture workers, exiting silently
  with zero SQLite files created when unconfigured, disabled, or out of scope.
- When sharing IS enabled and `cwd` is inside authorized `project_roots`, `Stop`
  auto-ensures the session lease (`not_before=0.0`) and hands off redacted capture.

[Timeouts & Locks]
- Runs within `mindie_launch.py` hook budgets (`HOOK_TOTAL = 10.0s`).

[Side-Effect Policy]
- Writes optional one-shot tool binds when configured and enqueues authorized
  redacted transcript captures.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

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
    if not raw or not raw.strip():
        return {}
    return parse_hook(raw)


def handle_prompt():
    """Passive UserPromptSubmit / legacy expansion hook: zero command parsing or SQLite I/O."""
    _print({})
    return 0


def handle_expansion():
    """Compatibility alias for passive prompt hook."""
    return handle_prompt()


def _record_pretool(exc):
    try:
        import diagnostic_support

        diagnostic_support.failure(
            "hook", stage="pretool_bind", category="local_storage",
            exception=exc, reportable=False,
        )
    except Exception:
        pass


def handle_pretool():
    try:
        if not config_path().is_file():
            _print({})
            return 0
        event = _read_event()
        name = event.get("tool_name")
        if not is_mindie_mcp_tool(name) or is_subagent(event):
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
        _record_pretool(exc)
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
        if not config_path().is_file():
            _print({})
            return 0
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
        import sharing as sharing_mod

        try:
            settings = sharing_mod.load()
        except TimeoutError:
            raise
        except Exception as exc:
            return _stop_failure("configuration", "unavailable", exc)
        if settings.state == "missing":
            _print({})
            return 0
        if not settings.allows_capture():
            consent_state = (settings.consent or {}).get("state")
            if settings.capture_block_kind() == "fault" and consent_state != "missing":
                return _stop_failure("authorization", "authority_unavailable")
            _print({})
            return 0
        for candidate_dir in (event.get("cwd"), event.get("workspace_root")):
            if isinstance(candidate_dir, str) and candidate_dir.strip():
                if not os.path.isabs(candidate_dir) or not settings.in_scope(candidate_dir):
                    _print({})
                    return 0

        session = require_session(event.get("session_id"))
        turn = event.get("prompt_id")
        if not isinstance(turn, str) or not turn or len(turn) > 256:
            return _stop_failure("identity", "missing_identity")
        transcript = event.get("transcript_path")
        if not isinstance(transcript, str) or not os.path.isabs(transcript):
            return _stop_failure("material", "no_capturable_material")

        from admission import ensure_default_lease, is_deactivated, knowledge_lease
        from paths import engine_config_path as engine_path
        from transcript import capture_source

        if is_deactivated(session):
            _print({})
            return 0
        existing = knowledge_lease(session)
        pinned = existing if (existing is not None and existing.get("activated_at") != 0.0) else None
        try:
            source = capture_source(transcript, session, binding=pinned)
        except TimeoutError:
            raise
        except (OSError, ValueError) as exc:
            return _stop_failure("material", "source_unavailable", exc)
        if not settings.in_scope(source["project_root"]):
            _print({})
            return 0

        lease = pinned or ensure_default_lease(
            session,
            project_root=source["project_root"],
            not_before=max(0.0, float(source["created_at"])),
        )
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
        from mindie_knowledge.loop.cli import capture_hook, config_at
        from mindie_knowledge.loop.handoff import _store_path, prepare_schema

        eng_file = str(engine_path())
        if not _store_path(config_at(eng_file)).exists():
            prepare_schema(eng_file)
        failed = _observe_stop(capture_hook(eng_file, forwarded))
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
    if op in {"expansion", "prompt", "user-prompt-submit"}:
        raise SystemExit(handle_prompt())
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
