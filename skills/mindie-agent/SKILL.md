---
name: mindie-agent
description: "Ascend NPU, vLLM-Ascend, xLLM, MindIE-SD, AscendC, Triton, and CANN engineering workflow: L1-L4 graded knowledge retrieval/distillation, remote NPU development, profiling, Roofline simulation, fault triage, and cluster lease coordination."
user-invocable: false
---

# MindIE Agent Runtime Workflow

## 1. Default-On MCP Tooling & Task Continuity

- **Ready out of the box**: While the MindIE plugin is enabled in Claude Code, all MCP surfaces—`knowledge` (`mindie_status`, `knowledge_query`, `knowledge_explain`, `knowledge_feedback`, `knowledge_distill`, `knowledge_sync`), `remote` (`remote_*`), and `components` (`coordinator`, `debugger`, `profiler`, `sim`, `diagnostics_status`, `diagnostics_bundle`)—work across main chats, `Task` subagents, and forks with zero pre-call session-binding gates or `context_file`.
- **Keep the user's task moving**: Knowledge, diagnostics, and local ledgers are optional helpers. If any helper fails, report that error once and continue with deterministic MCP or authorized host-native tools; reconcile any `unknown` remote write before repeating it.

## 2. Code-First Investigation, Knowledge & NPU Discipline

- **Inspect code first**: Read target source files and callers first; proceed directly when the codebase already explains the issue and fix.
- **Two-step `L1–L4` retrieval**:
  1. `knowledge_query`: pass concise technical terms, optional `domain` (e.g., `"vllm-ascend"`, `"operators"`), and observed `conditions` (`token_budget` default `8000`, up to `64000`). Treat `condition_unknown` as unproven—verify missing facts before applying the unit.
  2. `knowledge_explain`: batch-read exact `mindie://<domain>/units/<id>@<revision>` URIs in `refs=[...]`, observed `conditions`, and visible `known_refs=[...]`.
- **Feedback, distillation & NPU leases**: Record `knowledge_feedback` when useful and call `knowledge_distill` at `task_state="verified_milestone"` or `"completed"` (`reproduced_empirical` requires repeated verified runs; `L4` requires verified leave-one-out attribution). Obtain or verify a `coordinator` lease before running workloads on physical NPUs (respecting A3 dual-Die boundaries), inspect existing `execution_id` / `job_id` handles before resubmitting, and pass explicit `--time-window`, `--step-window`, or `--full-range` plus `--clock-domain` to `profiler`.
- **De-biased exploration**: Track multi-round sweeps via `rsi_loop.py` (`experiment-ledger.jsonl`) when helpful; when repeated `L3` attempts stall on a structural bottleneck, query `L4` mutators (`mode="explore"`) and optionally seed a subagent with `exploration_context.subagent_fact_pack.prompt_brief`.

## 3. Progressive References (Load Only When Needed)

- Session lifecycle, multi-domain routing, graded schemas & profiler rules: [Runtime Operations](references/setup-and-operations.md)
- Diagnostic isolation & scale-up verification (`W1–W6`, `C1–C4`): [Diagnostic Reasoning](references/diagnostic-reasoning.md)
