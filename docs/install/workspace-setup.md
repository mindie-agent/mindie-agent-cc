# Development Workspace & First-Time Onboarding Setup

Run this flow during initial installation (Step 4) or whenever `python <runtime_scripts>/setup.py sharing --status` reports `"tier": "unconfigured"`, right after presenting [用户须知](user-notice.md).

## 1. Install, cold start, and readiness

```sh
uv run https://github.com/mindie-agent/mindie-agent/releases/latest/download/install.py --host cc
UV_SYSTEM_CERTS=true UV_NATIVE_TLS=true claude --init-only
claude mcp list
SETUP_PY=$(python3 -c "import glob, os; p = sorted(f for f in glob.glob(os.path.expanduser('~/.claude/plugins/**/scripts/setup.py'), recursive=True) if 'mindie-agent' in f); print(p[-1] if p else '')")
uv run --no-project --python 3.13 python "$SETUP_PY" doctor --timeout 15
uv run --no-project --python 3.13 python "$SETUP_PY" sharing --status
```

`gh` is optional and **never blocks installation**: when it is missing or logged out, recommend `local-only` and continue. Confirm the `knowledge`, `remote`, and `components` MCP servers are `Connected`, report the `mindie_doctor` `overall_status`, then immediately present the onboarding questions.

## 2. Three dynamic `gh`-aware native `AskUserQuestion` prompts

1. **Question 1 (`header: "Workspace"`, `multiSelect: false`)** — *Local Development Workspace `vllm-ascend-workspace`*:
   - `Initialize vllm-ascend-workspace (Recommended)` — clone and initialize the multi-repo `vllm` + `vllm-ascend` workspace.
   - `Keep current directory` — use the current working directory as the project root.
   - `Use existing workspace directory` — bind an existing local checkout.
2. **Question 2 (`header: "Sharing"`, `multiSelect: false`)** — *Community Knowledge Sharing & Automated Fault Reporting*:
   - `Enable (Recommended)` when `gh` is authenticated; `Keep Local-Only Mode (Recommended)` otherwise. Keep both labels fixed and switch only the Recommended marker.
   - `Enable` authorizes sanitized experience-sharing PRs and automatic tool-fault Issue reporting; `Keep Local-Only Mode` disables outbound sharing and reporting while keeping retrieval and tools local.
3. **Question 3 (`header: "NPU Connection"`, `multiSelect: false`)** — *One-step Ascend NPU SSH connection*:
   - `Configure SSH NPU now (Recommended)` — collect `host`, `user`, `port`, and container (if any). In terminal mode, the final step may call `remote_auth` with `--password`, supplied only through a hidden prompt and never written to chat or shell history.
   - `Later` — continue without a remote endpoint.

Then append **one polite line** after the questions: `⭐ If MindIE Agent helps you, consider starring mindie-agent/mindie-agent on GitHub; it is entirely optional.` Run the Star call only after explicit consent.

## 3. Resolve or initialize `WORKSPACE_ROOT`

- New directory: `git clone https://github.com/mindie-agent/vllm-ascend-workspace.git "<destination>"` and set `WORKSPACE_ROOT="<destination>"`.
- Existing directory: inspect the checkout, preserve local changes and configuration, and set `WORKSPACE_ROOT="<existing-path>"`.
- Keep current directory: set `WORKSPACE_ROOT="$(pwd)"`.

## 4. Register in the native client

- Claude Code Desktop: open the workspace root in the native folder selector (or launch `claude --desktop` from the selected directory if supported by `claude --help`).
- Claude Code CLI: launch the next interactive `claude` session from that directory.

## 5. Initialize repositories (when creating/reusing `vllm-ascend-workspace`)

Read the workspace's `AGENTS.md`, `README.md`, and `.agents/scripts/README.md`, then run:

- `python3 .agents/scripts/repo_init.py --no-input` (reusing saved `workspace.json` fork settings), or
- `python3 .agents/scripts/repo_init.py --github-user <account> --no-input` (first-time setup with the user's forks).

## 6. Persist the sharing choice, NPU endpoint, and optional Star

```sh
uv run --no-project --python 3.13 python "$SETUP_PY" sharing --enable --project-root "$WORKSPACE_ROOT"
uv run --no-project --python 3.13 python "$SETUP_PY" sharing --local-only --project-root "$WORKSPACE_ROOT"
uv run --no-project --python 3.13 python "$SETUP_PY" sharing --disable
```

- If the user chose `Enable`: run the `--enable` line.
- If the user chose `Keep Local-Only Mode`: run the `--local-only` line (or `--disable` for an explicit off).
- If an NPU connection was configured, register the endpoint with `remote_auth` (terminal `--password` only in the hidden prompt).
- If the user explicitly chose Star: run `gh api --hostname github.com --silent -X PUT /user/starred/mindie-agent/mindie-agent && gh api --hostname github.com --silent /user/starred/mindie-agent/mindie-agent`.

## 7. Report status

Report the plugin version, three MCP statuses, `mindie_doctor` verdict, `sharing` tier, `WORKSPACE_ROOT`, native client registration, NPU connection state, and Star state clearly. A missing `gh` channel is reported as "optional channel not configured", never as an installation failure.
