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
$ARGUMENTS
