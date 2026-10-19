---
name: mindie-agent
description: Unified MindIE Agent entry for Ascend / vLLM-Ascend knowledge, skills, remote-dev, and preferences. Once community contribution is enabled, experience capture runs automatically by default at session end without per-task prompts.
disable-model-invocation: true
---

# MindIE Agent (Unified Entry)

Users interact with MindIE Agent through a single `/mindie-agent` entry (while `remote-dev` MCP tools trigger automatically by default when remote servers or NPU containers are involved):

1. **Domain Knowledge & Troubleshooting**: When the user asks an Ascend or `vLLM-Ascend` engineering question, use the native knowledge tools (`knowledge_query`, `knowledge_explain`, `knowledge_feedback`) and remote execution tools (`mindie-remote-dev`) directly. Knowledge entries are reference material—evaluate applicability against the user's actual environment.
2. **Status & Hook Context**: When `/mindie-agent` is invoked explicitly for status or setup, present the MindIE status from the `UserPromptExpansion` hook context. Never invent a session ID, repository, account, or project scope.
3. **Automatic Experience Loop by Default**: Once community contribution is configured and enabled for the project scope, eligible native `Stop` events are captured, redacted, and packaged automatically in the background—users never need to ask to save experience at the end of a session.

4. **Unified Contribution & Fault-Reporting Switch**: Community knowledge sharing and sanitized product fault reporting share a single persistent choice in `<adapter-config-directory>/mindie-consent.json`. When the native Hook returns `reporting.run_outside_hook=true`, execute its `reporting.command_line` immediately outside the Hook in the same setup flow (using a 90-second outer timeout) without asking for another confirmation or leaving the command for the user. Reuse a valid saved choice without another prompt. Resolve legacy `needs_choice` once through `/mindie-agent` using the user's explicit contribution choice, never as separate knowledge/reporting approvals or a per-task prompt. Keep three states strictly separate: (1) local consent configuration saved, (2) background reporter worker running (`reporting.status == "running"`), and (3) external GitHub Issue/PR created. Report `degraded`, authentication errors, timeouts, and `needs_choice` directly, and never retry an uncertain external write.

Resolve the installation's base adapter configuration first (`base_config` for a runtime binding); its parent contains `mindie-consent.json`. Use the existing entry to change the choice, never a guessed profile path.

## Progressive Context Disclosure (Read Only on Demand)

Do **not** load all reference files during ordinary troubleshooting. Read the relevant reference file **only** when the current turn requires it:

- **First-time onboarding, incomplete contribution configuration (`contribute`), the unified contribution/reporting switch, or capture receipts**: read [Setup & Operations](references/setup-and-operations.md).
- **Sharing scope, local redaction boundaries, or public Git history questions**: read [用户须知](references/user-notice.md).
- **Setting up a local `vllm` + `vllm-ascend` multi-task Git worktree workspace**: read [Workspace Setup](references/workspace-setup.md).

$ARGUMENTS
