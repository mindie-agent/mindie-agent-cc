---
name: reporting-enable
description: reporting-enable for independent product failure reporting.
disable-model-invocation: true
---

Present the reporting result from the UserPromptExpansion Hook. If the native result returns run_outside_hook=true, execute the returned `command_line` once outside the Hook (`command` is the same argument vector). Report readiness from the result; on failure report degraded and do not retry. Knowledge contribution is a separate choice.
$ARGUMENTS
