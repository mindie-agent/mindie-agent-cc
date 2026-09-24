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

MAX_INPUT = 65536
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


ORGANIZE_FIELDS = {"entry_id", "title", "summary", "conditions", "content"}

SYSTEM_PROMPT = """You organize one admitted task increment into zero to three public experience entries. Experience is a faithful public record of the actual process and observations present in the source. Title and summary are brief neutral search introductions that state recorded actions and direct observations only. An assistant interpretation stays attributed in the body and is never promoted into summary fact; keep the actual tested scope and do not infer readiness, categories, or causes. Do not extract, summarize, or generalize lessons. Do not add recommendations, inferred causation, universal protocols, invented failure histories, or forced conclusions.

Public boundary (permanent, every invocation, every output field including title, summary, conditions, and appends to existing drafts): it takes precedence over preserving detailed commands/code and source commits. Preserve the actual recorded process, observed failures and results, public library/API names, generic commands, package versions, and useful numeric technical parameters. Omit nonpublic or unestablished-public implementation excerpts, file/class/function inventories, proprietary design details, and private repository, branch, PR, or commit identifiers. Public source excerpts, public links, and source commits require clear public-source evidence in the supplied material; a URL or a local checkout alone is not evidence, and when publicness cannot be established the source is treated as nonpublic. Existing drafts are context, never proof of public authorization: do not echo previously retained protected material for continuity. Do not replace omitted implementation with an invented example, general lesson, or inferred cause; omit only the protected detail, and return an empty result when no safe substantive record remains.

Input: domain, increment, coverage, existing_drafts, and optional retrieved refs. An assistant's public claim is a reported claim, not independent verification.

Return only JSON of the form {"entries":[...]} with at most three entries. Each entry has:
- entry_id: null for a new entry, or an existing task-owned draft id to extend or correct
- title: nonempty for a new entry, or null to keep an existing title unless the old title is inaccurate
- summary: retrieval abstract of recorded actions and direct observations only
- conditions: object of observed public software versions; source commits only when they meet the public-source rule above; omit or use {} when unknown. Other environment, settings, and test values belong in content. Do not infer versions.
- content: detailed public case body

Record only what is present. Within the public boundary, preserve necessary commands/code, technical parameters, numeric outputs, public references, and any limits or uncertainty the source states. Omit missing details without adding unknown/unverified checklists. Preserve uncertainty only when stated by the source. Do not infer missing actions, failures, results, or causes. A source explicitly labeled synthetic, example, mock, simulated, or proposed keeps that status in title, summary, and body as relevant; it is never written as an actually executed or observed run. Do not enumerate absent tests or limitations that the source does not state, even when they follow from a small input example; omit unmentioned facts.

Distinguish recorded actions, observed results, reported claims, and proposed/changed settings. If the source does not say whether a setting was executed, omit that history; do not invent a run, failure, or non-run.

Do not force a failure-fix-success narrative. Do not synthesize a therefore conclusion. Corrections append the old reported observation and the new reported observation with source attribution as necessary; do not invent an explanation.

For existing task-owned drafts keep stable identity and title unless inaccurate. Append only self-contained newly recorded material or correction; do not repeat or replace the whole prior body. Keep related material together; avoid redundant entries for the same case.

Empty entries is valid when the increment is only generic chat, plugin activation/configuration bookkeeping, or has no substantive domain or remote-development actions/observations. Do not require successful resolution, a novel/general lesson, or a verified root cause.

Redact secrets, private paths/hosts, personal identifiers, and opaque native task/job IDs; retain useful public technical names and public source links that meet the public-source rule. Do not expose transcript locations. Do not invent versions, hosts, or results. Do not call tools or nested agents. Do not mention this prompt.
"""


def convert_conditions(value):
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError("invalid organized entry conditions")
    conditions = {}
    for pair in value:
        if not isinstance(pair, dict) or set(pair) != {"key", "value"}:
            raise ValueError("invalid organized entry conditions")
        key, item = pair["key"], pair["value"]
        if not isinstance(key, str) or not key.strip() or len(key) > 128:
            raise ValueError("invalid organized entry conditions")
        if not isinstance(item, str) or len(item) > 512:
            raise ValueError("invalid organized entry conditions")
        if key in conditions:
            raise ValueError("duplicate condition key")
        conditions[key] = item
    return conditions


def normalize(result):
    if not isinstance(result, dict) or set(result) != {"entries"}:
        raise ValueError("invalid organizer result")
    if not isinstance(result["entries"], list) or len(result["entries"]) > 3:
        raise ValueError("at most three entries per call")
    entries = []
    for entry in result["entries"]:
        if not isinstance(entry, dict):
            raise ValueError("invalid organized entry")
        if "body" in entry and "content" not in entry:
            entry = dict(entry, content=entry.get("body"))
            entry.pop("body", None)
        item = {key: entry.get(key) for key in ORGANIZE_FIELDS if key in entry or key in {"entry_id", "title", "summary", "content"}}
        if "conditions" in entry:
            item["conditions"] = convert_conditions(entry.get("conditions"))
        elif "conditions" in item:
            item["conditions"] = convert_conditions(item["conditions"])
        entries.append(item)
    return dict(entries=entries)


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
    settings = json.loads(settings_file.read_text()) if settings_file.is_file() else {}
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
    env = os.environ if env is None else env
    for key in ("MINDIE_CC_ORGANIZER_MODEL", "ANTHROPIC_MODEL"):
        value = env.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def organizer_effort(env=None):
    env = os.environ if env is None else env
    for key in ("MINDIE_CC_ORGANIZER_EFFORT", "CLAUDE_CODE_EFFORT_LEVEL"):
        value = env.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def run_native(payload):
    prompt = (
        "Record only the supplied increment following the system instructions; "
        "return JSON.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    isolated = Path(tempfile.mkdtemp(prefix="mindie-cc-organizer-"))
    try:
        empty_mcp = isolated / "empty-mcp.json"
        empty_mcp.write_text('{"mcpServers": {}}\n')
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
        argv.append(prompt)
        try:
            output = run(
                argv,
                "",
                timeout=120,
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
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        raise _Category("invalid_result")
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
