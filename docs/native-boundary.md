# Native Plugin & Runtime Boundary

<a href="native-boundary.zh.md">中文</a> | <b>English</b> · <a href="README.en.md">Documentation Index</a>

`mindie-agent-cc` follows the [MindIE Agent Engineering Design Principles](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.en.md) as a thin native Claude Code adapter. It translates Claude Code's native plugin lifecycle, hooks, and transcripts into the shared MindIE runtime without duplicating the knowledge engine or maintaining a separate state store.

---

## 1. Entrypoints & Task Identity Binding

### Silent Default-On MCP Tools & Semantic Skill Loading
`mindie-agent-cc` exposes all MCP surfaces by default while keeping community experience capture strictly opt-in:
- **Default-on MCP surfaces (`knowledge`, `remote`, and `components`)** are available out of the box in main sessions, `Task` subagents, and `--fork-session` branches without manual slash-command activation. Read-only retrieval (`knowledge_query`, `knowledge_explain`, `mindie_status`) never creates `admission.sqlite3`, while `knowledge_distill` and scoped `knowledge_feedback` auto-provision a default session lease (`not_before=0.0`) on demand unless explicitly deactivated.
- **Silent semantic Skill (`skills/mindie-agent/SKILL.md`)** declares `user-invocable: false` so Claude Code loads Ascend NPU engineering guidance semantically on demand without adding manual slash commands to the `/` menu.

### Optional `PreToolUse` → MCP Call Hint & Fallback Identity
Before a main-session tool call executes, Claude Code triggers the passive `PreToolUse` hook with the native `tool_use_id`, session identity, tool name, and arguments, and passes `_meta["claudecode/toolUseId"]` in the MCP call:
- When a matching `PreToolUse` bind exists, `mcp_server.py` consumes it at most once (verifying exact tool name and argument digest and rejecting duplicate replay or tampered binds).
- When `PreToolUse` did not run (such as inside `Task` subagents or direct MCP calls), `mcp_server.py` falls back cleanly to verified `_meta`/environment session IDs or the connection-scoped `_CONNECTION_SESSION_ID`.
- New sessions and `--fork-session` branches receive independent session identities while sharing `REMOTE_DEV_STATE_DIR` (`state_dir() / "remote"`) so subagents can poll or stop parent `job_id`s.

---

## 2. `Stop` Hook & Public Transcript Extraction

### Fast, Consent-First `Stop` Handoff
The `Stop` hook completes within a short local handoff budget and returns a neutral response so it never forces the model to continue running:
- **When community sharing is unconfigured, disabled, or `cwd` is outside authorized `project_roots`**: `Stop` checks `mindie-consent.json` and `community_config` first and exits in `< 100ms` with `{}`—opening no `admission.sqlite3`, reading no transcripts, creating no capture records, and making no organizer model calls.
- **When community sharing is enabled and `cwd` is inside authorized `project_roots`**: `Stop` verifies the session file inside the active Claude profile, auto-ensures the session lease (`not_before=0.0`), initializes `state-v4.sqlite3` if absent before the first capture, and enqueues the capture reference for the background worker.
- Enqueuing a capture record only confirms local admission; actual block indexing and GitHub PR publication are tracked by separate receipts in the background worker. Uncertain or failed model calls are never silently replayed.

### Deterministic Public Message Filtering
Only visible user and assistant messages from eligible opted-in tasks are captured:
- The parser reads `sessionId`, timestamps, and public text blocks.
- Internal thinking blocks, tool call inputs/outputs, injected `isMeta` messages, attachments, system records, and sidechain turns are strictly excluded.
- The capture window never starts earlier than `max(created_at, activated_at)`—the later of the session/fork creation timestamp and the lease `activated_at` boundary.

---

## 3. Division of Responsibilities & Updates

| Layer | Responsibility |
|---|---|
| **Claude Code Host** | Native plugin marketplace catalog, installation/uninstallation records, Hook event dispatch, and MCP stdio transport. |
| **`mindie-agent-cc` (Adapter)** | Plugin manifests (`.claude-plugin/`, `.mcp.json`, `hooks/`, `skills/`), task identity correlation, public transcript parsing, and fixed Python 3.13 runtime management under `CLAUDE_PLUGIN_DATA`. |
| **Shared Components** | `mindie-knowledge` (search, deterministic redaction, indexing queue, PR publication), `remote-dev` (SSH execution & job polling), and `mindie-diagnostics` (local receipts & opt-in fault reporting). |

During updates, each release prepares a complete immutable runtime (source, lockfile, Python interpreter, and configuration). In-flight operations finish under a shared lock before the binding switches atomically while idle. Failed update candidates leave the existing runtime untouched and are not repeatedly retried in a tight loop.

---

## 4. Verification Scope

- **Verified**: Native capability probes on Claude Code `2.1.269` (`deepseek-flash`, macOS isolated profile) confirmed `UserPromptExpansion`, `PreToolUse` → `_meta["claudecode/toolUseId"]` correlation, and silent `Stop` completion without retries.
- **Boundary**: Host hook correlation tests verify the adapter integration seam; full end-to-end contribution publication and native Windows host acceptance are tracked separately (see [Framework Stability & Testing](framework-stability.md)).
- **Official Claude Code References**: [Hooks](https://code.claude.com/docs/en/hooks) · [Plugins](https://code.claude.com/docs/en/plugins-reference) · [Skills](https://code.claude.com/docs/en/skills)
