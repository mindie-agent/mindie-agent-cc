# MindIE Agent — Claude Code Host Plugin (`mindie-agent-cc`)

This repository implements the native Claude Code plugin (`v0.1.32`: `.claude-plugin/plugin.json`, `hooks/hooks.json`, `.mcp.json`, `skills/mindie-agent/`, and `scripts/`) for MindIE Agent, backed by `mindie-harness` (`API_VERSION = 1`, compatible with `mindie-harness >= 0.1.5`) and all 9 locked runtime packages (`mindie-harness`, `mindie-knowledge`, `remote-dev`, `mindie-diagnostics`, `mindie-debugger`, `mindie-profiler`, `mindie-sim`, `npu-top 0.3.5`, `mindie-coordinator`).

## 1. Core Contracts & Guardrails

- **Great Subtraction — 8 core files + 4 live-upgrade shims, one substrate (`HOST-NATIVE` / `HOST-WIRE`)**:
  - `scripts/` retains the 8 core modules (`__init__.py`, `plugin_entry.py`, `session_start.py`, `mindie_launch.py`, `entry.py`, `host_adapter.py`, `transcript.py`, `setup.py`) plus the 4 thin `v0.1.29 -> v0.1.32` live-upgrade shims (`component_update.py`, `bridge.py`, `mcp_server.py`, `release_update.py`); total Python LOC is `1,344` (`<= 1,350` target / `<= 1,500` ceiling).
  - `.mcp.json` and `hooks/hooks.json` invoke `${CLAUDE_PLUGIN_ROOT}/scripts/plugin_entry.py` directly with zero `python -c` inline blobs.
  - `skills/mindie-agent/` sets `user-invocable: false`; tools are accelerators, never roadblocks—on failure report the concrete error once (reconciling uncertain remote writes first) and continue via native `ssh`/`scp` or task-local scripts.
  - Bundle deterministic `assets/{mindie-harness-seed.pyz, seed-lock.json}`: cold seed bootstrap (`mindie_launch.py:_cold`) isolates `stdin=subprocess.DEVNULL` and `stdout=sys.stderr`, rejects `WindowsApps` / 0-byte Python stubs, and exits non-zero if no valid runtime interpreter exists after bootstrap; cold `SessionStart` stays marker-only (`pending-update-check.json` + `{}` in `< 30ms`, zero `uv sync` / SQLite).
- **Strict MCP surface separation & 9-domain binding (`HOST-ADMISSION`)**:
  - `knowledge` exposes only the 5 `knowledge_*` tools; `remote` exposes only the 13 `claude` `remote_*` tools (`remote_Read` .. `remote_artifact_push`, zero `remote_BashOutput` / `remote_KillShell`); `components` exposes the 11 local/component tools (`mindie_status`, `mindie_doctor`, `diagnostics_status`, `diagnostics_bundle`, `profiler`, `sim`, `coordinator`, `npu_top`, `debugger`, `rsi_loop`, `experiment_ledger`) and zero `knowledge_*` / `remote_*`.
  - `entry.py:initialize_profile` and `setup.py:initialize_profile` provision all 9 domain configs (`engines/cc/<20hex>/<domain>.engine.json`) via `ensure_knowledge_binding(..., already_locked=True)` under one `.cc.json.update.lock` transaction (`adapter_plugin_root` exposes `scripts/transcript.py` in both source and installed wheel layouts).
  - `REMOTE_DEV_STATE_DIR` defaults to `profile.state_dir / "remote"` and `REMOTE_DEV_HARNESS_PROFILE` defaults to `claude` so Subagents and reconnected sessions share `job_id` visibility.
  - `Stop` checks `sharing_status` tier (`contribute`/`local-only`) and `cwd_in_scope` before admission state, preserves `inactive` revocation permanence (`Admission.inspect(session)` is never revived once deactivated), and hands off bounded capture via `run_stop_capture(..., timeout_sec=6.0)`; passive `PreToolUse`/prompt hooks never read stdin.
- **State honesty & compatibility (`HOST-UPGRADE`)**:
  - `setup.py release-metadata` validates `harness-compatibility.json`, every registered AST symbol, every test ID, and local evidence before emitting `mindie-plugin-release/1`; `.github/workflows/checks.yml` gates PRs with `verify-host-plugin.yml` (`host: cc`).
  - Precede every `except OSError:` / broad `except Exception:` with `except TimeoutError: raise`; keep backward compatibility with legacy `installation.json` / `cc.json` / `component_release/runtime.json`.
  - Every `.py` module in `scripts/` starts with a `[Role]` / `[Invariants]` / `[Timeouts & Locks]` / `[Side-Effect Policy]` header closing on or before line 30, and uses Unix LF only.

## 2. Verification Commands

Run all checks inside an isolated environment (`HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `XDG_*`, `MINDIE_*`, `REMOTE_DEV_*`, `UV_CACHE_DIR`, `TMPDIR` isolated via `tests/conftest.py`):

```bash
python3 .github/scripts/check_lf_eol.py
pytest -q -m "not live_npu"
python3 scripts/setup.py release-metadata --source . --tests tests --revision "$(git rev-parse HEAD)"
```
