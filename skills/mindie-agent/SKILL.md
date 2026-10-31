---
name: mindie-agent
description: Explicit /mindie-agent entry for knowledge, domain skills, feedback, distillation and background capture in the current Claude Code session. Configured deterministic tools remain available without this entry.
disable-model-invocation: true
---

# MindIE Agent (Unified Entry)

Use `/mindie-agent` to enable knowledge and experience for the current session.

The native `UserPromptExpansion` hook establishes this session's activation before
any knowledge tool is used. Never activate from topic matching. `knowledge_query`,
`knowledge_explain`, `knowledge_feedback`, `knowledge_distill`, `knowledge_sync`,
and background `Stop` capture all require an explicit `/mindie-agent` entry in the
current session (new chats, forked sessions, and subagents each require their own
manual entry). Saved contribution consent and project scope are additional capture
conditions and never activate a session on their own.

Remote and component tools remain available in every session without `/mindie-agent`:

- `remote-dev` MCP tools for remote file read/write, command execution, and background job management, plus read-only `mindie_status`.
- `components` MCP tools: `diagnostics_status` and `diagnostics_bundle` (included in the plugin runtime; local-only without uploading bundles), plus `coordinator`, `profiler`, and `sim` (which run the optional `mindie-coordinator`, `mindie-profiler`, and `mindie-sim` CLIs with an `argv` array when installed or configured via `component_commands`, returning `status`, `exit_code`, `stdout`, `stderr`, and parsed `result` only when `stdout` is JSON).

## Retrieve for a missing decision

In the current Claude Code conversation, manual `/mindie-agent` entry makes
knowledge available; it does not require a query before each repair.

- Read the implicated code and necessary callers. When they explain the
  violated invariant and support a fix and verification, proceed with zero
  knowledge calls. A filename or stack frame alone is insufficient.
- Probe missing environment or execution facts first. Use knowledge only for a
  missing contract, mechanism or method that could change the next action;
  never invent conditions to force a match.
- Reuse complete rules still visible. An exact returned ref needs no new query.
  Read graded rules through the native `knowledge_explain(refs=[...],
  conditions=..., known_refs=[...], token_budget=...)`, even for one unit. Copy
  revision references exactly; the existing native tool binding supplies task
  identity, not model-selected arguments.
- Stop reading once the next action and required premises are clear. New facts
  or failed verification can reopen retrieval. Keep correctness, device and
  acceptance checks; skipping knowledge needs no feedback call and does not
  disable this conversation's eligible background capture.

## Finish the verified task

Identify the requested behavior and its acceptance checks from the task and
relevant callers. Once the change satisfies those invariants and the affected
behavior checks pass, give the final response with the result, validation and
any remaining limitation. Let the host emit its normal completion event.

Extend the implementation or repeat validation only for a concrete failing
case, a required acceptance check, or new evidence that challenges the result.
Do not add optional parameter combinations, speculative compatibility paths or
defensive fallbacks after success without that evidence. An offline parser or
code repair does not require an invented hardware condition or an unrelated
live NPU run. Keep any hardware checks the actual task requires.

Knowledge feedback and background capture must not prolong a finished task.
A passing check alone does not fabricate native completion or C6 admission;
report unfinished requirements honestly if a budget or external dependency
prevents completion.

1. **Domain Knowledge (`L1–L4`), Multi-Domain Routing, Three-Valued `conditions` & Remote NPU Execution**:
   - Use native knowledge tools when a missing decision calls for domain knowledge, remote execution tools (`mindie-remote-dev`) when the task requires remote work, and component tools (`coordinator`, `profiler`, `sim`, `diagnostics_status`, `diagnostics_bundle`) for their deterministic operations. `knowledge_sync` refreshes configured public feeds for the current manually activated domain (or an explicitly activated optional `domain`), returns per-feed sync results, and neither starts the background knowledge service nor changes contribution settings.
   - **Graded Knowledge, Multi-Domain Routing, Reference Identity & `knowledge_distill`**:
     - `knowledge_query` accepts `query`, optional `domain` (selecting the default domain or a registered `domain_configs` Engine/Feed such as `"vllm-ascend"` or `"operators"`), `limit` (1–20), `conditions`, `continuation`, `domain_path`, `token_budget` (1–16000), and `include_unknown`. For graded units, `results[]` return `id`, `ref` (`mindie://<domain>/units/<id>@<integer revision>`), `unit_ref` (without revision), and `feedback_ref` (equal to `ref` for graded units; legacy material keeps `ref` and `feedback_ref` distinct).
     - Copy the exact returned revision-bound `ref` into `knowledge_explain(refs=[...], conditions=..., known_refs=..., token_budget=...)` (even for a single graded unit) and the exact `feedback_ref` into `knowledge_feedback`—never pass a bare `id`, as both tools route by the `mindie://<domain>/...` URI authority.
     - Call `knowledge_distill(inputs, task_state, domain=...)` at `task_state="verified_milestone"` or `"completed"` on caller-selected, already-authorized evidence. Runs with `observed` or fault/environment/build evidence (`compile_failed`, `infra_failed`, `measurement_invalid`) can yield `single_observation` or `hypothesis` units without elevating unverified faults to `hardware_spec` or `reproduced_empirical` (`reproduced_empirical` requires repeated `verified` evidence and `L4` requires a valid ablation receipt). Calling with `task_state="in_flight"` (or empty inputs) is a zero-call no-op.
     - Graded query options, multi-domain routing, `knowledge_distill`, `mindie-publication-contract/2`, independent Feed sync, and revision-bound unit feedback require the paired `mindie-knowledge 0.13.0` runtime; check the selected feed revision and installed runtime identity separately when diagnosing a mismatch.
   - **P0–P3 Progressive Context & Experience Flywheel**:
     - **P0 (On-Demand Closure Read)**: Call `knowledge_query` only when a needed reference is missing, inspect `cards` and deduplicated `foundation_cards` (set `include_archived=true` only when investigating archived/superseded units), and expand selected units via `knowledge_explain(refs=[...], conditions={...}, known_refs=[...], token_budget=...)`. Shared ancestors appear once in `requires`, full bodies appear untruncated in `units`, `known_refs` lists only exact `id@revision` units still fully visible in active context, and `unresolved` reports `condition_unknown` / `condition_mismatch` / `budget_excluded` / `removed_or_superseded` / `not_found`.
     - **P1/P2 (Delta & Proposal Distillation + 4-Stage Feedback)**: Pass `related_refs=[...]` to `knowledge_distill` so existing units are compared as a delta before writing, or pass a foreground `mindie-distill-decision/1` `proposal` for deterministic validation and application with `model_calls=0`. Record usage via `knowledge_feedback(ref, event_type="discovered"|"loaded"|"applied"|"evaluated", run_id=..., step_id=..., outcome="supported"|"refuted"|"inconclusive")` (optional `rating="up"|"down"` may accompany it).
     - **P3 (Three-Axis Exploration)**: Judge each experiment independently on `Performance`, `Correctness/Invariants`, and `Diagnostic Information Gain`—a sub-target win is not a refuted mechanism, a correctness failure is not a performance result, and a new diagnostic fact still counts as progress.
   - **Three-Valued `conditions` Rule (`matched` | `mismatched` | `condition_unknown`)**: Units separate functional `tier` (`L1–L4`) from `evidence_strength` (`hardware_spec`, `reproduced_empirical`, `single_observation`, `hypothesis`) and evaluate their declared condition keys. `condition_unknown` occurs when either the unit's declared `required` premise or the caller's `observed` fact is `"unknown"`, missing/not supplied, or has an incomparable format, and `unknown == unknown` never matches (an absent requirement or `"any"` is unconstrained `matched`, and historical `required: "unknown"` is never backfilled by later machine versions)—never treat `condition_unknown` as a match; verify whichever fact is missing/unknown (e.g., source `repository`/`revision` for an `L2` source SOP, or runtime environment for an NPU rule).
   - **Physical NPU Timing Mutual Exclusion & Profiler Discipline**: Only steps confirmed to need no device may use `npu_count=0` and parallel slot caches. Device JIT and warmup require the actual device lease; performance timing, attribution checks, and `mindie_profiler` runs on the same physical NPU (or same A3 dual-Die package) require an exclusive `coordinator` lease. `mindie_profiler` accepts explicit unlabeled `--time-window START:END`, labeled `--step-window STEP=START:END`, or `--full-range` (never inferring steps from kernel rows), uses explicit scene/clock annotations (`uncertain` / `clock_domain_unknown` otherwise), keeps single-kernel roofline bounds separate from unverified external `msmodeling` predictions, and never infers thermal throttling from a static `85°C` threshold.
2. **Status & Hook Context**: When `/mindie-agent` is invoked explicitly for status or setup, present the MindIE status from the `UserPromptExpansion` hook context. Never invent a session ID, repository, account, or project scope.
3. **Experience Loop After Manual Session Entry, `Stop` Boundary & Long-Horizon Continuation Ledger**:
   - In this manually enabled session, once community contribution is configured and enabled for the project scope, eligible native `Stop` events are captured, redacted, and packaged automatically in the background—users never need to ask to save experience at the end of a session.
   - **Host `Stop` != Long-Horizon Task Completion**: A single-turn `Stop` hook only hands off that turn's completed public transcript; it does not mean a multi-turn or multi-day engineering task is finished (`task_state="in_flight"` triggers a zero-call no-op in `knowledge_distill`).
   - **Launch-Time `in_flight_runs` Registration & Recovery-First-Query**: Immediately when `coordinator` or `remote-dev` launches a run or job, atomically update `PROGRESS.md` to register its `run_id` (`run_id == execution_id`, never replaced) or `job_id`, `slot_id`, endpoint, and `Run Manifest` path under `in_flight_runs` before polling or ending the turn. On resume or after compaction, query `in_flight_runs` first and never resubmit a duplicate command. Keep `Run Manifest` execution facts separate from condition-scoped hypothesis judgments in `experiment-ledger.jsonl`, never overwrite a succeeded remote run due to a local receipt/ledger error, and treat `candidate_for_sequential_pruning` with `pruned_combination_verified=false` as **RETAIN**.
4. **Unified Contribution & Fault-Reporting Switch**: Community knowledge sharing and sanitized product fault reporting share a single persistent choice in `<adapter-config-directory>/mindie-consent.json`. When the native Hook returns `reporting.run_outside_hook=true`, execute its `reporting.command_line` immediately outside the Hook in the same setup flow (using a 90-second outer timeout) without asking for another confirmation or leaving the command for the user. Reuse a valid saved choice without another prompt. Only when the entry returns `needs_choice` for legacy knowledge-only `contribute`, resolve it once through `/mindie-agent`; never split knowledge/reporting approvals or ask per task. Keep saved `disabled`, `read-only` and `later` choices without prompting. Keep three states strictly separate: (1) local consent configuration saved, (2) background reporter worker running (`reporting.status == "running"`), and (3) external GitHub Issue/PR created. Report `degraded`, authentication errors, timeouts, and `needs_choice` directly, and never retry an uncertain external write.

Resolve the installation's base adapter configuration first (`base_config` for a runtime binding); its parent contains `mindie-consent.json`. Use the existing entry to change the choice, never a guessed profile path.

## Knowledge use, diagnosis and exploration

Knowledge tiers (L1 constraints/measurements, L2 procedures, L3 decision branches,
L4 transformations), evidence strength and the current work mode are independent.
Hermes is experience revision across tiers; exploration does not require an L4 result.

- **Apply relevant knowledge when needed**: follow the missing-decision policy above. Read complete selected bodies and required premises before adopting them; probe unknown facts and choose L3 branches from current observations.
- **Explore an unresolved subproblem**: choose the least costly experiment that separates competing explanations. New facts or a first divergence count as progress even if the full test fails. Repeated no-information attempts prompt reconsideration, not an automatic mode switch. Use native independent context only when available and authorized; filtering an object or inheriting full history does not establish isolation. Device-free work may use `npu_count=0`; JIT, warmup and measurement use the required lease and actually assigned devices.
- **Revise from bounded evidence**: use `task_state="verified_milestone"` for a finished finding in an ongoing task, or `"completed"` for completion. Preserve counterevidence, human corrections, superseded plans and unresolved alternatives; compare related units and prefer revision or no-op. Separate evidence validity, goal attainment and reusable information, with supported/refuted/inconclusive feedback. L4 transformations still require a verified final-combination ablation receipt; diagnostic L2/L3 findings do not require performance gains.

For first-divergence checks, observer effects, history-dependent reproducers,
independent oracles, progressive scale-up verification (pruned vs. full weights),
same-topology baseline A/B parity, speculative graph/SLA sweeps, and parent/child
PR delivery workflows, read [Diagnostic Reasoning](references/diagnostic-reasoning.md).

## Progressive Context Disclosure (Read Only on Demand)

- **Installation, first-use GitHub authentication, enterprise-network token login or authentication repair**: read [GitHub Sign-In](references/github-authentication.md). Reuse a working login; otherwise guide browser login or token creation/input and verify credential reuse.
- **Normal installation and first-use setup, including when contribution is disabled**: present the existing [user notice](references/user-notice.md), then complete [Workspace Setup](references/workspace-setup.md). Offer creation/initialization, reuse or the current directory; approved creation/reuse includes native workspace registration. Reuse prior choices, and do not repeat this flow during ordinary tasks.

Do **not** load all reference files during ordinary troubleshooting. Read the relevant reference file **only** when the current turn requires it:

- **First-time onboarding, incomplete contribution configuration (`contribute`), the unified contribution/reporting switch, graded knowledge & `knowledge_distill` schemas, or long-horizon ledger & profiler rules**: read [Setup & Operations](references/setup-and-operations.md).
- **Sharing scope, local redaction boundaries, or public Git history questions**: read [用户须知](references/user-notice.md).

$ARGUMENTS
