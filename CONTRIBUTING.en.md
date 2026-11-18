# Contributing to `mindie-agent-cc`

中文版：[`CONTRIBUTING.md`](CONTRIBUTING.md)

Thank you for contributing to `mindie-agent-cc`, the native Claude Code plugin adapter for MindIE Agent.

## 1. Architecture & Guardrails

1. **Silent Semantic Skill & Thin Adapter Boundary (`HOST-NATIVE` / `HOST-WIRE`)**:
   - Keep `skills/mindie-agent/` as the single runtime skill directory with `user-invocable: false` so Claude Code loads Ascend NPU guidance semantically without exposing manual slash commands.
   - Invoke `${CLAUDE_PLUGIN_ROOT}/scripts/plugin_entry.py` in `.mcp.json` with zero `python -c` inline code (`AD-12`).
   - Delegate shared atomic writes, file locks, RSI helpers, runtime update, and MCP routing to `mindie-harness` (`assets/seed-lock.json` verified), keeping Claude-specific logic focused on hook hints, fork-aware `transcript.py` projection, and native marketplace release coordination.
2. **Default-On MCP, Consent-First `Stop` & Shared Remote Job Visibility (`HOST-ADMISSION`)**:
   - `knowledge`, `remote`, and `components` MCP surfaces work out of the box without mandatory `PreToolUse` binds or manual activation, never creating `admission.sqlite3` on read-only calls; `REMOTE_DEV_STATE_DIR` uses the shared profile `state_dir() / "remote"` (`AD-02`) so Subagents and reconnected turns can poll or stop parent `job_id`s.
   - `Stop` verifies `mindie-consent.json` and authorized `project_roots` before opening `admission.sqlite3` or spawning capture workers, exiting in `< 100ms` with zero SQLite files created when unconfigured, disabled, or out of scope.
3. **State Honesty & Auto-Update Recovery (`HOST-UPGRADE`)**:
   - Record `native_effect = "failed_cleanly"` when an external `claude plugin` subprocess exits non-zero cleanly so subsequent checks retry `marketplace add` (`#365`), reserving `"unknown"` strictly for timed-out/unsettled processes and classifying external CLI/Git/network errors as `reportable=False` (`#364`).
   - Maintain backward compatibility with `installation.json` / `cc.json` across upgrades and rollbacks, precede `except OSError:` with `except TimeoutError: raise`, and preserve `isError=False` (`receipt_warning` + `receipt_unconfirmed=True`) on post-execution bind accounting warnings.
4. **Line Endings & Module Size**:
   - Enforce Unix `LF` (`\n`) line endings on all text files (`python3 .github/scripts/check_lf_eol.py`).
   - Keep the eight retained `scripts/*.py` thin with total Python LOC `<= 1,200` (target `<= 1,050`) and a top-30-line `[Role]` / `[Invariants]` / `[Timeouts & Locks]` / `[Side-Effect Policy]` docstring header.

## 2. Pre-PR Verification

```bash
python3 .github/scripts/check_lf_eol.py
pytest -q -m "not live_npu"
python3 scripts/setup.py release-metadata --source . --tests tests --revision "$(git rev-parse HEAD)"
python3 .github/scripts/check_pr_checklist.py --profile host-plugin --body-file <pr_body.md>
```
