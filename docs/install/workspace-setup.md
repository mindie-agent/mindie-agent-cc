# Development Workspace & First-Time Onboarding Setup

Run this flow during initial installation (Step 5) or whenever `python <runtime_scripts>/setup.py sharing --status` reports `"needs_onboarding_prompt": true`, right after presenting [用户须知](user-notice.md):

1. **Pop up the 3 onboarding questions via Claude Code's native `AskUserQuestion` tool in the user's language**:
   - **Question 1 (`header: "开发工作区"` / `"Workspace"`, `multiSelect: false`)** — *本地开发工作区 `vllm-ascend-workspace`* / *Local Development Workspace `vllm-ascend-workspace`*:
     - `初始化 vllm-ascend-workspace (Recommended)` / `Initialize vllm-ascend-workspace (Recommended)` — 新建并初始化 `vllm` + `vllm-ascend` 协同工作区 / Clone and initialize the multi-repo `vllm` + `vllm-ascend` workspace
     - `保持当前目录` / `Keep current directory` — 直接将当前工作目录作为工作区 / Use the current working directory as the project root
     - `使用已有工作区目录` / `Use existing workspace directory` — 绑定已有的本地工作区目录 / Bind an existing local workspace checkout
   - **Question 2 (`header: "社区共享"` / `"Sharing"`, `multiSelect: false`)** — *社区知识共享与自动故障上报* / *Community Knowledge Sharing & Automated Fault Reporting*:
     - `开启社区共享与故障上报 (Recommended)` / `Enable (Recommended)` — 开启脱敏经验共享 PR 与工具故障自动上报 / Enable sanitized experience sharing PRs and automatic tool fault Issue reporting
     - `仅保持本地模式` / `Keep Local-Only Mode` — 关闭外发共享与故障上报，仅使用本地检索与工具 / Disable outgoing sharing and fault reporting; keep retrieval and tools local
   - **Question 3 (`header: "GitHub Star"`, `multiSelect: false`)** — *顺手给 MindIE Agent 点个 GitHub Star 吗？ ⭐* / *Would you like to give mindie-agent a Star on GitHub ⭐?*:
     - `好，点个 Star ⭐ (Recommended)` / `Sure, star it ⭐ (Recommended)` — 在 GitHub 上 star `mindie-agent/mindie-agent` 支持一下项目 / Star `mindie-agent/mindie-agent` on GitHub to support the project
     - `先不用` / `Maybe later` — 直接开始使用 / Continue without starring
2. **Verify GitHub authentication**: Before cloning or configuring forks, ensure GitHub authentication is ready via [GitHub Sign-In](github-authentication.md) using process-scoped `GIT_CONFIG_COUNT` (without mutating global or system-wide Git configuration).
3. **Resolve or initialize `WORKSPACE_ROOT` first**:
   - New directory: `git clone https://github.com/mindie-agent/vllm-ascend-workspace.git "<destination>"` and set `WORKSPACE_ROOT="<destination>"`.
   - Existing directory: inspect the checkout, preserve local changes and configuration, and set `WORKSPACE_ROOT="<existing-path>"`.
   - Keep current directory: set `WORKSPACE_ROOT="$(pwd)"`.
4. **Register in the native client**:
   - Claude Code Desktop: open the workspace root in the native folder selector (or launch `claude --desktop` from the selected directory if supported by `claude --help`).
   - Claude Code CLI: launch the next interactive `claude` session from that directory.
5. **Initialize repositories (when creating/reusing `vllm-ascend-workspace`)**: Read the workspace's `AGENTS.md`, `README.md`, and `.agents/scripts/README.md`, then run:
   - `python3 .agents/scripts/repo_init.py --no-input` (reusing saved `workspace.json` fork settings), or
   - `python3 .agents/scripts/repo_init.py --github-user <account> --no-input` (for first-time setup with the user's forks).
6. **Persist the sharing & fault-reporting choice and optional Star**:
   - If the user chose `开启社区共享与故障上报 (Recommended)` / `Enable (Recommended)`: run `python <runtime_scripts>/setup.py sharing --enable --project-root "$WORKSPACE_ROOT"`.
   - If the user chose `仅保持本地模式` / `Keep Local-Only Mode`: run `python <runtime_scripts>/setup.py sharing --disable`.
   - If the user chose `好，点个 Star ⭐ (Recommended)` / `Sure, star it ⭐ (Recommended)`: run `gh api --hostname github.com --silent -X PUT /user/starred/mindie-agent/mindie-agent && gh api --hostname github.com --silent /user/starred/mindie-agent/mindie-agent`.
7. **Report status**: Report the plugin status, sharing & fault-reporting status, workspace path (`WORKSPACE_ROOT`), native client registration status, repository initialization status, and GitHub Star status clearly.
