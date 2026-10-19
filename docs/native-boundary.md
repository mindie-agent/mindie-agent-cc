# Native Plugin & Runtime Boundary

<a href="native-boundary.zh.md">中文</a> | <b>English</b> · <a href="README.en.md">Documentation Index</a>

`mindie-agent-cc` follows the [MindIE Agent Engineering Design Principles](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.en.md) as a thin native Claude Code adapter. It translates Claude Code's native plugin lifecycle, hooks, and transcripts into the shared MindIE runtime without duplicating the knowledge engine or maintaining a separate state store.

---

## 1. Entrypoints & Task Identity Binding

### On-Demand Daily Usage
Ordinary development tasks do not require an explicit activation command:
- **Knowledge retrieval (`mindie-knowledge`)** binds to the active Claude Code task automatically via native hook and MCP metadata.
- **Remote Ascend execution (`remote-dev`)** operates independently and does not require knowledge activation or community sharing consent.
- **Explicit configuration (`/mindie-agent` or `/mindie-agent:init`)** provides an interactive setup and status entry. Its `UserPromptExpansion` event supplies the current task identity and working directory, reusing previously saved choices and prompting only when public contribution parameters (repository, account, or project root) are missing.

### Automatic `PreToolUse` → MCP Call Binding
Before a tool call executes, Claude Code triggers the `PreToolUse` hook with the native `tool_use_id`, session identity, tool name, and arguments. The subsequent MCP request carries that same ID in `_meta["claudecode/toolUseId"]`:
- The adapter binds the MCP tool call to its native task automatically—the model never needs to invent or pass session tokens.
- Each binding is single-use and verified against the exact tool name and argument digest; missing or mismatched bindings fail closed.
- New sessions and `--fork-session` branches receive independent bindings and never inherit a parent task's job ownership or collectable history.

---

## 2. `Stop` Hook & Public Transcript Extraction

### Fast, Non-Blocking `Stop` Handoff
The `Stop` hook completes within a short local handoff budget and returns a neutral response so it never forces the model to continue running:
- **When community sharing is disabled**: `Stop` performs no transcript reads, creates no capture records, and makes no organizer model calls.
- **When community sharing is enabled for the current project**: `Stop` verifies the session file inside the active Claude profile, checks the saved contribution scope, and enqueues the capture reference for the background worker.
- Enqueuing a capture record only confirms local admission; actual block indexing and GitHub PR publication are tracked by separate receipts in the background worker. Uncertain or failed model calls are never silently replayed.

### Deterministic Public Message Filtering
Only visible user and assistant messages from the current task are eligible for capture:
- The parser reads `sessionId`, timestamps, and public text blocks.
- Internal thinking blocks, tool call inputs/outputs, injected `isMeta` messages, attachments, system records, and sidechain turns are strictly excluded.
- The capture window never starts earlier than either the task creation timestamp or the timestamp when contribution was enabled.

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
