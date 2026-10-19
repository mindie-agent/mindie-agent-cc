# Setup, Contribution Configuration & Operations (Claude Code)

Load this reference **only** when the user is performing first-time setup, configuring or changing community experience contribution, configuring fault reporting, or troubleshooting capture status.

## 1. Community Experience Contribution Setup

- **Default Automatic Loop After Opt-In**: Once contribution is configured and enabled for the approved project scope, eligible native `Stop` events automatically capture the current task's public transcript, run deterministic local redaction + Gitleaks scanning, and prepare a knowledge PR in the background. Users never need to ask to save experience at the end of each task.
- **Completing Incomplete Configuration & On-Demand Knowledge Repo Fork**:
  - When the user explicitly opts into community contribution (`contribute`) for a target public knowledge repository `owner/repo` (such as `mindie-agent/knowledge-vllm-ascend`), first Star and Fork that target knowledge repository on demand via the authenticated `gh` channel (installation only forks `mindie-agent/mindie-agent`):
    ```sh
    gh api -X PUT /user/starred/owner/repo
    gh repo fork owner/repo --clone=false --remote=false
    ```
  - If contribution configuration is incomplete, reuse any already-approved values, ask only for the missing destination and scope, and bind `--fork USER/repo` so contribution branches push to the user's personal Fork:
    ```text
    /mindie-agent contribute --repository owner/repo --account USER --fork USER/repo --project-root /absolute/path --visibility public
    ```
  - Configuration and actual processing receipts are distinct facts: never report a PR as created or merged unless confirmed by a receipt.
- **Persistence & Disablement**:
  - There is no automatic opt-in ("no automatic yes").
  - A saved choice persists across sessions, forks, restarts, upgrades, and failures—it is never re-asked, and failure counts never revoke it.
  - Explicit disablement and legacy declined settings stay disabled until the user explicitly changes them; they are not alternative product modes or successful acceptance.

## 2. First-Use Onboarding & Optional Workspace Setup

During installation or first-use setup:
1. Present [用户须知](user-notice.md) (which explains visible-message sharing, redaction limits, model-provider processing, and public Git history).
2. Follow [optional workspace setup](workspace-setup.md): recommend the multi-worktree workspace (`vllm` + `vllm-ascend`), ask whether and where to create it, and after creation ask whether to initialize it. Reuse prior answers and offer this only during setup rather than ordinary task execution.
3. Offer the independent product fault reporting choice once (`/mindie-agent reporting-enable`, `reporting-disable`, or `reporting-later`). It is separate from knowledge contribution, an installer default-off is not a recorded user decision, and any recorded decision persists without being re-asked.

## 3. Error Handling & Background Worker Rules

- Report missing configuration, an out-of-scope task, or a component failure directly to the caller when relevant.
- Ordinary tasks have no `activate` / `lease` / `recover` step. Do not require a `deactivate` / `reactivate` cycle to recover from a component failure.
- An explicit task deactivation remains in force for that task.
- The background worker reports processing failures separately from captured material and preserves billed or uncertain model outcomes; **never** replay a model invocation to recover a missing receipt.
