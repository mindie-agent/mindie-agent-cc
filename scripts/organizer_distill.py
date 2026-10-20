#!/usr/bin/env python3
"""C6 distillation branch for the native organizer worker.

[Role]
Admits one explicitly prepared `mindie-distill-request/1`, asks the shared
`mindie_knowledge.graded.distill` module for the prompt, decision schema and
validation, and emits the existing `mindie-summary-outcome/1` envelope whose
result is a C6 `DistillationDecision`.

[Invariants]
- Stop is not completion: only the request's explicit `task_state` and evidence
  outcomes are trusted. In-flight or unverified material returns a shared noop
  decision without a paid model call.
- The selected sanitized inputs are the complete read scope. This branch never
  scans a ledger, transcript or filesystem material and keeps no curation state.
- Prompt, decision schema and decision validation stay in the shared runtime;
  this module only bridges them to the native invocation and its receipt.
- A returned decision is a local result, never publication authorization. The
  shared engine owns local installation and any separate contribution plan.

[Timeouts & Locks]
- One native invocation within the parent's summary budget; the shared bounded
  runner owns cancellation, process-tree cleanup and output byte limits.

[Side-Effect Policy]
- Writes only the outcome envelope to stdout; never writes knowledge, Git
  state, GitHub state, credentials or raw model text.
"""
from __future__ import annotations

import time

REQUEST_SCHEMA = "mindie-distill-request/1"


class DistillUnavailable(ImportError):
    """The installed shared runtime does not expose the matching C6 API."""

    reason = "dependency_unavailable"


class RequestInvalid(ValueError):
    """A C6 request lacks the explicit state the paid boundary depends on."""

    reason = "request_invalid"


def _shared_api():
    """Import the landed shared C6 API only when this branch is selected."""
    try:
        from mindie_knowledge.graded import distill
    except ImportError as exc:
        raise DistillUnavailable("shared C6 distillation API is unavailable") from exc
    required = ("validate_request", "with_worker_identity", "value_gate",
                "noop_decision", "validate_decision", "build_prompt",
                "decision_schema", "parse_validate_result")
    if any(not callable(getattr(distill, name, None)) for name in required):
        raise DistillUnavailable("shared C6 distillation API is incomplete")
    return distill


def _reason(error, receipt):
    """Reuse the organizer's categories with request faults kept distinct."""
    import organizer

    if isinstance(error, ValueError) and not receipt.get("native_started"):
        return "request_invalid"
    return organizer._reason(error, receipt)


def run(request):
    """Execute one prepared C6 request without discovering any new material."""
    import organizer
    from mindie_knowledge.materials import summarizer

    distill = _shared_api()
    if request.get("task_state") not in distill.TASK_STATES:
        raise RequestInvalid("C6 request must declare an explicit task state")
    started = time.monotonic()
    receipt = organizer.new_receipt()
    # Configuration and request identity faults happen before any paid call and
    # therefore cannot be represented by a worker outcome for an unverified
    # request; they propagate to the worker's protocol exit path.
    env = organizer.native_environment()
    model = organizer.native_model(env)
    current_identity = organizer.identity(env)
    if "policy_identity" in request and request["policy_identity"] != current_identity:
        # Mirror the legacy worker boundary: policy drift is known before a
        # paid call and is reported against the caller's own request.
        if not all(key in request for key in ("task_id", "batch_id", "body_version", "input_digest")):
            raise organizer.ConfigurationError("distill worker policy changed")
        return summarizer.outcome(
            request, status="failed", error="configuration",
            error_reason="worker_policy_changed", model_calls=0,
            elapsed_ms=round((time.monotonic() - started) * 1000))
    worker_request = distill.with_worker_identity(request, current_identity)
    distill.validate_request(worker_request)
    # The shared outcome validator can recheck the returned decision when the
    # sanitized scope travels with the worker receipt.
    worker_request["sanitized_inputs"] = request["sanitized_inputs"]

    result, error, error_reason, status = None, None, None, "returned"
    try:
        gate = distill.value_gate(request)
        if gate is not None:
            result = distill.validate_decision(
                distill.noop_decision(request, gate), request)
        else:
            prompt = distill.build_prompt(request)
            schema = distill.decision_schema()
            organizer._invoke_native(prompt, schema, env, model, receipt)
            if not receipt["turn_completed"]:
                raise organizer.NativeFailure("native execution did not report completion")
            raw = receipt.get("final_message")
            if not isinstance(raw, str) or not raw:
                raise organizer.InvalidResultError("completed C6 decision is unavailable")
            try:
                result = distill.parse_validate_result(raw, request)
            except (ValueError, TypeError) as exc:
                raise organizer.InvalidResultError(
                    "C6 decision violates the prepared request") from exc
    except Exception as exc:
        error = organizer._category(exc)
        error_reason = _reason(exc, receipt)
        # Same transport boundary as summary: an interrupted paid call without
        # terminal evidence is unknown, never a free retry or a silent noop.
        status = ("outcome_unknown" if receipt["native_started"]
                  and not receipt["turn_completed"]
                  and not receipt.get("turn_failed") else "failed")
        result = None
    usage = receipt.get("usage")
    known = bool(isinstance(usage, dict)
                 and all(type(usage.get(key)) is int and usage[key] >= 0
                         for key in summarizer.COUNTERS))
    return summarizer.outcome(
        worker_request, status=status, result=result,
        raw_result=receipt.get("failure_diagnostic"), usage=usage,
        model_calls=int(bool(receipt.get("native_started"))), usage_known=known,
        elapsed_ms=round((time.monotonic() - started) * 1000),
        error=error, error_reason=error_reason,
        cleanup_failed=bool(receipt.get("cleanup_failed", False)))
