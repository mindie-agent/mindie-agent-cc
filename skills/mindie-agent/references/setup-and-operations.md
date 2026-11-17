# Setup, Contribution Configuration & Operations (Claude Code)

Load this reference when performing first-time setup, configuring community contribution or fault reporting, or consulting detailed graded-knowledge and profiler conventions.

## 1. Community Experience Contribution & Onboarding

- **Unified `/mindie-agent` Entry**: Invoking `/mindie-agent` enables knowledge and background `Stop` capture for the current session (`new` chats, forks, and subagents require their own entry). Subcommands (`status`, `deactivate`, `recover --batch <ID> inspect|reconcile|retry|compact`, `sharing-enable`, `sharing-disable`, `reporting-status`) all route through `/mindie-agent <subcommand>`.
- **First-Use Onboarding & `setup.py sharing`**:
  1. Present [用户须知](user-notice.md).
  2. When `python <runtime_scripts>/setup.py sharing --status` reports `"needs_onboarding_prompt": true`, call Claude Code's native **`AskUserQuestion`** tool to ask the 3 onboarding questions together (Community Knowledge Sharing & Automated Fault Reporting, GitHub Star for `mindie-agent/mindie-agent`, and Local Development Workspace `vllm-ascend-workspace`), then follow [Workspace Setup](workspace-setup.md) to resolve `WORKSPACE_ROOT` first.
  3. **Unified Contribution & Fault-Reporting Switch**:
     - `base_config.parent / "mindie-consent.json"` stores the persistent setting governing both scoped knowledge PRs and sanitized product fault Issues.
     - Persist the user's choice via `setup.py sharing`:
       ```sh
       python <runtime_scripts>/setup.py sharing --enable --project-root "$WORKSPACE_ROOT"
       # or when keeping local-only mode:
       python <runtime_scripts>/setup.py sharing --disable
       ```
       (You may also use `/mindie-agent contribute --repository owner/repo --account USER --fork USER/repo --project-root /absolute/path --visibility public`.)
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

- **`knowledge_query`**: Accepts `query`, optional `domain`, `limit` (`1..20`), `conditions`, `continuation`, `domain_path`, `token_budget` (`1..64000`, default `8000` in standard/reuse mode and `24000` in `mode="explore"`), `include_unknown` (default `false`), and `include_archived` (default `false`). Returns bounded navigation `cards` and deduplicated `foundation_cards` (`L1` premises), with revision-bound `ref` (`mindie://<domain>/units/<id>@<integer revision>`), `unit_ref`, and `feedback_ref`.
- **`knowledge_explain`**: Pass revision-bound `ref` URIs in `refs=[...]` with observed `conditions`, visible `known_refs` (exact `id@revision` units currently visible in active context), and `token_budget` (`1..64000`, default `16000` in reuse/diagnose and `24000` in `mode="explore"`). Shared prerequisites expand once in `requires`, requested bodies return untruncated in `units`, and `unresolved` reports `condition_unknown`, `condition_mismatch`, `budget_excluded`, `removed_or_superseded`, and `not_found`. All refs in a batch must share the same `mindie://<domain>/` authority.
- **Three-Valued `conditions` (`matched` | `mismatched` | `condition_unknown`)**: Units separate functional `tier` (`L1–L4`) from `evidence_strength` (`hardware_spec`, `reproduced_empirical`, `single_observation`, `hypothesis`). When a required premise or observed fact is missing or `"unknown"`, status is `condition_unknown`; probe the missing fact before applying the unit.
- **`knowledge_distill`**: Call `knowledge_distill(inputs, task_state, domain=..., related_refs=[...], proposal=...)` at `task_state="verified_milestone"` or `"completed"`. `hardware_spec` requires a `hardware_spec` source; `reproduced_empirical` requires `outcome == "verified"` with `repetitions >= 2`; `L4` requires verified leave-one-out factor attribution. `observed` or fault/build evidence (`compile_failed`, `infra_failed`, `measurement_invalid`) distills into `single_observation` or `hypothesis`. Pass `related_refs` for delta comparison (`revise`/`merge`/`supersede`), or pass a `mindie-distill-decision/1` `proposal` for deterministic validation and application (`model_calls=0`).
- **Four-Stage Usage Feedback**: `knowledge_feedback(ref=..., event_type=...)` records `discovered`, `loaded`, `applied`, or `evaluated` (requiring `run_id` and `outcome="supported"|"refuted"|"inconclusive"`, with optional `step_id`), alongside optional `rating="up"|"down"`.

## 4. Handle Continuation, Optional RSI Helpers & Profiler Discipline

- **Shared Handle Visibility (`coordinator` & `remote-dev`)**: `coordinator` (`execution_id`) and `remote-dev` (`job_id`) persist authoritative run state under shared profile state directories so parent sessions, Subagents, and reconnected turns can inspect (`remote_job_status`, `coordinator` status) or stop (`remote_job_stop`, `coordinator` cancel) active jobs directly. Always inspect existing handles after compaction or interruption before submitting new runs.
- **Optional Experiment Ledger (`experiment_ledger.py` / `rsi_loop.py`)**: Both helpers delegate to `mindie_harness.rsi` and remain optional for multi-round optimization sweeps. Record `experiment-ledger.jsonl` entries (`supported` | `refuted` | `inconclusive`) and verify multi-factor candidates via sequential leave-one-out pruning where the retained combination passes both correctness and performance checks.
- **Exclusive Physical NPU Lease & `mindie_profiler`**: Use `npu_count=0` only for confirmed device-free steps. Benchmarks, attribution checks, and `mindie_profiler` runs on the same physical NPU (or same A3 dual-Die package, `Phy-ID 0..15`) require an exclusive `coordinator` lease or an equivalent verified host reservation. Pass explicit `--time-window START:END`, `--step-window STEP=START:END` / `--steps-file`, or `--full-range` (`--unit us|ns|ms`) and explicit scene/`--clock-domain` annotations to `mindie_profiler`. Keep `ideal_hardware_lower_bound_ms`, `model_predicted_ms`, and `measured_device_ms` distinct, and rely on hardware throttle signal bits rather than a static `85°C` threshold.
