---
name: init
description: Bind MindIE Agent for this Claude Code task (alias of the mindie-agent entry), or show first-use choices.
disable-model-invocation: true
---

This command is an alias of `/mindie-agent`. Present the MindIE status from the `UserPromptExpansion` hook context.

Invoking the entry binds the current session. If contribution configuration is incomplete, reuse already-approved values and ask only for the missing destination and scope:
`/mindie-agent contribute --repository owner/repo --account USER --project-root /absolute/path --visibility public`.

Community contribution governs both scoped knowledge PRs and sanitized product fault Issues in `mindie-consent.json`. When the hook returns `reporting.run_outside_hook=true`, execute `reporting.command_line` immediately outside the hook in the same setup flow (with a 90-second timeout). Reuse valid saved choices (`contribute`, `disabled`, `read-only`, `later`); if a legacy knowledge-only `contribute` returns `needs_choice`, resolve it once through `/mindie-agent`. Keep local config state, background reporter status (`running` / `degraded`), and external GitHub Issue/PR creation distinct.

For GitHub authentication setup or troubleshooting, follow [GitHub Sign-In](../mindie-agent/references/github-authentication.md). During initial installation or first-use setup, present [用户须知](../mindie-agent/references/user-notice.md) and follow [Workspace Setup](../mindie-agent/references/workspace-setup.md).

$ARGUMENTS
