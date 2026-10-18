# MindIE Agent — Claude Code Native Harness Plugin (`mindie-agent-cc`)

This repository implements the **Claude Code Native Harness Plugin** for MindIE Agent (`.claude-plugin/plugin.json`, `hooks/hooks.json`, `.mcp.json`, `skills/`, and `scripts/`). It bridges the Claude Code host lifecycle (`SessionStart`, `UserPromptExpansion`, `PreToolUse`, `Stop`, and stdio MCP server) to the shared runtime components (`mindie_knowledge`, `remote_dev`, `mindie_diagnostics`, `npu_top`, and `coordinator`).

---

## 1. Plugin Script Navigation & Call Graph

All plugin scripts live under `scripts/` and include standardized top-of-file Agent Contract docstrings (`[Role]`, `[Invariants]`, `[Timeouts & Locks]`, `[Side-Effect Policy]`):

| Layer | Script | Role & Key Responsibilities |
| :--- | :--- | :--- |
| **Native Facade & Bootstrap** | `mindie_launch.py` | Stable entrypoint invoked by `hooks/hooks.json` and `.mcp.json`. Enforces `HOOK_TOTAL=10.0s`, `HOOK_LOCK_BUDGET=2.0s`, and `BOOTSTRAP_TOTAL=540.0s`. |
| | `bootstrap.py` / `setup.py` / `install_state.py` | Materializes the locked `uv` runtime, validates `product-contract.json` & `uv.lock`, and writes atomic `installation.json` under `OperationLock`. |
| **Host Hook Lifecycle** | `entry.py` / `entry_state.py` | Handles `/mindie-agent` slash commands via `UserPromptExpansion` (`activate`, `deactivate`, `status`, `sharing-*`). Returns `status="degraded"` if service binding fails after lease activation. |
| | `bridge.py` | Implements `PreToolUse` (one-shot `claudecode/toolUseId` bind) and `Stop` (turn-end transcript capture handoff; never exits 2 or forces model continuation). |
| | `admission.py` | Thin wrapper around `mindie_knowledge.loop.activation.Admission` for session lease management. |
| **MCP Server & Identity** | `mcp_server.py` | Stdio JSON-RPC MCP server for knowledge and `remote_dev` tools. Consumes one-shot `claudecode/toolUseId` binds and preserves succeeded remote results even if local `finish_bind` fails. |
| | `identity.py` | Validates hook payloads and manages `native-tool-binds.sqlite3` (WAL + `busy_timeout=5000`). |
| | `receipt_layout.py` | Declares and validates `remote-layout.json` (`mindie-cc-remote-layout/1`) before external remote work. |
| **Transcript, Summary & Diagnostics** | `transcript.py` / `capture_config.py` | Parses Claude Code JSONL transcripts (`identify`, `read_material`, `capture_source`) with fork-boundary attribution. |
| | `organizer.py` | Isolated native `claude` CLI summary worker emitting `mindie-summary-outcome/1` envelopes. |
| | `diagnostic_support.py` / `diagnostic_fallback.py` | Records redacted diagnostic incidents (`mindie_diagnostics` or stdlib fallback) without altering caller outcomes. |
| **Configuration, Service & Subprocess** | `paths.py` / `consent.py` / `sharing.py` / `community_config.py` | Resolves MindIE-owned paths (never writing `~/.claude`), manages `mindie-consent.json`, and updates `mindie-community-config/1`. |
| | `bounded.py` / `owned_process.py` | Bounded subprocess runner with POSIX process-group (`owned_process.py`) and Windows Job Object tree cleanup. |
| | `knowledge_service.py` / `service_handoff.py` / `maintenance.py` / `release_update.py` | Local service connection, update handoff, feed sync/diagnostic retention, and background release checks. |
| | `product_contract.py` / `candidate_validate.py` / `runtime_probe.py` / `harness.py` | Validates dependency pins, source digests, runtime imports, and `harness-compatibility.json`. |

### Call Graph

```text
Claude Code Hook (SessionStart / UserPromptExpansion / PreToolUse / Stop)
  └─► mindie_launch.py (HOOK_TOTAL=10.0s, HOOK_LOCK_BUDGET=2.0s)
        ├─► bootstrap.py ──► setup.py (cold-start locked runtime preparation, BOOTSTRAP_TOTAL=540.0s)
        ├─► entry.py (UserPromptExpansion `/mindie-agent` slash commands)
        │     ├─► admission.py (shared Admission lease store)
        │     └─► knowledge_service.py / sharing.py / entry_state.py
        └─► bridge.py
              ├─► PreToolUse ──► identity.py (record one-shot claudecode/toolUseId bind)
              └─► Stop ──► transcript.py ──► knowledge_service.py (capture handoff)

Claude Code MCP Client (stdio JSON-RPC)
  └─► mindie_launch.py mcp-server ──► mcp_server.py
        ├─► identity.py (claim_bind / finish_bind on claudecode/toolUseId)
        ├─► receipt_layout.py (prepare_layout for remote-dev state)
        ├─► knowledge_service.py (knowledge tools)
        └─► remote_dev.mcp.server (remote-dev tools)
```

---

## 2. Host Lifecycle Contracts & Explicit `/mindie-agent` Gating

1. **Explicit Slash-Command Activation (`UserPromptExpansion` Only)**:
   - Session activation and community sharing configuration occur **only** via `/mindie-agent` slash commands in `entry.py`.
   - MCP `status` or tool discovery never activates a lease or enables sharing.
   - If lease activation succeeds in `entry.py` but background service connection fails, `entry.py` returns `status="degraded"` (never revoking the active lease).
2. **One-Shot Native Tool Binding (`PreToolUse` → `mcp_server.py`)**:
   - Claude Code MCP `tools/call` does not carry trusted session metadata in model arguments.
   - `bridge.py` (`PreToolUse`) binds `params._meta['claudecode/toolUseId']` to `(session_id, tool_name, input_sha256)` in `native-tool-binds.sqlite3`; `mcp_server.py` claims and consumes that bind once.
   - **Receipt Honesty Invariant**: Once a remote or knowledge tool call succeeds, if local `finish_bind` raises an `OSError`/`sqlite3.Error`, `mcp_server.py` attaches `bind_warning` + `diagnostic_ref` to `structuredContent` and **never** flips `isError=True` on the succeeded result.
3. **Turn-End Capture (`Stop`)**:
   - `bridge.py` (`Stop`) never exits 2 and never forces model continuation.
   - When contribution is enabled and the session transcript is in scope, `Stop` verifies fork-safe transcript ownership (`transcript.capture_source`) and hands off to the shared capture service.

---

## 3. Timeout Budgets & `v0.0.2` Anti-Over-Defensive Iron Rules

- **Timeout Hierarchy (`outer_timeout >= sum(inner_budgets) + cleanup_grace`)**:
  - `mindie_launch.py` enforces `HOOK_TOTAL = 10.0s`, `HOOK_LOCK_BUDGET = 2.0s`, and `BOOTSTRAP_TOTAL = 540.0s`.
  - `bounded.py` kills the entire child process group (POSIX) or Windows Job Object on timeout before draining pipes, and uses temporary files for stdin so unresponsive children cannot block pipe writes.
- **Four `v0.0.2` Iron Rules**:
  1. **Lightweight SQLite Connections**: `identity.py` opens `native-tool-binds.sqlite3` with `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000` (`timeout=5.0`). Never add `.owner`/`.authority.json` sidecar `lstat` checks or per-query schema reflection.
  2. **Honest External Receipts**: Never overwrite a succeeded tool call with `isError=True` due to a local bind-accounting failure (`mcp_server.py`), and preserve active leases as `status="degraded"` when background service startup fails (`entry.py`).
  3. **Simple Regular-File I/O**: Use `Path.exists()`, `Path.is_file()`, and `Path.open("rb")` in `entry.py`, `transcript.py`, `product_contract.py`, `diagnostic_support.py`, and `receipt_layout.py` instead of `O_NONBLOCK`/`O_NOFOLLOW`/`fstat` or parent symlink walks (`_present`).
  4. **Never Write `~/.claude`**: All MindIE state and configuration live under MindIE-owned directories (`paths.py`).

---

## 4. Verification & Live NPU Acceptance Commands

Use the shared test virtual environment (`/Volumes/data/code/mindie-release-20261014/codex-interrupt-final-env-20261016/bin/python`) to run the test suite:

```bash
# 1. Full unit, contract, and E2E MCP knowledge suite (Tier 0 + Tier 1)
/Volumes/data/code/mindie-release-20261014/codex-interrupt-final-env-20261016/bin/python -m pytest tests -q --ignore=tests/test_live_npu_acceptance.py

# 2. End-to-end MCP + Knowledge lifecycle verification
/Volumes/data/code/mindie-release-20261014/codex-interrupt-final-env-20261016/bin/python -m pytest tests/test_mcp_knowledge_e2e.py -q

# 3. Live Ascend NPU acceptance on standing A2 (173.125.1.2) & A3 (192.168.13.165) clusters (Tier 2)
/Volumes/data/code/mindie-release-20261014/codex-interrupt-final-env-20261016/bin/python -m pytest tests/test_live_npu_acceptance.py -q
```
