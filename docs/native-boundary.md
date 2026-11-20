# Native Plugin & Runtime Boundary

<a href="native-boundary.zh.md">中文</a> | <b>English</b> · <a href="README.en.md">Documentation Index</a>

`mindie-agent-cc` follows the [MindIE Agent Engineering Design Principles](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.en.md) as a thin native Claude Code adapter. It translates Claude Code's native plugin lifecycle, hooks, and transcripts into the shared MindIE runtime without duplicating the knowledge engine or maintaining a separate state store.

---

## 1. Entrypoints & Task Identity Binding

### Silent Default-On MCP Tools & Semantic Skill Loading
`mindie-agent-cc` (`v0.1.32`) exposes three strictly disjoint MCP surfaces by default while keeping community experience capture strictly opt-in:
- **Disjoint default-on MCP surfaces (`knowledge`, `remote`, and `components`)** are available out of the box in main sessions, `Task` subagents, and `--fork-session` branches without manual slash-command activation: `knowledge` exposes only the 5 `knowledge_*` tools (`knowledge_query`, `knowledge_explain`, `knowledge_feedback`, `knowledge_distill`, `knowledge_sync`), `remote` exposes the 13 `claude` profile `remote_*` tools (`remote_Read` .. `remote_artifact_push`), and `components` exposes the 11 local/component tools (`mindie_status`, `mindie_doctor`, `diagnostics_status`, `diagnostics_bundle`, `profiler`, `sim`, `coordinator`, `npu_top`, `debugger`, `rsi_loop`, `experiment_ledger`). Cross-surface calls fail closed with `unknown_operation`. Read-only calls (`knowledge_query`, `knowledge_explain`, `mindie_status`, `mindie_doctor`) never create `admission.sqlite3`, while `knowledge_distill` and scoped `knowledge_feedback` auto-provision a default session lease (`not_before=0.0`) on demand unless explicitly deactivated.
- **Cold-start hooks & deterministic seed bootstrap**: Cold `SessionStart` (`session_start.py` -> `mindie_launch.py`) is pure-stdlib and marker-only, writing `pending-update-check.json` and returning `{}` in `< 30ms` without running `uv sync`, building a runtime, or creating SQLite. When an MCP call arrives before `cc.json` has an active interpreter, `mindie_launch.py:_cold` verifies `assets/mindie-harness-seed.pyz` against `assets/seed-lock.json`, runs it with `stdin=subprocess.DEVNULL` and `stdout=sys.stderr` so MCP JSON-RPC streams remain untouched, rejects `WindowsApps` / 0-byte Python stubs, and exits non-zero if no valid runtime interpreter is produced.
- **9-domain content-addressed bindings & wheel layout support**: `entry.py:initialize_profile` and `setup.py:initialize_profile` call `ensure_knowledge_binding(..., already_locked=True)` inside one `.cc.json.update.lock` transaction to provision all 9 domain engines under `engines/cc/<20hex>/<domain>.engine.json` (keeping `vllm-ascend.engine.json` as the default `engine_config` and leaving `cc.json` intact on `deferred` outcomes). `host_adapter.py:adapter_plugin_root` exposes `scripts/transcript.py` across both source checkouts and installed wheel (`mindie_cc`) layouts.
- **Silent semantic Skill (`skills/mindie-agent/SKILL.md`)** declares `user-invocable: false` so Claude Code loads Ascend NPU engineering guidance semantically on demand without adding manual slash commands to the `/` menu.

### Optional `PreToolUse` → MCP Call Hint & Fallback Identity
Before a main-session tool call executes, Claude Code triggers the passive `PreToolUse` hook (`< 30ms` `{}` fast path, zero stdin read on cold path) and passes `_meta["claudecode/toolUseId"]` in the MCP call:
- When a matching `PreToolUse` bind exists, `scripts/entry.py`'s `ManagedMCPAdapter("cc")` consumes it at most once (verifying exact tool name and argument digest and rejecting duplicate replay or tampered binds).
- When `PreToolUse` did not run (such as inside `Task` subagents or direct MCP calls), `scripts/entry.py`'s `ManagedMCPAdapter("cc")` falls back cleanly to verified `_meta`/environment session IDs or the connection-scoped `_CONNECTION_SESSION_ID`.
- New sessions and `--fork-session` branches receive independent session identities while sharing `REMOTE_DEV_STATE_DIR` (`state_dir() / "remote"`) so subagents can poll or stop parent `job_id`s.

---

## 2. `Stop` Hook & Public Transcript Extraction

### Fast, Consent-First & Revocation-Safe `Stop` Handoff
The `Stop` hook completes within a bounded local handoff budget (`timeout_sec=6.0`) and returns `{}` so it never forces the model to continue running:
- **When community sharing is unconfigured, disabled, or `cwd` is outside authorized `project_roots`**: `Stop` checks `mindie-consent.json` and `community_config` first and exits in `< 30ms` with `{}`—opening no `admission.sqlite3`, reading no transcripts, creating no capture records, and making no organizer model calls.
- **When community sharing is enabled and `cwd` is inside authorized `project_roots`**: `entry.py:_stop` inspects `Admission(profile.admission_db)` first—if a session was explicitly revoked (`status == "inactive"`), `Stop` exits immediately with `{}` and never revives it. Otherwise, `Stop` validates the native session file and creation boundary via `transcript.capture_source` and hands off bounded capture to `mindie_harness.runtime.knowledge_binding.run_stop_capture(..., timeout_sec=6.0)`.
- Enqueuing a capture record only confirms local admission; actual block indexing and GitHub PR publication are tracked by separate receipts in the background worker. Uncertain or failed model calls are never silently replayed.

### Deterministic Public Message Filtering
Only visible user and assistant messages from eligible opted-in tasks are captured:
- The parser reads `sessionId`, timestamps, and public text blocks.
- Internal thinking blocks, tool call inputs/outputs, `<command-...>` blocks, injected `isMeta` messages, attachments, system records, and sidechain turns are strictly excluded.
- The capture window never starts earlier than `max(created_at, activated_at)`—the later of the session/fork `enqueue` creation timestamp and the lease `activated_at` boundary.

---

## 3. Division of Responsibilities & Updates

| Layer | Responsibility |
|---|---|
| **Claude Code Host** | Native plugin marketplace catalog, installation/uninstallation records, Hook event dispatch, and MCP stdio transport. |
| **`mindie-agent-cc` (Adapter)** | Plugin manifests (`.claude-plugin/`, `.mcp.json`, `hooks/`, `skills/`), 8 core modules + 4 `v0.1.29` live-upgrade shims (`1,344` total LOC `<= 1,500`), fork-aware `transcript.py` parsing, and fixed Python 3.13 runtime handoff under `CLAUDE_PLUGIN_DATA`. |
| **Shared Substrate & Components** | `mindie-harness` (`ManagedMCPAdapter`, `ensure_knowledge_binding`, `run_stop_capture`, seed/wheelhouse updates, RSI), `mindie-knowledge` (9-domain `L1–L4` search, redaction, capture, PR loop), `remote-dev` (SSH execution & job polling), `mindie-diagnostics`, `mindie-coordinator`, `mindie-debugger`, `mindie-profiler`, `mindie-sim`, and `npu-top 0.3.5`. |

During updates, each release prepares a complete immutable runtime (source, lockfile, Python interpreter, and configuration). In-flight operations finish under a shared lock before the binding switches atomically while idle. Failed update candidates leave the existing runtime untouched and are not repeatedly retried in a tight loop.

---

## 4. Verification Scope

- **Verified**: Native capability probes on Claude Code (`>= 2.1.269 < 3.0.0`), 3-OS (`ubuntu-latest`, `macos-latest`, `windows-latest`) locked contract tests (`tests/test_launch_and_mcp_surfaces.py`, `tests/test_thin_plugin_contract.py`, `tests/test_legacy_self_upgrade.py`, `tests/test_transcript.py`), and automated CI host acceptance via `mindie-agent/mindie-bench/.github/workflows/verify-host-plugin.yml@main` (`host: cc`).
- **Boundary**: Automated CI and host acceptance verify disjoint MCP surfaces, cold-start/revocation contracts, and out-of-tree wheel builds; live remote Ascend A2/A3 NPU task benchmarks are tracked separately in `mindie-bench` (see [Framework Stability & Testing](framework-stability.md)).
- **Official Claude Code References**: [Hooks](https://code.claude.com/docs/en/hooks) · [Plugins](https://code.claude.com/docs/en/plugins-reference) · [Skills](https://code.claude.com/docs/en/skills)
