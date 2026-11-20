# Contributing to `mindie-agent-cc`

中文版：[`CONTRIBUTING.md`](CONTRIBUTING.md)

Thank you for contributing to `mindie-agent-cc` (`v0.1.32`), the native Claude Code plugin adapter for MindIE Agent.

## 1. Architecture & Guardrails

1. **Silent Semantic Skill & Thin Adapter Boundary (`HOST-NATIVE` / `HOST-WIRE`)**:
   - Keep `skills/mindie-agent/` as the single runtime skill directory with `user-invocable: false` so Claude Code loads Ascend NPU guidance semantically without exposing manual slash commands.
   - Invoke `${CLAUDE_PLUGIN_ROOT}/scripts/plugin_entry.py` in `.mcp.json` and `hooks/hooks.json` with zero `python -c` inline code (`AD-12`).
   - Delegate shared atomic writes, file locks, 9-domain knowledge bindings (`ensure_knowledge_binding(..., already_locked=True)` producing content-addressed `engines/cc/<20hex>/<domain>.engine.json`), RSI helpers, runtime update, and MCP routing to `mindie-harness` (`assets/seed-lock.json` verified, with all 9 runtime packages including `npu-top 0.3.5` locked in [`pyproject.toml`](pyproject.toml) and [`product-contract.json`](product-contract.json)); `host_adapter.py:adapter_plugin_root` exposes `scripts/transcript.py` across both source checkouts and installed wheel layouts.
   - Keep cold `SessionStart` marker-only (`< 30ms` `pending-update-check.json` write, zero `uv sync` / SQLite), and isolate cold seed bootstrap with `stdin=subprocess.DEVNULL`, failing non-zero if no valid runtime interpreter is produced (`WindowsApps` stubs excluded).
2. **Disjoint Default-On MCP Surfaces, Consent-First `Stop` & Revocation Permanence (`HOST-ADMISSION`)**:
   - Keep the three MCP surfaces strictly disjoint and default-on without pre-call session-binding gates: `knowledge` (5 `knowledge_*` tools only), `components` (11 tools: `mindie_status`, `mindie_doctor`, `diagnostics_status`, `diagnostics_bundle`, `profiler`, `sim`, `coordinator`, `npu_top`, `debugger`, `rsi_loop`, `experiment_ledger`), and `remote` (13 `claude` tools). Read-only calls never create `admission.sqlite3`; `REMOTE_DEV_STATE_DIR` uses the shared profile `state_dir() / "remote"` (`AD-02`) so Subagents and reconnected turns can poll or stop parent `job_id`s.
   - `Stop` verifies `mindie-consent.json` and authorized `project_roots` before opening `admission.sqlite3` or spawning capture workers (`< 30ms` zero-SQLite exit when unconfigured, disabled, or out of scope), and preserves `inactive` revocation permanence via `Admission.inspect(session)` and `run_stop_capture`.
3. **State Honesty & Auto-Update Recovery (`HOST-UPGRADE`)**:
   - Record `native_effect = "failed_cleanly"` when an external `claude plugin` subprocess exits non-zero cleanly so subsequent checks retry `marketplace add` (`#365`), reserving `"unknown"` strictly for timed-out/unsettled processes and classifying external CLI/Git/network errors as `reportable=False` (`#364`).
   - Maintain backward compatibility with `installation.json` / `cc.json` across upgrades and rollbacks (including `v0.1.29 -> v0.1.32` live self-upgrade shims and single-lock `deferred` preservation), precede `except OSError:` with `except TimeoutError: raise`, and preserve `isError=False` (`receipt_warning` + `receipt_unconfirmed=True`) on post-execution bind accounting warnings.
4. **Line Endings & Module Budget**:
   - Enforce Unix `LF` (`\n`) line endings on all text files (`python3 .github/scripts/check_lf_eol.py`).
   - Keep `scripts/*.py` (8 core modules + 4 `v0.1.29` upgrade shims) within `<= 1,350` target / `<= 1,500` ceiling (currently `1,344` lines), with a top-30-line `[Role]` / `[Invariants]` / `[Timeouts & Locks]` / `[Side-Effect Policy]` docstring header per file; CI runs native host acceptance via `verify-host-plugin.yml` (`host: cc`).

## 2. Pre-PR Verification

```bash
python3 .github/scripts/check_lf_eol.py
pytest -q -m "not live_npu"
python3 scripts/setup.py release-metadata --source . --tests tests --revision "$(git rev-parse HEAD)"
python3 .github/scripts/check_pr_checklist.py --profile host-plugin --body-file <pr_body.md>
```
