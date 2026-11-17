# Contributing to `mindie-agent-cc`

中文版：[`CONTRIBUTING.md`](CONTRIBUTING.md)

Thank you for contributing to `mindie-agent-cc`, the native Claude Code plugin adapter for MindIE Agent.

## 1. Architecture & Guardrails

1. **Single `/mindie-agent` Entry & Thin Adapter Boundary (`HOST-NATIVE` / `HOST-WIRE`)**:
   - Keep `skills/mindie-agent/` as the single slash-command skill directory (`Principle 3`), routing subcommands (`status`, `deactivate`, `recover`, `sharing-*`, `reporting-*`) via `/mindie-agent <subcommand>`.
   - Invoke `${CLAUDE_PLUGIN_ROOT}/scripts/plugin_entry.py` in `.mcp.json` with zero `python -c` inline code (`AD-12`).
   - Delegate shared atomic writes, file locks, and RSI helpers (`rsi_loop.py`, `rsi_explore.py`, `experiment_ledger.py`) to `mindie-harness` (`assets/seed-lock.json` verified), keeping Claude-specific logic focused on hook binds, fork-aware `transcript.py` projection, and native marketplace release coordination.
2. **Admission & Shared Remote Job Visibility (`HOST-ADMISSION`)**:
   - `remote` and `components` MCP surfaces work without `/mindie-agent` or mandatory `PreToolUse` binds (`P2.7`), and `REMOTE_DEV_STATE_DIR` uses the shared profile `state_dir() / "remote"` (`AD-02`) so Subagents and reconnected turns can poll or stop parent `job_id`s.
   - `knowledge_*` tools require an active `/mindie-agent` lease (`P2.1`) and never create `admission.sqlite3` on unactivated reads.
3. **State Honesty & Auto-Update Recovery (`HOST-UPGRADE`)**:
   - Record `native_effect = "failed_cleanly"` when an external `claude plugin` subprocess exits non-zero cleanly so subsequent checks retry `marketplace add` (`#365`), reserving `"unknown"` strictly for timed-out/unsettled processes and classifying external CLI/Git/network errors as `reportable=False` (`#364`).
   - Maintain backward compatibility with `installation.json` / `cc.json` across upgrades and rollbacks, precede `except OSError:` with `except TimeoutError: raise`, and preserve `isError=False` on post-execution bind accounting warnings.
4. **Line Endings & Module Size**:
   - Enforce Unix `LF` (`\n`) line endings on all text files (`python3 .github/scripts/check_lf_eol.py`).
   - Keep core Python modules `< 800` lines with a top-30-line `[Role]` / `[Invariants]` / `[Timeouts & Locks]` / `[Side-Effect Policy]` docstring header.

## 2. Pre-PR Verification

```bash
python3 .github/scripts/check_lf_eol.py
pytest -q -m "not live_npu"
python3 scripts/harness.py --source . --tests tests --revision "$(git rev-parse HEAD)"
python3 .github/scripts/check_pr_checklist.py --profile host-plugin --body-file <pr_body.md>
```
