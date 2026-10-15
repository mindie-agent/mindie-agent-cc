"""Knowledge refresh and diagnostic retention, independent of plugin updates."""

import json
from pathlib import Path
import sys

from bounded import run

_FEED_OK = frozenset({"synced", "unchanged"})
_FEED_PENDING = frozenset({"busy", "deferred"})
_FEED_ERROR = frozenset({"unavailable", "invalid", "exhausted", "partial"})
_FEED_KNOWN = _FEED_OK | _FEED_PENDING | _FEED_ERROR


def fold_feed_results(output):
    """Fold core `sync` stdout, which MUST be one JSON list (no prefix/suffix).

    Empty list is ok. busy/deferred means not completed now, not a failed
    attempt: all-pending folds to deferred. Mix of completed (synced/unchanged)
    with pending or failed folds to degraded. A committed snapshot whose
    cleanup/receipt failed, or partially promoted metadata, also stays degraded:
    its known effect must not be confused with an unstarted operation.
    Every original row is preserved. Unknown status or malformed stdout raises.
    """
    text = (output or "").strip()
    if not text:
        raise ValueError("knowledge sync returned empty output")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("knowledge sync returned non-JSON") from exc
    if not isinstance(payload, list):
        raise ValueError("knowledge sync result is not a list")
    rows = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("knowledge sync row is not an object")
        status = item.get("status")
        repo = item.get("repository")
        if not isinstance(status, str) or not status:
            raise ValueError("knowledge sync row missing status")
        if not isinstance(repo, str) or not repo:
            raise ValueError("knowledge sync row missing repository")
        if status not in _FEED_KNOWN:
            raise ValueError("knowledge sync unknown status: " + status)
        for field in ("cleanup_status", "receipt_status"):
            if field in item and item[field] != "failed":
                raise ValueError("knowledge sync unknown " + field)
        if status == "partial" and item.get("metadata_committed") is not True:
            raise ValueError("partial knowledge sync lacks its committed metadata outcome")
        rows.append(item)
    good = sum(1 for row in rows if row["status"] in _FEED_OK and not _feed_late_failures(row))
    committed = any(row["status"] in _FEED_OK or row["status"] == "partial" for row in rows)
    pending = sum(1 for row in rows if row["status"] in _FEED_PENDING)
    failed = sum(1 for row in rows if row["status"] in _FEED_ERROR or _feed_late_failures(row))
    if not rows or good == len(rows):
        aggregate = "ok"
        summary = None
    elif committed and (pending or failed):
        aggregate = "degraded"
        summary = _feed_error_summary(rows)
    elif failed:
        aggregate = "sync_failed"
        summary = _feed_error_summary(rows)
    elif pending:
        aggregate = "deferred"
        summary = _feed_error_summary(rows)
    else:
        aggregate = "sync_failed"
        summary = _feed_error_summary(rows)
    return aggregate, rows, summary


def _feed_late_failures(row):
    return [field for field in ("cleanup_status", "receipt_status") if row.get(field) == "failed"]


def _feed_error_summary(rows):
    parts = []
    for row in rows:
        late = _feed_late_failures(row)
        if row["status"] in _FEED_OK and not late:
            continue
        detail = row.get("detail") or row.get("cause") or ""
        observed = row["status"] + ("+" + "+".join(late) + "=failed" if late else "")
        parts.append(f"{row['repository']}:{observed}:{str(detail)[:80]}")
    return "; ".join(parts)[:240]


def sync_public_feed(python, engine_config):
    """Preserve core sync outcomes independently of process/cleanup failure."""
    receipt = {}
    try:
        output = run(
            [python, "-m", "mindie_knowledge.loop.cli", "sync",
             "--config", str(engine_config)],
            "",
            receipt=receipt,
        )
        aggregate, rows, summary = fold_feed_results(output)
        feed = dict(result=aggregate, results=rows)
        if summary:
            feed["error"] = summary
    except Exception as exc:
        feed = dict(result="sync_failed",
                    error=f"{type(exc).__name__}: {str(exc)[:200]}")
        output = getattr(exc, "mindie_output", getattr(exc, "stdout", ""))
        if output:
            try:
                _, rows, _ = fold_feed_results(output)
                feed["results"] = rows
            except ValueError:
                pass
    if receipt.get("cleanup_failed"):
        feed["cleanup_failed"] = True
        feed["cleanup"] = receipt["cleanup"]
        feed.setdefault(
            "error",
            "sync command completed but owned-process cleanup failed; "
            "inspect the completed feed results before repeating",
        )
    if receipt.get("cancelled_after_completion"):
        feed.update(operation_completed=True, cancelled_after_completion=True,
                    returncode=receipt.get("returncode"))
    return feed


def session_start():
    """Run session-triggered maintenance, with no plugin-version scheduler."""
    from diagnostic_support import reporting_config_path
    from install_state import atomic_write
    from paths import state_dir
    from paths import engine_config_path

    feed = sync_public_feed(sys.executable, engine_config_path())
    process = {}
    try:
        raw = run(
            [sys.executable, "-m", "mindie_diagnostics.cli", "reporting", "maintain",
             "--update-running", "--config", str(reporting_config_path())],
            check=False, receipt=process,
        )
        reporting = json.loads(raw)
        if not isinstance(reporting, dict):
            raise ValueError("diagnostic maintenance did not return an object")
        if process.get("returncode") != 0 or process.get("cleanup_failed"):
            reporting = dict(reporting, operation_failed=True, process=process)
    except Exception as exc:
        reporting = dict(status="unavailable", error_type=type(exc).__name__, process=process)
    ready = (feed["result"] == "ok" and not feed.get("error")
             and reporting.get("status") == "ok"
             and not reporting.get("operation_failed"))
    result = dict(status="ready" if ready else "incomplete", feed=feed, diagnostics=reporting)
    try:
        atomic_write(state_dir() / "session-maintenance.json", result)
    except OSError as exc:
        result.update(status="incomplete", recording_error=type(exc).__name__)
    return result


if __name__ == "__main__":
    print(json.dumps(session_start()))
