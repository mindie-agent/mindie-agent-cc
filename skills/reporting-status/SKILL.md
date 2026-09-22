---
name: reporting-status
description: reporting-status for independent product failure reporting.
disable-model-invocation: true
---

Present the reporting result from the UserPromptExpansion Hook. Do not start a reporter, retry, activate knowledge, or change community contribution. Knowledge contribution is a separate choice.
`not_configured` refers only to the shared user-level automatic-upload settings; local logging is independent, so report only returned fields and do not infer from empty `recent` or missing reporting configuration that no logs were produced.
$ARGUMENTS
