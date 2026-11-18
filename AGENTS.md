# MindIE Agent — Claude Code Host Plugin (`mindie-agent-cc`)

This repository implements the native Claude Code plugin (`.claude-plugin/plugin.json`, `hooks/hooks.json`, `.mcp.json`, `skills/mindie-agent/`, and `scripts/`) for MindIE Agent, backed by `mindie-harness` (`API_VERSION = 1`) and the shared runtime components (`knowledge`, `remote-dev`, `diagnostics`, `debugger`, `profiler`, `sim`, `coordinator`, `npu-top`).

## 1. Core Contracts & Guardrails

- **Silent Semantic Skill & Zero-Inline `.mcp.json` (`HOST-NATIVE` / `HOST-WIRE`)**:
  - `skills/mindie-agent/` sets `user-invocable: false` so Claude Code loads Ascend NPU engineering guidance semantically on demand with zero slash-command clutter.
  - `.mcp.json` invokes `${CLAUDE_PLUGIN_ROOT}/scripts/plugin_entry.py` directly with zero `python -c` inline blobs (`AD-12`), exposing `knowledge`, `remote`, and `components` MCP surfaces.
  - Bundle deterministic `assets/{mindie-harness-seed.pyz, seed-lock.json}` and delegate shared RSI (`rsi_loop.py`, `rsi_explore.py`, `experiment_ledger.py`), atomic writes, and file locking to `mindie_harness`.
- **Silent Default-On MCP, Consent-First `Stop` & Shared Remote State (`HOST-ADMISSION`)**:
  - `knowledge`, `remote`, and `components` MCP tools run out of the box across main sessions, `Task` subagents, and forks with zero pre-call session-binding gates, falling back cleanly to `_CONNECTION_SESSION_ID` when optional `PreToolUse` binds are absent and routing `readOnlyHint=True` calls through a dedicated `read_pool`.
  - `REMOTE_DEV_STATE_DIR` points to the shared profile `state_dir() / "remote"` (`AD-02`) so Subagents and reconnected sessions can poll (`remote_job_status`) and cancel (`remote_job_stop`) parent `job_id`s.
  - `Stop` verifies `mindie-consent.json` and authorized `project_roots` before opening `admission.sqlite3` or spawning capture workers, exiting in `< 100ms` with zero SQLite files created when unconfigured, disabled, or out of scope.
- **State Honesty & Auto-Update Recovery (`HOST-UPGRADE`, `#364` / `#365`)**:
  - In `scripts/release_update.py`, reserve `native_effect = "unknown"` strictly for timed-out or unsettled child processes; a settled non-zero `claude plugin` exit records `native_effect = "failed_cleanly"` so subsequent checks retry `claude plugin marketplace add` cleanly (`#365`), and external `claude plugin` CLI / git / network failures classify as non-reportable (`reportable=False`, never `update_internal_failure`, `#364`).
  - Precede every `except OSError:` with `except TimeoutError: raise`, preserve `isError=False` (`receipt_warning` + `receipt_unconfirmed=True`) when local bind accounting fails after primary tool success, and retain backward compatibility with `installation.json` / `cc.json`.
  - Keep core Python modules `< 800` lines with a top-30-line `[Role]` / `[Invariants]` / `[Timeouts & Locks]` / `[Side-Effect Policy]` docstring header.

## 2. Verification Commands

Run all checks inside an isolated environment (`HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `XDG_*`, `MINDIE_*`, `REMOTE_DEV_*`, `UV_CACHE_DIR`, `TMPDIR` isolated via `tests/conftest.py`):

```bash
python3 .github/scripts/check_lf_eol.py
pytest -q -m "not live_npu"
python3 scripts/harness.py --source . --tests tests --revision "$(git rev-parse HEAD)"
```
