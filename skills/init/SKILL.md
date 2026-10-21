---
name: init
description: Bind MindIE Agent for this Claude Code task (alias of the mindie-agent entry), or show first-use choices.
disable-model-invocation: true
---

The mindie-agent skill is the single entry; this command is its alias.
Present the MindIE status from the UserPromptExpansion hook context. Do not
invent a session id, repository, account, or project scope. Do not call tools
to bind or to enable sharing.

Invoking the entry binds this native task. If configuration is incomplete,
reuse approved values and obtain only the missing destination and scope:
`/mindie-agent contribute --repository owner/repo --account USER
--project-root /absolute/path --visibility public`.
The configured loop captures and processes eligible Stop events automatically.
Binding, configuration and actual processing receipts are distinct facts.
Explicit disable and legacy declined settings stay disabled until changed;
they are not alternative product modes or successful acceptance.
Report missing configuration, an out-of-scope task or a component failure
directly. Do not require a deactivate/reactivate recovery cycle.

Contribution enables both scoped knowledge PRs and sanitized product fault Issues.
When the native Hook returns `reporting.run_outside_hook=true`, execute its
`reporting.command_line` now, outside the Hook, in the same setup flow. Use a
90-second outer timeout. This is part of the user's existing contribution
request; do not ask for another confirmation or leave the command for the user.
Only report the reporter ready when the returned status is `running`. Report
`degraded`, authentication errors, timeouts, and `needs_choice` directly; do not
retry an uncertain external write. Local configuration alone is not delivery.

There is no automatic yes. Reuse the installation's valid saved choice without
another prompt. Only when the entry returns `needs_choice` for legacy
knowledge-only `contribute`, resolve it once using the user's explicit choice; a contribution request already
made in this setup flow needs no further confirmation. Startup does not grant
missing permission. Saved `disabled`, `read-only` and `later` choices remain
unchanged without prompting, and ordinary tasks do not ask again. Remote-dev
works without this binding.

For setup or a GitHub authentication failure, follow [GitHub Sign-In](../mindie-agent/references/github-authentication.md). Reuse existing authentication; offer browser login or enterprise-network token creation/input only when needed.

During installation or first-use setup, present the [user notice](../mindie-agent/references/user-notice.md),
then follow [optional workspace setup](../mindie-agent/references/workspace-setup.md).
Run the workspace offer even when contribution is disabled. Reuse the user's
creation and initialization decisions; approved creation/reuse includes adding
the path to the native client workspace without a second registration question.

$ARGUMENTS
