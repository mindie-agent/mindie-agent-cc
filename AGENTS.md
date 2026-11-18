# MindIE Agent — Claude Code Host Plugin (`mindie-agent-cc`)

This repository implements the native Claude Code plugin (`.claude-plugin/plugin.json`, `hooks/hooks.json`, `.mcp.json`, `skills/mindie-agent/`, and `scripts/`) for MindIE Agent, backed by `mindie-harness` (`API_VERSION = 1`) and the shared runtime components (`knowledge`, `remote-dev`, `diagnostics`, `debugger`, `profiler`, `sim`, `coordinator`, `npu-top`).

## 1. Core Contracts & Guardrails

- **Great Subtraction — 8 thin files, one substrate (`HOST-NATIVE` / `HOST-WIRE`)**:
  - `scripts/` retains `__init__.py`, `plugin_entry.py`, `session_start.py`, `mindie_launch.py`, `entry.py`, `host_adapter.py`, `transcript.py`, `setup.py`, and the thin `v0.1.29 -> v0.1.31` live-upgrade shims (`component_update.py`, `bridge.py`, `mcp_server.py`, `release_update.py`); total Python LOC stays `<= 1,500`.
  - `.mcp.json` and `hooks/hooks.json` invoke `${CLAUDE_PLUGIN_ROOT}/scripts/plugin_entry.py` directly with zero `python -c` inline blobs.
  - `skills/mindie-agent/` sets `user-invocable: false`; tools are accelerators, never roadblocks—on failure report the concrete error once (reconciling uncertain remote writes first) and continue via native `ssh`/`scp` or task-local scripts.
  - Bundle deterministic `assets/{mindie-harness-seed.pyz, seed-lock.json}` and delegate RSI, atomic writes, locking, runtime update, and MCP routing to `mindie_harness`.
- **Strict MCP surface separation (`HOST-ADMISSION`)**:
  - `knowledge` exposes only the 5 `knowledge_*` tools; `remote` exposes only the 13 `claude` `remote_*` tools (`remote_Read` .. `remote_artifact_push`, zero `remote_BashOutput` / `remote_KillShell`); `components` exposes only component tools and zero `knowledge_*`.
  - `REMOTE_DEV_STATE_DIR` defaults to `profile.state_dir / "remote"` and `REMOTE_DEV_HARNESS_PROFILE` defaults to `claude` so Subagents and reconnected sessions share `job_id` visibility.
  - `Stop` re-checks `sharing_status` tier (`contribute`/`local-only`) and `cwd_in_scope` before admission state, then hands off bounded capture with `timeout=6.0`; passive `PreToolUse`/prompt hooks never read stdin.
- **State honesty & compatibility (`HOST-UPGRADE`)**:
  - `setup.py release-metadata` validates `harness-compatibility.json`, every registered AST symbol, every test ID, and local evidence before emitting `mindie-plugin-release/1`.
  - Precede every `except OSError:` / broad `except Exception:` with `except TimeoutError: raise`; keep backward compatibility with legacy `installation.json` / `cc.json` / `component_release/runtime.json`.
  - Every `.py` module in `scripts/` starts with a `[Role]` / `[Invariants]` / `[Timeouts & Locks]` / `[Side-Effect Policy]` header closing on or before line 30, and uses Unix LF only.

## 2. Verification Commands

Run all checks inside an isolated environment (`HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `XDG_*`, `MINDIE_*`, `REMOTE_DEV_*`, `UV_CACHE_DIR`, `TMPDIR` isolated via `tests/conftest.py`):

```bash
python3 .github/scripts/check_lf_eol.py
pytest -q -m "not live_npu"
python3 scripts/setup.py release-metadata --source . --tests tests --revision "$(git rev-parse HEAD)"
```
