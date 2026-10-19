---
name: sharing-enable
description: Compatibility alias of the unified entry's contribute choice (public community contribution for authorized project roots).
disable-model-invocation: true
---

Alias kept for compatibility: the unified entry's contribute choice is the
same operation — `/mindie-agent contribute --repository owner/repo
--account USER --project-root /absolute/path --visibility public`.

Required arguments: --repository owner/repo --account USER --project-root /absolute/path --visibility public
Optional: --branch main --fork owner/repo

Present the sharing result from the UserPromptExpansion hook context. Destination comes from this slash command's native arguments, not a replacement you invent. Sharing stays off until this explicit public opt-in succeeds.
Contribution enables both scoped knowledge PRs and sanitized product fault Issues.
When the native Hook returns `reporting.run_outside_hook=true`, execute its
`reporting.command_line` now, outside the Hook, in the same setup flow. Use a
90-second outer timeout. This is part of the user's existing contribution
request; do not ask for another confirmation or leave the command for the user.
Only report the reporter ready when the returned status is `running`. Report
`degraded`, authentication errors, timeouts, and `needs_choice` directly; do not
retry an uncertain external write. Local configuration alone is not delivery.

$ARGUMENTS
