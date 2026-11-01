# Setup, Contribution Configuration & Operations (Claude Code)

Load this reference when performing first-time setup, configuring community contribution or fault reporting, or consulting detailed graded-knowledge and long-horizon ledger schemas.

## 1. Community Experience Contribution & Onboarding

- **Session Activation**: Invoking `/mindie-agent` enables knowledge and background `Stop` capture for the current session (`new` chats, forks, and subagents require their own entry).
- **First-Use Onboarding**:
  1. Present [用户须知](user-notice.md).
  2. Follow [Workspace Setup](workspace-setup.md) (offering workspace creation/initialization, reuse, or keeping the current directory, including when contribution is disabled).
  3. **Unified Contribution & Fault-Reporting Switch**:
     - `base_config.parent / "mindie-consent.json"` stores the persistent setting governing both scoped knowledge PRs and sanitized product fault Issues.
     - When enabling contribution (`contribute`) for a public knowledge repository `owner/repo` (e.g., `mindie-agent/knowledge-vllm-ascend`), ensure the personal fork exists (`gh repo fork owner/repo --clone=false --remote=false`) and bind `--fork USER/repo`:
       ```text
       /mindie-agent contribute --repository owner/repo --account USER --fork USER/repo --project-root /absolute/path --visibility public
       ```
     - When the hook returns `reporting.run_outside_hook=true`, execute `reporting.command_line` immediately outside the hook in the same setup flow (90-second timeout).
     - Keep three states distinct when reporting status: (1) local consent config written, (2) background reporter running (`reporting.status == "running"`), and (3) external GitHub Issue/PR created.

## 2. Multi-Domain Routing & `deactivate`

- **Route Registration**: Register an additional domain Engine/Feed in an existing base adapter config with:
  ```sh
  python -m mindie_knowledge.loop.domain_routes register --adapter-config <path> --domain <domain> --engine-config <path>
  ```
- **Target-Domain Activation**: Distilling or contributing to a registered non-default `domain` requires that domain's own consent and `project_root` scope plus `/mindie-agent --domain <domain>`.
- **Deactivation**: `/mindie-agent deactivate` revokes the current `session_id` across the default domain and all registered `domain_configs`, returning `status="partial"` (`partial=True`) with per-domain `domains[]` details if any domain fails during revocation.

## 3. Graded Knowledge (`L1–L4`), `knowledge_distill` & Usage Feedback

- **`knowledge_query`**: Accepts `query`, optional `domain`, `limit` (`1..20`), `conditions`, `continuation`, `domain_path`, `token_budget` (`1..16000`, default `800`), `include_unknown` (default `false`), and `include_archived` (default `false`). Returns bounded navigation `cards` and deduplicated `foundation_cards` (`L1` premises), with revision-bound `ref` (`mindie://<domain>/units/<id>@<integer revision>`), `unit_ref`, and `feedback_ref`.
- **`knowledge_explain`**: Pass revision-bound `ref` URIs in `refs=[...]` with observed `conditions`, visible `known_refs` (exact `id@revision` units currently visible in active context), and `token_budget` (default `4096`). Shared prerequisites expand once in `requires`, requested bodies return untruncated in `units`, and `unresolved` reports `condition_unknown`, `condition_mismatch`, `budget_excluded`, `removed_or_superseded`, and `not_found`. All refs in a batch must share the same `mindie://<domain>/` authority.
- **Three-Valued `conditions` (`matched` | `mismatched` | `condition_unknown`)**: Units separate functional `tier` (`L1–L4`) from `evidence_strength` (`hardware_spec`, `reproduced_empirical`, `single_observation`, `hypothesis`). When a required premise or observed fact is missing or `"unknown"`, status is `condition_unknown`; probe the missing fact before applying the unit.
- **`knowledge_distill`**: Call `knowledge_distill(inputs, task_state, domain=..., related_refs=[...], proposal=...)` at `task_state="verified_milestone"` or `"completed"`. `hardware_spec` requires a `hardware_spec` source; `reproduced_empirical` requires `outcome == "verified"` with `repetitions >= 2`; `L4` requires a verified `mindie-bench-ablation/1` receipt. `observed` or fault/build evidence (`compile_failed`, `infra_failed`, `measurement_invalid`) distills into `single_observation` or `hypothesis`. Pass `related_refs` for delta comparison (`revise`/`merge`/`supersede`), or pass a `mindie-distill-decision/1` `proposal` for deterministic validation and application (`model_calls=0`).
- **Four-Stage Usage Feedback**: `knowledge_feedback(ref=..., event_type=...)` records `discovered`, `loaded`, `applied`, or `evaluated` (requiring `run_id` and `outcome="supported"|"refuted"|"inconclusive"`, with optional `step_id`), alongside optional `rating="up"|"down"`.

## 4. Long-Horizon Continuation Ledger & Profiler Discipline

- **Launch-Time `in_flight_runs` Registration**: `coordinator` assigns `run_id` (`run_id == execution_id`) at submission. Immediately upon launching a run or background job, atomically update `PROGRESS.md` (`os.replace`) to record `{run_id, job_id, slot_id, host_endpoint, manifest_path, started_at}` under `in_flight_runs`, along with the task goal, current `git_commit`/`diff_digest`, verified baseline `run_id`, best verified candidate `run_id`, and active hypothesis.
- **Recovery-First-Query**: After any compaction, interruption, or resume, read `PROGRESS.md` `in_flight_runs` first and query `TaskClient.observe(run_id=...)` or `remote_job_status` before submitting new work.
- **Single-Writer `experiment-ledger.jsonl` & Sequential Pruning**: Append one JSON record per experiment linking `task_id`, `exp_id`, `run_ids`, `hypothesis`, `factors`, `factor_roles`, `unit_refs` (`unit_id` + `revision`), `receipt_refs`, `run_status`, `judgment` (`supported` / `refuted` / `inconclusive`), and `scope_conditions`. Treat `disposition == "candidate_for_sequential_pruning"` with `pruned_combination_verified == false` as **RETAIN**.
- **Exclusive Physical NPU Lease & `mindie_profiler`**: Use `npu_count=0` only for confirmed device-free steps. Benchmarks, attribution checks, and `mindie_profiler` runs on the same physical NPU (or same A3 dual-Die package) require an exclusive `coordinator` lease. Pass explicit `--time-window START:END`, `--step-window STEP=START:END` / `--steps-file`, or `--full-range` (`--unit us|ns|ms`) and explicit scene/`--clock-domain` annotations to `mindie_profiler`. Keep `ideal_hardware_lower_bound_ms`, `model_predicted_ms`, and `measured_device_ms` distinct, and rely on hardware throttle signal bits rather than a static `85°C` threshold.
