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

## Progressive Context Disclosure (Read Only on Demand)

Do **not** load all reference files during ordinary troubleshooting. Read the relevant reference file **only** when the current turn requires it:

- **First-time onboarding, incomplete contribution configuration (`contribute`), the unified contribution/reporting switch, or capture receipts**: read [Setup & Operations](references/setup-and-operations.md).
- **Sharing scope, local redaction boundaries, or public Git history questions**: read [用户须知](references/user-notice.md).
- **Setting up a local `vllm` + `vllm-ascend` multi-task Git worktree workspace**: read [Workspace Setup](references/workspace-setup.md).

$ARGUMENTS

Contribution enables both scoped knowledge PRs and sanitized product fault Issues.
When the native Hook returns `reporting.run_outside_hook=true`, execute its
`reporting.command_line` now, outside the Hook, in the same setup flow. Use a
90-second outer timeout. This is part of the user's existing contribution
request; do not ask for another confirmation or leave the command for the user.
Only report the reporter ready when the returned status is `running`. Report
`degraded`, authentication errors, timeouts, and `needs_choice` directly; do not
retry an uncertain external write. Local configuration alone is not delivery.
