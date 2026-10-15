#!/usr/bin/env python3
"""Native Claude summary worker for the shared block-indexing contract.

The shared core invokes this worker with ``--identity`` before block indexing,
then sends one admitted request on stdin and expects exactly one outcome
envelope line on stdout (``mindie-summary-outcome/1``). Exit zero means the
envelope was delivered, not that indexing succeeded; a valid request always
gets an explicit envelope, including failures and uncertain paid outcomes.
The caller owns queue transactions and scans returned metadata; this worker
never stores source text, rewrites a body, substitutes a fallback summary, or
retries an uncertain/billed model request.

The single native call is the local ``claude`` CLI in an isolated workdir with
no tools, hooks, plugins or MCP: provider and model come from the selected
native profile's settings (never a shipped host-specific default or
credential). A missing native model is a configuration failure, not a silent
default. Cancellation, maintenance-owner death and output bounds still end the
attempt; there is no arbitrary total execution deadline.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import bounded
from bounded import OutputLimitExceeded, OwnerExited
import paths
from mindie_knowledge.loop.process import AGENT_ERROR_EXIT_CODES
from mindie_knowledge.materials import summarizer

MAX_REQUEST_BYTES = 128 * 1024
SUMMARY_EFFORT = "low"
# The native JSON envelope wraps the bounded result text with usage/timing
# metadata; the result text itself is checked against MAX_RESPONSE_BYTES.
MAX_ENVELOPE_BYTES = summarizer.MAX_RESPONSE_BYTES + 16 * 1024


class ConfigurationError(ValueError):
    """The worker configuration or the admitted policy is unusable."""


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
    """Read only provider/model settings from the selected native user profile.

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
    for key in paths.NATIVE_PROVIDER_ENV:
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


def identity(env=None):
    """The actual executing policy: pinned sources plus the native model name."""
    if env is None:
        env = native_environment()
    # Read the implementation actually executing, not merely its command path.
    sources = (Path(__file__), Path(bounded.__file__), Path(summarizer.__file__),
               Path(paths.__file__))
    implementation = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    implementation["native_command"] = os.environ.get("MINDIE_CC_BIN", "claude")
    return summarizer.policy_identity(model=native_model(env), effort=SUMMARY_EFFORT,
                                      implementation=implementation)


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


def _parse_native_result(output, receipt):
    """One `claude --print --output-format json` envelope; never retain text."""
    try:
        data = json.loads(output.strip())
    except (ValueError, UnicodeError):
        raise InvalidResultError("native result envelope is not one JSON object") from None
    if not isinstance(data, dict):
        raise InvalidResultError("native result envelope is not one JSON object")
    usage = _usage_counters(data.get("usage"))
    if usage is not None:
        receipt["usage"] = usage
    # Native num_turns includes tool-result user events, including the local
    # StructuredOutput result. The CLI --max-turns bound limits model turns.
    if data.get("is_error") is True:
        receipt["turn_failed"] = True
        raise NativeFailure("native invocation reported an error result", reason="native_turn_failed")
    # A well-formed non-error result envelope is terminal evidence; a missing
    # answer inside it is a known failure, not an uncertain call.
    receipt["turn_completed"] = True
    metadata = data.get("structured_output")
    if not isinstance(metadata, dict):
        raise InvalidResultError("completed structured summary is unavailable")
    receipt["final_message"] = summarizer.canonical(metadata)


def _terminal_result(raw):
    """True once accumulated stdout holds one complete native result object.

    Partial input is not yet terminal; anything malformed stays for the strict
    envelope parse after the run. Never retains or reports the text.
    """
    try:
        data = json.loads(raw.decode("utf-8", "replace").strip())
    except (ValueError, UnicodeError):
        return False
    return isinstance(data, dict) and data.get("type") == "result"


def _invoke_native(prompt, schema, env, model, receipt):
    """One bounded native call in an isolated workdir; no retry, no deadline."""
    isolated = Path(tempfile.mkdtemp(prefix="mindie-cc-summary-"))
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
            "json",
            "--json-schema",
            summarizer.canonical(schema),
            "--max-turns",
            "1",
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
                timeout=None,
                env=child_env,
                cwd=str(isolated),
                max_output=MAX_ENVELOPE_BYTES,
                receipt=run_receipt,
                terminal=_terminal_result,
            )
        except OwnerExited as exc:
            raise NativeFailure("maintenance owner exited", reason="native_owner_exited") from exc
        except OutputLimitExceeded:
            raise
        except bounded.CommandCancelled:
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
        _parse_native_result(output, receipt)
    finally:
        try:
            shutil.rmtree(isolated)
        except OSError:
            receipt["cleanup_failed"] = True


class NativeClaudeModel:
    """LangMem `.invoke` bridge; no provider SDK, tools, history or retries."""

    def __init__(self, env, model, schema):
        self.env = env
        self.model = model
        self.schema = schema
        self.receipt = dict(native_started=False, turn_completed=False, turn_failed=False,
                            usage=None, cleanup_failed=False)
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
    if isinstance(error, (ConfigurationError, NativeStartError, ImportError)):
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
    if isinstance(error, ConfigurationError):
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
    known = bool((receipt.get("turn_completed") or receipt.get("turn_failed"))
                 and isinstance(usage, dict)
                 and all(type(usage.get(key)) is int and usage[key] >= 0 for key in summarizer.COUNTERS))
    return summarizer.outcome(payload, status=status, result=result,
                              raw_result=model.raw_result if model else None, usage=usage,
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
            raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
            if len(raw) > MAX_REQUEST_BYTES:
                raise summarizer.SummaryInputError("summary request exceeds wire budget")
            value = run(json.loads(raw))
        sys.stdout.buffer.write((summarizer.canonical(value) + "\n").encode("utf-8"))
        sys.stdout.buffer.flush()
        return 0
    except Exception as exc:
        category = _category(exc)
    print("summary protocol failed: " + category, file=sys.stderr)
    return AGENT_ERROR_EXIT_CODES.get(category, 2)


if __name__ == "__main__":
    raise SystemExit(main())
