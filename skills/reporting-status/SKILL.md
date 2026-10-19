---
name: reporting-status
description: reporting-status for product failure reporting under the contribution switch.
disable-model-invocation: true
---

Present the reporting result from the UserPromptExpansion Hook. Do not start a reporter, retry, activate knowledge, or change community contribution. Knowledge sharing and fault uploads use the same contribution choice.
`not_configured` refers only to the shared user-level automatic-upload settings; local logging is independent, so report only returned fields and do not infer from empty `recent` or missing reporting configuration that no logs were produced.
$ARGUMENTS
