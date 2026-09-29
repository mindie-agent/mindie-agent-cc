#!/usr/bin/env python3
"""Native Claude organizer: isolated workdir, no tools/hooks/plugins.

Uses local `claude --print --output-format json`. Model and effort come from
an explicit override or the process auth/model environment — never a shipped
host-specific default. A missing model or host is a failure, never a
successful empty organization. One bounded call; no automatic retry.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from bounded import CommandCancelled, CommandTimedOut, OutputLimitExceeded, run
from mindie_knowledge.loop.process import AGENT_ERROR_EXIT_CODES

MAX_RESULT = 32768
_DIAGNOSTICS = {
    "configuration": "organizer configuration failed",
    "deadline": "organizer invocation exceeded the deadline",
    "native": "organizer native invocation failed",
    "invalid_result": "organizer result was invalid",
    "output_limit": "organizer output exceeded the bound",
}


class _Category(Exception):
    """Stage marker with no provider text or credentials."""

    def __init__(self, category):
        super().__init__(category)
        self.category = category


SYSTEM_PROMPT = """Return only JSON with title and summary strings for the supplied
redacted public conversation. Use a brief neutral title and summary. Attribute
reported claims; preserve stated uncertainty. Do not infer causes, tests, or
results. Never return or rewrite the body. No tools or nested agents."""


def normalize(result):
    if not isinstance(result, dict) or set(result) != {'title', 'summary'}:
        raise ValueError('summary must contain only title and summary')
    if not all(isinstance(value, str) and value.strip() for value in result.values()):
        raise ValueError('summary metadata must be nonempty text')
    return {key: value.strip() for key, value in result.items()}


def extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("organizer output is not JSON")
    return json.loads(text[start : end + 1])


def public_result_text(output: str) -> str:
    """Extract the public final text from `claude --print --output-format json`.

    Never persist or return hidden reasoning. Prefer the top-level result
    string; fall back to scanning public text only.
    """
    text = output.strip()
    if not text:
        raise ValueError("organizer produced no output")
    try:
        data = json.loads(text)
    except ValueError:
        return text
    if isinstance(data, dict):
        for key in ("result", "text", "content"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, list):
                chunks = []
                for item in value:
                    if isinstance(item, dict) and item.get("type") == "text":
                        chunk = item.get("text")
                        if isinstance(chunk, str):
                            chunks.append(chunk)
                    elif isinstance(item, str):
                        chunks.append(item)
                if chunks:
                    return "\n".join(chunks)
    return text


def claude_bin():
    explicit = os.environ.get("MINDIE_CC_BIN")
    if explicit:
        return explicit
    found = shutil.which("claude")
    if found:
        return found
    raise RuntimeError("claude binary is not on PATH; organizer cannot run")


def native_environment():
    """Read only provider/model settings from the selected native user profile.

    An OS-started service has no interactive Claude process to materialize
    these settings. Never import hooks, tools, MCP, project settings or the
    complete profile into the isolated organizer.
    """
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    from paths import load_adapter_config

    selected = load_adapter_config().get("claude_config_dir")
    home = Path(selected or env.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    settings_file = home / "settings.json"
    settings = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.is_file() else {}
    if not isinstance(settings, dict):
        raise ValueError("native provider settings must be an object")
    allowed = {
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
        "ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "CLAUDE_CODE_EFFORT_LEVEL", "MAX_THINKING_TOKENS",
    }
    provider = settings.get("env", {})
    if not isinstance(provider, dict):
        raise ValueError("native provider settings are not an object")
    for key in allowed:
        if key in provider:
            if not isinstance(provider[key], str):
                raise ValueError("native provider setting must be text")
            env[key] = provider[key]
    if isinstance(settings.get("model"), str):
        env.setdefault("ANTHROPIC_MODEL", settings["model"])
    if isinstance(settings.get("effortLevel"), str):
        env.setdefault("CLAUDE_CODE_EFFORT_LEVEL", settings["effortLevel"])
    return env


def organizer_model(env=None):
    # Reuse the native provider's available model; no plugin model setting.
    return (env or os.environ).get('ANTHROPIC_MODEL')


def organizer_effort(env=None):
    return 'low'


def run_native(payload):
    prompt = (
        "Write a brief title and summary of the supplied redacted public conversation; "
        "return JSON.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    isolated = Path(tempfile.mkdtemp(prefix="mindie-cc-organizer-"))
    try:
        empty_mcp = isolated / "empty-mcp.json"
        empty_mcp.write_text('{"mcpServers": {}}\n', encoding="utf-8")
        claude_home = isolated / "claude-config"
        claude_home.mkdir()
        try:
            env = native_environment()
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            raise _Category("configuration") from exc
        for nested in (
            "CLAUDECODE",
            "CLAUDE_CODE_SESSION_ID",
            "CLAUDE_CODE_CHILD_SESSION",
            "CLAUDE_PLUGIN_ROOT",
        ):
            env.pop(nested, None)
        env["MAX_THINKING_TOKENS"] = "0"
        env["CLAUDE_CODE_EFFORT_LEVEL"] = "low"
        env["CLAUDE_CONFIG_DIR"] = str(claude_home)
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
        try:
            binary = claude_bin()
        except RuntimeError as exc:
            raise _Category("native") from exc
        argv = [
            binary,
            "--print",
            "--output-format",
            "json",
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
            "--system-prompt",
            SYSTEM_PROMPT,
        ]
        model = organizer_model(env)
        if model:
            argv.extend(["--model", model])
        effort = organizer_effort(env)
        if effort:
            argv.extend(["--effort", effort])
        try:
            output = run(
                argv,
                prompt,
                timeout=35,
                env=env,
                cwd=str(isolated),
                max_output=MAX_RESULT,
            )
        except CommandCancelled:
            raise
        except CommandTimedOut as exc:
            raise _Category("deadline") from exc
        except OutputLimitExceeded as exc:
            raise _Category("output_limit") from exc
        except (OSError, RuntimeError) as exc:
            raise _Category("native") from exc
        try:
            public = public_result_text(output)
            if not public.strip():
                raise ValueError("organizer produced no public result")
            return normalize(extract_json(public))
        except ValueError as exc:
            raise _Category("invalid_result") from exc
    finally:
        shutil.rmtree(isolated, ignore_errors=True)


def main():
    raw = sys.stdin.buffer.read()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise _Category("invalid_result") from exc
    if not isinstance(payload, dict):
        raise _Category("invalid_result")
    print(json.dumps(run_native(payload), ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except _Category as exc:
        print(_DIAGNOSTICS[exc.category], file=sys.stderr)
        raise SystemExit(AGENT_ERROR_EXIT_CODES[exc.category])
    except CommandCancelled:
        print("organizer invocation cancelled", file=sys.stderr)
        raise SystemExit(130)
    except Exception:
        print("organizer failed unexpectedly", file=sys.stderr)
        raise SystemExit(2)
