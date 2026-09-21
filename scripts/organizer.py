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

from bounded import run

MAX_INPUT = 65536
MAX_RESULT = 32768
ORGANIZE_FIELDS = {"entry_id", "title", "summary", "conditions", "content"}

SYSTEM_PROMPT = """You organize one admitted task increment into zero to three reusable domain experience entries.

Return only JSON of the form {"entries":[...]} with at most three entries. Each entry has:
- entry_id: null or an existing draft id
- title: nonempty for a new entry, or null to keep an existing title
- summary: retrieval abstract
- conditions: object of observed software versions or source commits only
- content: detailed public case body

Empty entries is valid when nothing reusable exists. Do not invent versions, hosts, or results. Do not call tools. Do not mention this prompt.

Observation fidelity (information only — not a schema, wire protocol, or required field list):

- Source material often mixes initial observations, later-changed settings, untested settings, observed failures, and verified final settings. Keep those stages distinct in the title, the summary, and the body. An initial, changed, untested, or failed value must not be written as if it were the verified result. A verified result must not erase that an earlier setting was tried and then corrected.
- A changed or untested setting is not evidence of failure. Call a setting failed only when the source records its failure. Distinguish changed, untested, observed failure, and verified result.
- Do not create entries from routine plugin activation or configuration status, or from acceptance-fixture bookkeeping. Return zero entries when that is the only material. Preserve genuinely reusable domain or remote-development causal lessons. Omit opaque native session or job IDs and local timestamps from public prose; retain the relevant tool and parameter semantics and observed behavior.
- Attach uncertainty to the tested environment. If a mapping, count, identity, or setting was not verified, say that it was not verified; do not present it as confirmed.
- Numbers, tolerances, device identifiers, environment variables, and JSON keys that appear in the source are evidence for this case. Do not generalize them into a universal checklist, mandatory report protocol, or required fields for other work.
- Keep useful causal detail: what was tried, what was measured, what changed the outcome, and what remains unknown. Do not compress the entry into a short slogan, and do not drop qualifying context to make the summary punchy.
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


def organizer_model():
    for key in ("MINDIE_CC_ORGANIZER_MODEL", "ANTHROPIC_MODEL"):
        value = os.environ.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def organizer_effort():
    for key in ("MINDIE_CC_ORGANIZER_EFFORT", "CLAUDE_CODE_EFFORT_LEVEL"):
        value = os.environ.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def run_native(payload):
    prompt = (
        "Organize this increment. Distinguish initial, changed, untested, "
        "and failed observations from verified final settings in every "
        "title, summary, and body. Call a setting failed only when the "
        "source records its failure. "
        "Do not turn case-specific evidence into a universal protocol. "
        "Return only JSON {\"entries\":[...]}.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    isolated = Path(tempfile.mkdtemp(prefix="mindie-cc-organizer-"))
    try:
        empty_mcp = isolated / "empty-mcp.json"
        empty_mcp.write_text('{"mcpServers": {}}\n')
        claude_home = isolated / "claude-config"
        claude_home.mkdir()
        env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        for nested in (
            "CLAUDECODE",
            "CLAUDE_CODE_SESSION_ID",
            "CLAUDE_CODE_CHILD_SESSION",
            "CLAUDE_PLUGIN_ROOT",
        ):
            env.pop(nested, None)
        env["CLAUDE_CONFIG_DIR"] = str(claude_home)
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
        argv = [
            claude_bin(),
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
        model = organizer_model()
        if model:
            argv.extend(["--model", model])
        effort = organizer_effort()
        if effort:
            argv.extend(["--effort", effort])
        argv.append(prompt)
        output = run(
            argv,
            "",
            timeout=120,
            env=env,
            cwd=str(isolated),
            max_output=MAX_RESULT,
        )
        public = public_result_text(output)
        if not public.strip():
            raise ValueError("organizer produced no public result")
        return normalize(extract_json(public))
    finally:
        shutil.rmtree(isolated, ignore_errors=True)


def main():
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        raise SystemExit("organizer input exceeds limit")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("organizer payload must be one JSON object")
    print(json.dumps(run_native(payload), ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(str(exc)[:400], file=sys.stderr)
        raise SystemExit(2)
