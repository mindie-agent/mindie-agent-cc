#!/usr/bin/env python3
"""Native Claude summary worker for the shared block-indexing contract.

[Role]
Executes a single isolated `claude` CLI invocation to summarize redacted
transcript blocks for `mindie_knowledge` indexing, emitting one
`mindie-summary-outcome/1` JSON envelope line on stdout.

[Invariants]
- Exit 0 indicates the outcome envelope was delivered on stdout; never retries
  uncertain/billed model calls or substitutes fallback summaries.
- Runs `claude` in an isolated workdir with no business tools, hooks, plugins,
  or MCP; provider and model come strictly from the selected native profile.
- Only explicit versioned requests are admitted: the existing summary schema
  keeps its path, a C6 request delegates prompt/schema/decision validation to
  shared `graded.distill`, and an unknown schema fails instead of downgrading.
- The C6 path consumes only supplied sanitized inputs, never scans a ledger or
  filesystem, never installs/publishes locally and keeps no second task state.

[Timeouts & Locks]
- Native execution uses the parent's remaining budget with a cleanup reserve;
  `bounded.py` also observes cancellation, owner death and output byte bounds.

[Side-Effect Policy]
- Never stores raw source text; writes only the structured summary outcome
  envelope to stdout.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import bounded
from bounded import OutputLimitExceeded, OwnerExited
import paths
from mindie_knowledge.loop.process import AGENT_ERROR_EXIT_CODES
from mindie_knowledge.loop.summary_budget import native_timeout
from mindie_knowledge.loop.summary_scratch import (
    ENVIRONMENT_KEY as SUMMARY_SCRATCH_ENV,
    ScratchOwnershipError,
    create_profile,
)
from mindie_knowledge.materials import summarizer

MAX_REQUEST_BYTES = 128 * 1024
SUMMARY_EFFORT = "low"
# Native corrections include multiple submission/result events. This bounds
# transport, independently of the final metadata's MAX_RESPONSE_BYTES limit.
# The observed five-attempt native stream fits well below this envelope.
MAX_NATIVE_STREAM_BYTES = 1024 * 1024

# C6 requests carry explicitly selected sanitized evidence, so they use a wider
# wire budget than legacy summary batches. The branch API is imported lazily.
MAX_DISTILL_REQUEST_BYTES = 512 * 1024

# Native 2.1.269 result/terminal enums. Unknown values never become log text.
NATIVE_FAILURE_REASONS = {
    "error_max_turns": "native_max_turns",
    "error_max_budget_usd": "native_budget_exhausted",
    "error_max_structured_output_retries": "native_structured_output_exhausted",
}
NATIVE_RESULT_SUBTYPES = {"success", "error_during_execution", *NATIVE_FAILURE_REASONS}
NATIVE_TERMINAL_REASONS = {
    "blocking_limit", "rapid_refill_breaker", "prompt_too_long", "image_error",
    "model_error", "api_error", "malformed_tool_use_exhausted",
    "aborted_streaming", "aborted_tools", "stop_hook_prevented", "hook_stopped",
    "tool_deferred", "max_turns", "background_requested", "completed",
    "budget_exhausted", "structured_output_retry_exhausted",
    "tool_deferred_unavailable", "turn_setup_failed",
}


class ConfigurationError(ValueError):
    """The worker configuration or the admitted policy is unusable."""


class UnsupportedSchemaError(ValueError):
    """A request declares a worker protocol this build does not implement."""

    reason = "unsupported_schema"


class ProfileUnavailableError(ConfigurationError):
    """The native profile/provider settings are missing or unreadable."""


class MissingModelError(ConfigurationError):
    """The native profile names no model; there is no shipped default."""


class NativeFailure(RuntimeError):
    """The native invocation failed or reported no completed turn."""

    def __init__(self, message, *, reason="native_failure"):
        super().__init__(message)
        self.reason = reason


class NativeStartError(OSError):
    """The native executable could not be started."""


class InvalidResultError(ValueError):
    """The native result envelope was missing, malformed or contradictory."""


def claude_bin():
    explicit = os.environ.get("MINDIE_CC_BIN")
    if explicit:
        return explicit
    found = shutil.which("claude")
    if found:
        return found
    raise NativeStartError("claude binary is not on PATH; the summary worker cannot run")


def native_environment():
    """Read provider/model and native runtime settings from the selected profile.

    An OS-started service has no interactive Claude process to materialize
    these settings. Never import hooks, tools, MCP, project settings or the
    complete profile into the isolated worker.
    """
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    from paths import load_adapter_config

    try:
        selected = load_adapter_config().get("claude_config_dir")
        home = Path(selected or env.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
        settings_file = home / "settings.json"
        settings = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.is_file() else {}
        if not isinstance(settings, dict):
            raise ValueError("native provider settings must be an object")
        provider = settings.get("env", {})
        if not isinstance(provider, dict):
            raise ValueError("native provider settings are not an object")
    except (OSError, ValueError, TypeError) as exc:
        raise ProfileUnavailableError("native provider settings are unavailable") from exc
    for key in (*paths.NATIVE_PROVIDER_ENV, *paths.NATIVE_RUNTIME_ENV):
        if key in provider:
            if not isinstance(provider[key], str):
                raise ProfileUnavailableError("native provider setting must be text")
            env[key] = provider[key]
    if isinstance(settings.get("model"), str):
        env.setdefault("ANTHROPIC_MODEL", settings["model"])
    if isinstance(settings.get("effortLevel"), str):
        env.setdefault("CLAUDE_CODE_EFFORT_LEVEL", settings["effortLevel"])
    return env


def native_model(env):
    """The model policy is the native profile's configured model, or absent."""
    model = env.get("ANTHROPIC_MODEL")
    if not isinstance(model, str) or not model.strip():
        raise MissingModelError(
            "the selected native profile names no model (ANTHROPIC_MODEL or "
            "settings model); the summary worker has no default"
        )
    return model.strip()


def _distill_capabilities():
    """Declare C6 only when the installed shared runtime exposes its API."""
    try:
        from mindie_knowledge.graded import distill
    except ImportError:
        return []
    try:
        from organizer_distill import REQUEST_SCHEMA as WORKER_SCHEMA
    except ImportError:
        return []
    schema = getattr(distill, "REQUEST_SCHEMA", None)
    required = ("with_worker_identity", "build_prompt", "decision_schema",
                "parse_validate_result")
    if (schema == WORKER_SCHEMA
            and all(callable(getattr(distill, name, None)) for name in required)):
        return [WORKER_SCHEMA]
    return []


def identity(env=None):
    """The actual executing policy: pinned sources plus the native model name."""
    if env is None:
        env = native_environment()
    # Read the implementation actually executing, not merely its command path.
    sources = [Path(__file__), Path(bounded.__file__), Path(summarizer.__file__),
               Path(paths.__file__)]
    companion = Path(__file__).with_name("organizer_distill.py")
    if companion.is_file():
        sources.append(companion)
    implementation = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    implementation["native_command"] = os.environ.get("MINDIE_CC_BIN", "claude")
    value = summarizer.policy_identity(model=native_model(env), effort=SUMMARY_EFFORT,
                                       implementation=implementation)
    capabilities = _distill_capabilities()
    if capabilities:
        value["capabilities"] = capabilities
        value["fingerprint"] = summarizer.digest(
            {key: item for key, item in value.items() if key != "fingerprint"})
    return value


def _usage_counters(raw):
    """Map the native usage object onto the shared receipt counters.

    ``input_tokens`` includes cache-creation input (both are fresh input-side
    processing); ``cached_input_tokens`` is cache reads. A missing or invalid
    counter makes the whole receipt unknown, never a zero.
    """
    if not isinstance(raw, dict):
        return None

    def number(key):
        value = raw.get(key)
        return value if type(value) is int and value >= 0 else None

    fresh, written = number("input_tokens"), number("cache_creation_input_tokens")
    cached, output = number("cache_read_input_tokens"), number("output_tokens")
    if None in (fresh, written, cached, output):
        return None
    return {
        "input_tokens": fresh + written,
        "cached_input_tokens": cached,
        "output_tokens": output,
    }


def _native_failure_diagnostic(data, validation=None):
    """Keep terminal cause and shape without storing model/provider free text."""
    def label(key, allowed):
        value = data.get(key)
        return value if isinstance(value, str) and value in allowed else "unknown"

    def number(key, lower, upper):
        value = data.get(key)
        return value if type(value) is int and lower <= value <= upper else None

    def shape(key):
        value = data.get(key)
        kind = ("absent" if key not in data else "null" if value is None
                else "text" if isinstance(value, str) else "array" if isinstance(value, list)
                else "object" if isinstance(value, dict) else "other")
        return dict(type=kind, count=len(value) if isinstance(value, (str, list, dict)) else None)

    return summarizer.canonical(dict(
        schema="mindie-cc-native-failure/1", is_error=True,
        subtype=label("subtype", NATIVE_RESULT_SUBTYPES),
        terminal_reason=label("terminal_reason", NATIVE_TERMINAL_REASONS),
        num_turns=number("num_turns", 0, 1_000_000),
        api_error_status=number("api_error_status", 100, 599),
        errors=shape("errors"), result=shape("result"),
        structured_output_present=data.get("structured_output") is not None,
        validation=validation,
    ))


def _parse_native_result(output, receipt):
    """The stream's sole terminal envelope; never retain native free text."""
    try:
        data = json.loads(output.strip())
    except (ValueError, UnicodeError):
        raise InvalidResultError("native result envelope is not one JSON object") from None
    if not isinstance(data, dict):
        raise InvalidResultError("native result envelope is not one JSON object")
    usage = _usage_counters(data.get("usage"))
    if usage is not None:
        receipt["usage"] = usage
    # Native num_turns includes local tool-result events and schema corrections;
    # it is neither an invocation count nor an HTTP-request count.
    if data.get("is_error") is True:
        receipt["turn_failed"] = True
        receipt["failure_diagnostic"] = _native_failure_diagnostic(
            data, receipt.get("native_validation")
        )
        subtype = data.get("subtype")
        reason = NATIVE_FAILURE_REASONS.get(subtype, "native_turn_failed") if isinstance(subtype, str) else "native_turn_failed"
        raise NativeFailure("native invocation reported an error result", reason=reason)
    # A well-formed non-error result envelope is terminal evidence; a missing
    # answer inside it is a known failure, not an uncertain call.
    receipt["turn_completed"] = True
    metadata = data.get("structured_output")
    if not isinstance(metadata, dict):
        raise InvalidResultError("completed structured summary is unavailable")
    receipt["final_message"] = summarizer.canonical(metadata)


def _terminal_result(raw):
    """Recognize the native stream's final complete result line."""
    try:
        data = json.loads(raw.rstrip().rsplit(b"\n", 1)[-1])
    except (ValueError, UnicodeError):
        return False
    return isinstance(data, dict) and data.get("type") == "result"


def _validation_location(content):
    """Project native validation text onto schema-owned labels, never values."""
    unknown = ("unknown", "unknown")
    prefix = "Output does not match required schema: "
    if not isinstance(content, str) or not content.startswith(prefix):
        return unknown
    location, separator, detail = content[len(prefix):].partition(": ")
    allowed_paths = {"root", "/blocks", "/navigation"}
    allowed_paths.update("/navigation/" + name for name in ("title", "summary"))
    for index in range(summarizer.MAX_BLOCKS):
        path = "/blocks/" + str(index)
        allowed_paths.add(path)
        allowed_paths.update(path + "/" + name for name in ("block_id", "title", "summary"))
    if not separator or location not in allowed_paths:
        return unknown
    patterns = (
        (r"must have required property '[^']*'", "required"),
        (r"must be equal to one of the allowed values: .+", "enum"),
        (r"must NOT have more than [0-9]+ characters \(got [0-9]+\)", "maxLength"),
    )
    keyword = next((name for pattern, name in patterns if re.fullmatch(pattern, detail)), "unknown")
    return location, keyword


def _parse_native_stream(output, receipt):
    """Read one native event stream, keeping only terminal output and safe facts."""
    terminal = None
    submissions, results = set(), set()
    failures = {}
    rejected = 0
    try:
        for line in output.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict) or terminal is not None:
                raise ValueError("unexpected event after terminal")
            if event.get("type") == "result":
                terminal = event
                usage = _usage_counters(event.get("usage"))
                if usage is not None:
                    # A later protocol violation does not erase billed usage.
                    receipt["usage"] = usage
                continue
            message = event.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict):
                    continue
                if (event.get("type") == "assistant" and item.get("type") == "tool_use"
                        and item.get("name") == "StructuredOutput" and isinstance(item.get("id"), str)):
                    submissions.add(item["id"])
                if (event.get("type") != "user" or item.get("type") != "tool_result"
                        or not isinstance(item.get("tool_use_id"), str)
                        or item["tool_use_id"] not in submissions
                        or item["tool_use_id"] in results):
                    continue
                results.add(item["tool_use_id"])
                if item.get("is_error") is True:
                    rejected += 1
                    key = _validation_location(item.get("content"))
                    failures[key] = failures.get(key, 0) + 1
    except (ValueError, UnicodeError):
        raise InvalidResultError("native output is not one complete event stream") from None
    if terminal is None:
        raise InvalidResultError("native stream has no terminal result")
    receipt["native_validation"] = {
        "submissions": len(submissions),
        "rejected": rejected,
        "errors": [{"path": path, "keyword": keyword, "count": count}
                   for (path, keyword), count in sorted(failures.items())],
    }
    _parse_native_result(summarizer.canonical(terminal), receipt)


def _invoke_native(prompt, schema, env, model, receipt):
    """One native call within the parent summary budget, without retries."""
    directory, managed = create_profile(prefix="mindie-cc-summary-")
    isolated = Path(directory)
    try:
        empty_mcp = isolated / "empty-mcp.json"
        empty_mcp.write_text('{"mcpServers": {}}\n', encoding="utf-8")
        claude_home = isolated / "claude-config"
        claude_home.mkdir()
        child_env = dict(env)
        for nested in (
            "CLAUDECODE",
            "CLAUDE_CODE_SESSION_ID",
            "CLAUDE_CODE_CHILD_SESSION",
            "CLAUDE_PLUGIN_ROOT",
            # The maintenance contract belongs to this worker's own spawns,
            # not to the native child.
            "MINDIE_MAINTENANCE_GROUP",
            "MINDIE_MAINTENANCE_OWNER",
            SUMMARY_SCRATCH_ENV,
        ):
            child_env.pop(nested, None)
        child_env["MAX_THINKING_TOKENS"] = "0"
        child_env["CLAUDE_CODE_EFFORT_LEVEL"] = SUMMARY_EFFORT
        child_env["CLAUDE_CONFIG_DIR"] = str(claude_home)
        child_env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
        argv = [
            claude_bin(),
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--json-schema",
            summarizer.canonical(schema),
            "--setting-sources",
            "",
            "--tools",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            str(empty_mcp),
            "--disable-slash-commands",
            "--safe-mode",
            "--no-session-persistence",
            "--permission-mode",
            "dontAsk",
            "--model",
            model,
            "--effort",
            SUMMARY_EFFORT,
        ]
        run_receipt = {}
        try:
            output = bounded.run(
                argv,
                prompt,
                timeout=native_timeout(),
                env=child_env,
                cwd=str(isolated),
                max_output=MAX_NATIVE_STREAM_BYTES,
                receipt=run_receipt,
                terminal=_terminal_result,
                core_managed=managed,
            )
        except OwnerExited as exc:
            raise NativeFailure("maintenance owner exited", reason="native_owner_exited") from exc
        except OutputLimitExceeded:
            raise
        except bounded.CommandCancelled:
            raise
        except TimeoutError:
            raise
        except OSError as exc:
            raise NativeStartError("native executable could not start") from exc
        except RuntimeError as exc:
            # A nonzero native exit without terminal evidence keeps the paid
            # outcome uncertain; it is never retried here.
            raise NativeFailure("native invocation exited unsuccessfully", reason="native_exit_nonzero") from exc
        finally:
            receipt["native_started"] = bool(run_receipt.get("started"))
            if run_receipt.get("cleanup_failed"):
                receipt["cleanup_failed"] = True
        _parse_native_stream(output, receipt)
    finally:
        # The shared owner must stop all descendants before removing its scratch.
        if not managed:
            try:
                shutil.rmtree(isolated)
            except OSError:
                receipt["cleanup_failed"] = True


def new_receipt():
    """One invocation's transport facts; a missing counter stays unknown."""
    return dict(native_started=False, turn_completed=False, turn_failed=False,
                usage=None, cleanup_failed=False)


class NativeClaudeModel:
    """LangMem `.invoke` bridge; no provider SDK, tools, history or retries."""

    def __init__(self, env, model, schema):
        self.env = env
        self.model = model
        self.schema = schema
        self.receipt = new_receipt()
        self.raw_result = None
        self.calls = 0

    def invoke(self, messages):
        from langchain_core.messages import AIMessage
        self.calls += 1
        if self.calls != 1:
            raise NativeFailure("summary attempted another model call")
        data = [dict(role=message.type, content=message.content) for message in messages]
        prompt = summarizer.render_prompt(data)
        if len(prompt.encode("utf-8")) > summarizer.MAX_PROMPT_BYTES:
            raise summarizer.SummaryInputError("complete prompt exceeds budget")
        _invoke_native(prompt, self.schema, self.env, self.model, self.receipt)
        if not self.receipt["turn_completed"]:
            raise NativeFailure("native execution did not report completion")
        message = self.receipt.get("final_message")
        if not isinstance(message, str) or not message:
            raise InvalidResultError("completed summary message is unavailable")
        raw = message.encode("utf-8")
        if len(raw) > summarizer.MAX_RESPONSE_BYTES:
            raise OutputLimitExceeded("summary metadata exceeds output budget")
        self.raw_result = message
        return AIMessage(content=self.raw_result)


def _category(error):
    if isinstance(error, (ConfigurationError, ScratchOwnershipError, NativeStartError, ImportError)):
        return "configuration"
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
        return "deadline"
    if isinstance(error, OutputLimitExceeded):
        return "output_limit"
    if isinstance(error, (InvalidResultError, summarizer.SummaryInputError,
                          summarizer.SummaryResultError, ValueError)):
        return "invalid_result"
    if isinstance(error, NativeFailure):
        return "native"
    return "unknown"


def _reason(error, receipt):
    if isinstance(error, summarizer.SummaryResultError):
        return error.reason
    if type(error) is ConfigurationError:
        return "worker_policy_changed"
    if isinstance(error, (ConfigurationError, ScratchOwnershipError)):
        return "configuration_invalid"
    if isinstance(error, NativeStartError):
        return "native_start_failed"
    if isinstance(error, ImportError):
        return "dependency_unavailable"
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
        return "native_deadline"
    if isinstance(error, OutputLimitExceeded):
        return "native_output_limit"
    if isinstance(error, InvalidResultError):
        return "native_output_unreadable" if receipt.get("turn_completed") else "result_contract_invalid"
    if isinstance(error, summarizer.SummaryInputError):
        return "request_invalid"
    if isinstance(error, NativeFailure):
        reason = getattr(error, 'reason', 'native_failure')
        if isinstance(reason, str) and reason.startswith('native_') and reason in summarizer.SUMMARY_FAILURE_REASONS:
            return reason
        return 'native_failure'
    return "unknown_failure"


def run(payload):
    # A new explicit request selects the C6 branch. Unknown schema versions are
    # rejected, never reinterpreted as a legacy summary request.
    if isinstance(payload, dict) and "schema_version" in payload:
        from organizer_distill import REQUEST_SCHEMA, run as run_distill
        if payload.get("schema_version") == REQUEST_SCHEMA:
            return run_distill(payload)
        raise UnsupportedSchemaError("unsupported worker request schema")
    # Invalid wire input is rejected before a model object exists. Valid requests
    # always receive an explicit outcome, even for configuration/parse failures.
    summarizer.validate_request(payload)
    started = time.monotonic()
    model = None
    result, error, error_reason, status = None, None, None, "returned"
    try:
        env = native_environment()
        name = native_model(env)
        if payload["policy_identity"] != identity(env):
            raise ConfigurationError("summary worker policy changed")
        schema = summarizer.output_schema([block["block_id"] for block in payload["blocks"]])
        model = NativeClaudeModel(env, name, schema)
        result = summarizer.summarize_batch(payload, model)
    except Exception as exc:
        error = _category(exc)
        error_reason = _reason(exc, model.receipt if model else {})
        # A missing executable is known to be uncalled. A completed model with
        # invalid metadata is a known failure with its usage retained. Interruption
        # after spawn without terminal evidence has an uncertain paid outcome.
        status = ("outcome_unknown" if model and model.receipt["native_started"]
                  and not model.receipt["turn_completed"]
                  and not model.receipt.get("turn_failed") else "failed")
    receipt = model.receipt if model else {}
    usage = receipt.get("usage")
    known = bool(isinstance(usage, dict)
                 and all(type(usage.get(key)) is int and usage[key] >= 0 for key in summarizer.COUNTERS))
    return summarizer.outcome(payload, status=status, result=result,
                              raw_result=(receipt.get("failure_diagnostic")
                                          or (model.raw_result if model else None)), usage=usage,
                              model_calls=int(bool(receipt.get("native_started"))), usage_known=known,
                              elapsed_ms=round((time.monotonic() - started) * 1000),
                              error=error, error_reason=error_reason,
                              cleanup_failed=bool(receipt.get("cleanup_failed", False)))


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity", action="store_true", help="Print actual policy identity without a model call")
    args = parser.parse_args()
    try:
        if args.identity:
            value = identity()
        else:
            raw = sys.stdin.buffer.read(MAX_DISTILL_REQUEST_BYTES + 1)
            if len(raw) > MAX_DISTILL_REQUEST_BYTES:
                raise summarizer.SummaryInputError("worker request exceeds wire budget")
            payload = json.loads(raw)
            versioned = isinstance(payload, dict) and "schema_version" in payload
            if not versioned and len(raw) > MAX_REQUEST_BYTES:
                raise summarizer.SummaryInputError("summary request exceeds wire budget")
            value = run(payload)
        sys.stdout.buffer.write((summarizer.canonical(value) + "\n").encode("utf-8"))
        sys.stdout.buffer.flush()
        return 0
    except Exception as exc:
        category = _category(exc)
        detail = getattr(exc, "reason", None)
        suffix = " (" + detail + ")" if isinstance(detail, str) and detail else ""
        print("summary protocol failed: " + category + suffix, file=sys.stderr)
        return AGENT_ERROR_EXIT_CODES.get(category, 2)


if __name__ == "__main__":
    raise SystemExit(main())
