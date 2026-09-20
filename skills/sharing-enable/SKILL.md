---
name: sharing-enable
description: Opt in to public community contribution for authorized project roots.
disable-model-invocation: true
---

Required arguments: --repository owner/repo --account USER --project-root /absolute/path --visibility public
Optional: --branch main --fork owner/repo

Present the sharing result from the UserPromptExpansion hook context. Destination comes from this slash command's native arguments, not a replacement you invent. Sharing stays off until this explicit public opt-in succeeds.
$ARGUMENTS
