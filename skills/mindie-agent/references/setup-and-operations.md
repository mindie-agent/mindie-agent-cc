# Runtime Operations, Multi-Domain Routing & Graded Knowledge (Claude Code)

Load this reference when routing across knowledge domains, inspecting session/reporter state, or consulting graded-knowledge and profiler conventions.

## 1. Silent Default-On Session Lifecycle & Opt-In Capture

- **Disjoint Default-On MCP Surfaces**: `knowledge` (`knowledge_query`, `knowledge_explain`, `knowledge_feedback`, `knowledge_distill`, `knowledge_sync`), `components` (`mindie_status`, `mindie_doctor`, `diagnostics_status`, `diagnostics_bundle`, `profiler`, `sim`, `coordinator`, `npu_top`, `debugger`, `rsi_loop`, `experiment_ledger`), and `remote` (the 13 `claude` `remote_*` tools) are exposed on `tools/list` at startup and execute across main sessions, `Task` subagents, and forks without manual command activation. Read-only calls (`knowledge_query`, `knowledge_explain`, `mindie_status`, `mindie_doctor`) never create `admission.sqlite3`; `knowledge_distill` and scoped `knowledge_feedback` auto-provision a default session lease (`not_before=0.0`) on demand unless the session was explicitly deactivated.
- **Opt-In Consent, `Stop` Capture & Revocation Permanence**:
  - `base_config.parent / "mindie-consent.json"` and `community_config` store the persistent opt-in policy governing both scoped knowledge PRs and sanitized product fault Issues (`mindie_status` and `diagnostics_status` on the `components` surface inspect factual local state without mutating it).
  - The `Stop` hook checks `mindie-consent.json` and authorized `project_roots` before opening `admission.sqlite3` or spawning capture workers: when sharing is unconfigured, disabled, or `cwd` is outside authorized `project_roots`, `Stop` exits in `< 30ms` with `{}` and zero SQLite files created; when sharing is enabled and `cwd` is inside authorized `project_roots`, `Stop` checks `Admission.inspect(session)` first—preserving `status == "inactive"` revocation permanently without reviving the session—and otherwise hands off bounded (`6.0s`) redacted transcript capture via `run_stop_capture`.
  - Keep three states distinct when reporting status: (1) local consent config written, (2) background reporter running (`reporting.status == "running"`), and (3) external GitHub Issue/PR created.
- **Sharing & readiness commands**:
  ```sh
  python scripts/setup.py sharing --enable --project-root "$PWD"     # opt-in community PRs + fault reporting
  python scripts/setup.py sharing --local-only --project-root "$PWD" # local capture only, no outbound reporting
  python scripts/setup.py sharing --disable                          # explicit off
  python scripts/setup.py sharing --status                           # read-only tier / gh / cwd scope
  python scripts/setup.py doctor [--probe-remote] [--cwd DIR] [--timeout SEC]
  ```
  `mindie_doctor` (also exposed on the `components` MCP surface) is the read-only readiness check and never writes state. If it fails, report the concrete error once and continue via native `ssh`/`scp` or task-local scripts.

## 2. Multi-Domain Routing

- **9 Provisioned Domains Out of the Box**: `setup.py init`/`install` and `entry.py:initialize_profile` provision all 9 standard domain configs (`vllm-ascend`, `operators`, `xllm`, `mindie-sd`, `infer-core`, `infer-distributed`, `infer-observability`, `infer-ops-compat`, `agent-engineering`) under content-addressed `engines/cc/<20hex>/<domain>.engine.json` via `ensure_knowledge_binding` inside one `.cc.json.update.lock` transaction. Additional custom domains can still be registered with:
  ```sh
  python -m mindie_knowledge.loop.domain_routes register --adapter-config <path> --domain <domain> --engine-config <path>
  ```
- **Target-Domain Operations**: Read-only `knowledge_query`, `knowledge_explain`, and `knowledge_sync` route across registered `domain_configs` out of the box. Distilling or publishing contributions to a registered `domain` requires that domain's own opt-in consent and `project_root` scope, auto-provisioning the target domain's session lease on demand unless explicitly deactivated.

## 3. Graded Knowledge (`L1–L4`), `knowledge_distill` & Usage Feedback

- **`knowledge_query`**: Accepts `query`, optional `domain`, `limit` (`1..20`), `conditions`, `continuation`, `domain_path`, `token_budget` (`1..64000`, default `8000` in standard/reuse mode and `24000` in `mode="explore"`), `include_unknown` (default `false`), and `include_archived` (default `false`). Returns bounded navigation `cards` and deduplicated `foundation_cards` (`L1` premises), with revision-bound `ref` (`mindie://<domain>/units/<id>@<integer revision>`), `unit_ref`, and `feedback_ref`.
- **`knowledge_explain`**: Pass revision-bound `ref` URIs in `refs=[...]` with observed `conditions`, visible `known_refs` (exact `id@revision` units currently visible in active context), and `token_budget` (`1..64000`, default `16000` in reuse/diagnose and `24000` in `mode="explore"`). Shared prerequisites expand once in `requires`, requested bodies return untruncated in `units`, and `unresolved` reports `condition_unknown`, `condition_mismatch`, `budget_excluded`, `removed_or_superseded`, and `not_found`. All refs in a batch must share the same `mindie://<domain>/` authority.
- **Three-Valued `conditions` (`matched` | `mismatched` | `condition_unknown`)**: Units separate functional `tier` (`L1–L4`) from `evidence_strength` (`hardware_spec`, `reproduced_empirical`, `single_observation`, `hypothesis`). When a required premise or observed fact is missing or `"unknown"`, status is `condition_unknown`; probe the missing fact before applying the unit.
- **`knowledge_distill`**: Call `knowledge_distill(inputs, task_state, domain=..., related_refs=[...], proposal=...)` at `task_state="verified_milestone"` or `"completed"`. `hardware_spec` requires a `hardware_spec` source; `reproduced_empirical` requires `outcome == "verified"` with `repetitions >= 2`; `L4` requires verified leave-one-out factor attribution. `observed` or fault/build evidence (`compile_failed`, `infra_failed`, `measurement_invalid`) distills into `single_observation` or `hypothesis`. Pass `related_refs` for delta comparison (`revise`/`merge`/`supersede`), or pass a `mindie-distill-decision/1` `proposal` for deterministic validation and application (`model_calls=0`).
- **Four-Stage Usage Feedback**: `knowledge_feedback(ref=..., event_type=...)` records `discovered`, `loaded`, `applied`, or `evaluated` (requiring `run_id` and `outcome="supported"|"refuted"|"inconclusive"`, with optional `step_id`), alongside optional `rating="up"|"down"`.

## 4. Handle Continuation, Optional RSI Helpers & Profiler Discipline

- **Shared Handle Visibility (`coordinator` & `remote-dev`)**: `coordinator` (`execution_id`) and `remote-dev` (`job_id`) persist authoritative run state under shared profile state directories (`state_dir / "remote"`) so parent sessions, Subagents, and reconnected turns can inspect (`remote_TaskOutput`, `coordinator` status) or stop (`remote_TaskStop`, `coordinator` cancel) active jobs directly. Always inspect existing handles after compaction or interruption before submitting new runs.
- **Optional Experiment Ledger (`rsi_loop` / `experiment_ledger` component tools)**: Both helpers delegate to `mindie_harness.rsi` and remain optional for multi-round optimization sweeps. Record `experiment-ledger.jsonl` entries (`supported` | `refuted` | `inconclusive`) and verify multi-factor candidates via sequential leave-one-out pruning where the retained combination passes both correctness and performance checks.
- **Exclusive Physical NPU Lease & `mindie_profiler`**: Use `npu_count=0` only for confirmed device-free steps. Benchmarks, attribution checks, and `mindie_profiler` runs on the same physical NPU (or same A3 dual-Die package, `Phy-ID 0..15`) require an exclusive `coordinator` lease or an equivalent verified host reservation. Pass explicit `--time-window START:END`, `--step-window STEP=START:END` / `--steps-file`, or `--full-range` (`--unit us|ns|ms`) and explicit scene/`--clock-domain` annotations to `mindie_profiler`. Keep `ideal_hardware_lower_bound_ms`, `model_predicted_ms`, and `measured_device_ms` distinct, and rely on hardware throttle signal bits rather than a static `85°C` threshold.
