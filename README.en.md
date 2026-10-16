<p align="center">
  <img src="https://raw.githubusercontent.com/mindie-agent/mindie-agent/main/assets/brand/mindie-agent-logo.png" alt="MindIE Agent logo" width="128" height="128">
</p>

<h1 align="center">MindIE Agent for Claude Code</h1>

<p align="center">
  <b>Native Claude Code Plugin for the Full Ascend NPU AI Infra Lifecycle</b><br/>
  <sub>Architecture Design · Feature Development · AscendC / Triton Kernels · Remote NPU Testing · Silent Experience Loop</sub>
</p>

<p align="center">
  <a href="https://github.com/mindie-agent/mindie-agent-cc/releases"><img src="https://img.shields.io/badge/release-v0.0.1-0F766E.svg" alt="Release v0.0.1"></a>
  <a href="harness-compatibility.json"><img src="https://img.shields.io/badge/Claude_Code-2.1.269_%7C_2.1.299-D97757.svg" alt="Claude Code 2.1.269 | 2.1.299"></a>
  <a href="pyproject.toml"><img src="https://img.shields.io/badge/python-3.13-3776AB.svg" alt="Python 3.13"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-0F766E.svg" alt="License: MIT"></a>
</p>

<p align="center">
  <a href="README.md">中文</a> | <b>English</b> ·
  <a href="#quick-start">Quick Start</a> ·
  <a href="#why-use-this-plugin">What Can It Do?</a> ·
  <a href="#everyday-usage--command-reference">Commands</a> ·
  <a href="#architecture--component-map">Architecture</a> ·
  <a href="docs/README.en.md">Documentation</a>
</p>

---

**MindIE Agent for Claude Code** (`mindie-agent-cc`) is the official native Claude Code plugin for [MindIE Agent](https://github.com/mindie-agent/mindie-agent), providing **a unified Agent capability foundation across the entire Ascend NPU AI Infra workflow — architecture design, feature development, operator implementation, testing & verification, and performance tuning**. Today it includes deep support for **`vLLM-Ascend` development & testing** and **`AscendC` / `Triton` kernel engineering**, with broader Ascend AI Infra domains continuously expanding.

Built on the [MindIE Agent Engineering Design Principles](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.en.md), **domain knowledge and the Skill system are invoked manually on demand via `/mindie-agent`** so they never pollute unrelated tasks, while **`remote-dev` remote NPU development and testing tools trigger automatically by default** in everyday conversation—surfacing every failure or uncertain external state transparently without silent fallbacks.

---

## Why Use This Plugin? (Trigger Modes & Usage Examples)

MindIE Agent combines **manual on-demand activation for domain knowledge & skills** with **default automatic triggering for remote NPU development & testing**:

| Capability | Trigger Mode | When to Use | How to Use |
| :--- | :--- | :--- | :--- |
| **Experience, Knowledge & Skill System**<br/>(`mindie-knowledge` + `mindie-agent` Skill) | **🎯 Manual Trigger** | When designing Ascend AI Infra features, developing in `vLLM-Ascend`, writing `AscendC` / `Triton` kernels, authoring unit/accuracy tests, or tuning performance | Explicitly type **`/mindie-agent` + your task description** (all setup and status actions are also wrapped inside `/mindie-agent`) |
| **Remote Ascend NPU Dev & Testing**<br/>(`remote-dev` MCP toolset) | **⚡ Default Auto-Trigger** | When syncing local code/tests to a remote Ascend server/container, running NPU builds/tests, checking `npu-smi`, or tailing long job logs | **No slash command needed**—just describe the remote task in natural language |
| **Engineering Experience Loop**<br/>(`Stop` Auto-Capture + Gitleaks Redaction) | **🔄 Silent Background Run** | When contributing sanitized design, kernel development, testing, or tuning takeaways back to the open-source knowledge base | **Set once, zero daily friction**—once enabled in setup, every session is automatically redacted and packaged into a knowledge PR in the background |

### Scenario 1: Type `/mindie-agent` for Feature Design, Kernel Development & Test Authoring (Manual Trigger)
> **The Problem**: Designing new Ascend features, implementing `AscendC` / `Triton` operators, or writing `vLLM-Ascend` accuracy tests often requires domain-specific architecture conventions, kernel constraints, and verified test patterns.  
> **Copy-Paste Example**:
> ```text
> /mindie-agent I want to design and implement a custom fused operator in vllm-ascend (with Triton / AscendC kernel support) and add unit + accuracy tests. Please check past experience and architecture conventions to propose an implementation plan.
> ```
> **What Happens**: Typing `/mindie-agent` activates the `mindie-agent` Skill and queries locally synced [`knowledge-vllm-ascend`](https://github.com/mindie-agent/knowledge-vllm-ascend) packages, returning verified engineering cases complete with **applicable environments, verification evidence, and known caveats** as reference material.

### Scenario 2: Everyday Conversation Automatically Triggers Remote Ascend NPU Dev & Testing (`remote-dev` Default Auto-Trigger)
> **The Problem**: Your daily machine is a macOS or Windows laptop, while the Ascend NPU cards live on a remote Linux server or container. Manually syncing code, compiling kernels, and tailing long test logs breaks your flow.  
> **Copy-Paste Example (no slash command required)**:
> ```text
> Connect to the vllm_dev container on my remote 910B server, check current NPU memory usage, and sync my new operator implementation and rotary_embedding tests to run the full pytest verification suite.
> ```
> **What Happens**: The built-in `remote-dev` MCP server is always on and **triggers automatically by default** whenever your prompt involves remote servers, containers, or NPU commands—handling remote file reads/writes, patch application, managed background jobs, and live log polling over SSH. It works completely independently, even if the knowledge service or community sharing is turned off.

### Scenario 3: Silent Background Experience Capture & Community Contribution (Set Once, Zero Friction)
> **The Problem**: After completing a complex architecture design, kernel implementation, or NPU test verification, that engineering insight usually disappears when you close the terminal—yet sharing raw transcripts risks leaking internal IPs, tokens, or file paths.  
> **Copy-Paste Example (just work on your engineering task normally)**:
> ```text
> /mindie-agent Help me complete the build and accuracy alignment verification for this custom Ascend operator using the knowledge base and our remote NPU container, and run the unit test suite.
> ```
> **What Happens**: Community contribution is **off by default**. Once you opt in during initial setup, every time a session finishes (`Stop`), the plugin silently extracts public user/assistant messages in the background, runs local deterministic redaction and Gitleaks scanning, generates navigation metadata, and opens a sanitized knowledge PR—with zero extra steps required during your daily work.
> - Read the **[User & Contribution Notice](skills/mindie-agent/references/user-notice.md)** for full details on sharing scope, redaction limits, and public Git history.
> - To set up a multi-task Git worktree layout for `vllm` and `vllm-ascend`, see **[Workspace Setup](skills/mindie-agent/references/workspace-setup.md)** and [vllm-ascend-workspace](https://github.com/mindie-agent/vllm-ascend-workspace.git).

---

## Quick Start

### Option 1: One-Sentence Install via Your AI Agent (Recommended)

Copy and paste the following prompt directly to Claude Code (or any terminal-capable AI Agent) and let it install and verify the plugin for you:

```text
Please install https://github.com/mindie-agent/mindie-agent-cc for me
```

<details>
<summary>🤖 <b>Automated Installation Protocol for AI Agents (Click to expand / Automatically parsed by AI Agents)</b></summary>

If you are an AI Agent asked by the user to install this plugin, run the following deterministic steps directly in the terminal **without asking the user to read the documentation manually**:

1. **Check prerequisites and host version**:
   ```sh
   git --version
   uv --version
   claude --version
   ```
   - Verify `claude --version` matches one of the exact versions supported by release `v0.0.1`: `2.1.269` or `2.1.299` (declared in [`harness-compatibility.json`](harness-compatibility.json)).
   - On **Linux or Windows WSL**, if the background knowledge service will be used, also run `systemctl --user show --property=Version` to verify `systemd >= 254` is reachable (inside WSL, use Linux-native `claude`, `git`, and `uv`).

2. **Add the marketplace, install the plugin, and initialize the locked runtime**:
   ```sh
   claude plugin marketplace add "https://github.com/mindie-agent/mindie-agent-cc.git#v0.0.1"
   claude plugin install mindie-agent@mindie-agent-cc --scope user
   claude --init-only
   ```
   > Note: `claude --init-only` runs `SessionStart` without starting a conversation or calling a model, allowing `uv` to provision Python 3.13 and install the locked runtime ahead of time.

3. **Verify installation and MCP server health**:
   ```sh
   claude plugin list --json
   claude mcp list
   ```
   - Confirm `claude plugin list --json` shows `mindie-agent@mindie-agent-cc` (`0.0.1`) enabled with `user` scope.
   - Confirm `claude mcp list` reports both `mindie-knowledge` and `remote-dev` as `Connected`, then report completion to the user.

</details>

### Option 2: One-Line Terminal Install (Manual)

To install manually from a macOS / Linux / WSL shell or native Windows PowerShell:

```sh
claude plugin marketplace add "https://github.com/mindie-agent/mindie-agent-cc.git#v0.0.1" && \
claude plugin install mindie-agent@mindie-agent-cc --scope user && \
claude --init-only && \
claude mcp list
```

#### Environment Notes
- **Prerequisites**: `git`, `uv`, and an authenticated Claude Code (`v0.0.1` is verified against Claude Code `2.1.269` and `2.1.299`; see [`harness-compatibility.json`](harness-compatibility.json)).
- **Native Windows**: Git for Windows provides the SSH client used by `remote-dev`, preserving your existing SSH and model settings.
- **Linux & WSL**: Inside WSL, use Linux-native `claude`, `git`, and `uv` with your profile and runtime stored on the Linux filesystem. Service-backed knowledge operations on Linux/WSL require a reachable `systemd --user` manager (`systemd >= 254` with unified cgroup v2); if unavailable, the knowledge service reports an explicit error rather than running unprotected, while `remote-dev` continues to work normally.

---

## Everyday Usage & Command Reference (One Unified `/mindie-agent` Entry)

To keep daily engineering effortless, MindIE Agent wraps all interactions into **a single unified entrypoint: `/mindie-agent`**. You **never need to memorize internal subcommands or flags**—operational context is progressively disclosed only to the Agent when needed:

| Scenario | What You Type | What Happens Behind the Scenes |
|---|---|---|
| **Ascend AI Infra Design, Dev (`vLLM-Ascend` / `AscendC` / `Triton`), Testing & Tuning** | `/mindie-agent <describe your design, development, or testing task>` | Activates Ascend Skill guidance and local `mindie-knowledge` search, working alongside `remote-dev` to implement, test, and verify on real NPUs |
| **First-Time Setup, Checking Status, or Changing Sharing Settings** | `/mindie-agent` (or `/mindie-agent check status`, `/mindie-agent enable/disable community sharing`) | Your Agent reads the progressively disclosed context to display status or update preferences; saved choices persist across sessions and updates |
| **Everyday Remote Ascend Server / Container Work** | **No slash command needed**—just use natural language | Always-on `remote-dev` MCP tools trigger automatically to read/edit remote files, apply patches, and poll background jobs |

### Silent Updates & Safe Uninstall

- **Silent Compatible Updates**: More than 24 hours after the last successful check, the next natural MCP tool call triggers a non-blocking background check for the highest compatible Release declared in [`harness-compatibility.json`](harness-compatibility.json). The new `uv` runtime is built in isolation and switched atomically when the service is idle, preserving all user preferences.
- **Safe Uninstall**: Close active sessions using the plugin, then uninstall via Claude Code while keeping persistent runtime data for clean shutdown:
  ```sh
  claude plugin uninstall mindie-agent@mindie-agent-cc --scope user --keep-data --json
  ```

---

## Architecture & Component Map

`mindie-agent-cc` is designed as a **thin native adapter**: it handles Claude Code plugin manifests, hook bridging, public transcript extraction, and the immutable Python 3.13 runtime lifecycle, while delegating domain knowledge, remote execution, and diagnostics to shared components.

```mermaid
flowchart LR
  CC["Claude Code Host<br/>(Hooks / MCP stdio / Plugin CLI)"] --> Entry["mindie-agent-cc<br/>(Native Bridge & Locked Runtime Entry)"]
  Entry --> Knowledge["mindie-agent/knowledge<br/>(Search, Redaction, Capture & PR Loop)"]
  Knowledge --> Feed["mindie-agent/knowledge-vllm-ascend<br/>(vLLM-Ascend Knowledge Feed)"]
  Entry --> RemoteDev["mindie-agent/remote-dev<br/>(SSH Remote Ascend Build, Test & Poll)"]
  Entry --> Diagnostics["mindie-agent/diagnostics<br/>(Local Receipts & Opt-In Fault Reporter)"]
```

| Repository / Component | Responsibility |
|---|---|
| [`mindie-agent/mindie-agent`](https://github.com/mindie-agent/mindie-agent) | Top-level architecture, MindIE Agent Engineering Design Principles, domain boundaries, and paired-release governance. |
| `mindie-agent/mindie-agent-cc` (this repo) | Claude Code native plugin manifest (`.claude-plugin/`, `.mcp.json`, `hooks/`, `skills/`), task identity bridge, public transcript parser, and immutable runtime lifecycle (`scripts/`). |
| [`mindie-agent/knowledge`](https://github.com/mindie-agent/knowledge) | Shared knowledge core: local SQLite/FTS5 search, deterministic redaction, capture queue, block-index scheduling, and GitHub PR publication reconciliation. |
| [`mindie-agent/knowledge-vllm-ascend`](https://github.com/mindie-agent/knowledge-vllm-ascend) | Curated `vllm-ascend` domain knowledge feed (pinned by contract SHA-256 and verified commit in [`product-contract.json`](product-contract.json)). |
| [`mindie-agent/remote-dev`](https://github.com/mindie-agent/remote-dev) | Bounded remote command execution, workspace synchronization, and background job polling over SSH for Ascend servers. |
| [`mindie-agent/diagnostics`](https://github.com/mindie-agent/diagnostics) | Bounded local diagnostic retention and optional sanitized GitHub Issue fault reporting. |

### Local Storage Layout

- **Immutable Runtime Directory**: `${XDG_DATA_HOME:-$HOME/.local/share}/mindie-agent/runtimes/cc` on macOS/Linux/WSL, or `%LOCALAPPDATA%/mindie-agent/runtimes/cc` on Windows (indexed by source, lockfile, Python ABI, and platform).
- **Persistent Release Entry**: `CLAUDE_PLUGIN_DATA/releases/0.0.1/entry.py` (decoupled from volatile plugin caches so long-lived sessions remain stable).
- **Adapter Configuration**: `${XDG_CONFIG_HOME:-$HOME/.config}/mindie-agent/cc.json` (records the active installation binding; user consent and contribution scope are stored separately as persistent user data).

---

## Developer Verification

To run the locked component test suite from the repository root (requires a local checkout of `mindie-agent/knowledge` at the exact commit pinned in [`uv.lock`](uv.lock)):

```sh
uv sync --locked --no-editable --group test
MINDIE_TEST_KIMI_SCRIPTS="$PWD/tests/fixtures/kimi-86de2c3/scripts" \
MINDIE_TEST_KNOWLEDGE_CHECKOUT=/absolute/path/to/declared-knowledge-checkout \
uv run --locked --no-editable --group test python -m pytest -q
```

Validate the product contract and Harness interface registry:

```sh
uv run --locked --no-editable --group test python scripts/product_contract.py --source .
uv run --locked --no-editable --group test python scripts/harness.py --source . --tests tests --revision "$(git rev-parse HEAD)"
```

---

## Documentation Index

Detailed engineering guides and verification specifications are available in [`docs/`](docs/README.en.md) in both English and Chinese:

- **[Documentation Index (English)](docs/README.en.md)** | **[文档索引（中文）](docs/README.md)**
- **Claude Code Native Boundary**: [English](docs/native-boundary.md) | [中文](docs/native-boundary.zh.md) — Task identity binding, `PreToolUse` / `Stop` hook mechanics, and shared component boundaries.
- **Harness Interface Evidence & Fork Attribution**: [English](docs/harness-interface-evidence.md) | [中文](docs/harness-interface-evidence.zh.md) — Verified Claude Code interfaces, structured-output retry boundaries, and `--fork-session` attribution.
- **Service Continuity & Smooth Updates**: [English](docs/service-continuity.md) | [中文](docs/service-continuity.zh.md) — Immutable `uv` runtime preparation, shared/exclusive locking, idle service handoff, and `Stop` queue durability.
- **Failure Diagnostics & Local Cancellation**: [English](docs/failure-diagnostics.md) | [中文](docs/failure-diagnostics.zh.md) — Read-only status checks, local process cleanup on cancel, and opt-in product fault reporting.
- **Framework Stability & Test Contracts**: [English](docs/framework-stability.md) | [中文](docs/framework-stability.zh.md) — Persistent user preferences, Windows atomic config I/O, and reproducible test boundaries.

---

## License

Released under the [MIT License](LICENSE).
