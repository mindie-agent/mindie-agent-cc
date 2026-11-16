---
name: init
description: Compatibility alias of /mindie-agent to enable knowledge for the current Claude Code session.
disable-model-invocation: true
---

This command is a compatibility alias of the unified `/mindie-agent` entry. Present the MindIE status from the `UserPromptExpansion` hook context.

Invoking `/mindie-agent` enables knowledge and background capture for the current session (deterministic `remote-dev` and `components` tools are already available without it). Reuse saved choices (`contribute`, `disabled`, `read-only`, `later`); if contribution configuration is incomplete and the user asked to enable sharing, ask only for the missing destination and scope. When the hook returns `reporting.run_outside_hook=true`, run `reporting.command_line` outside the hook with a `90s` timeout. Keep local config state, background reporter health, and external GitHub Issue/PR creation distinct.

For GitHub sign-in or first-use workspace setup, see [GitHub Sign-In](../mindie-agent/references/github-authentication.md), [用户须知](../mindie-agent/references/user-notice.md), and [Workspace Setup](../mindie-agent/references/workspace-setup.md).

$ARGUMENTS
