---
name: mindie-agent
description: Use MindIE knowledge and remote-dev when relevant. Approved current-task capture runs automatically within the saved project scope. The entry provides status and configuration; ordinary tasks need no activation command.
disable-model-invocation: true
---

Claude Code may show this entry bare or namespaced. When invoked explicitly,
present the MindIE status from the UserPromptExpansion hook context. Do not
invent a session id, repository, account, or project scope. Knowledge reads and
remote work are available through their native tool identities without invoking
this entry. Use them when relevant; knowledge remains reference material.

If contribution configuration is incomplete,
reuse approved values and obtain only the missing destination and scope:
`/mindie-agent contribute --repository owner/repo --account USER
--project-root /absolute/path --visibility public`.
The configured loop automatically associates and captures eligible native Stop
events using the current task's own transcript and approved project scope.
Configuration and actual processing receipts are distinct facts.
Explicit disable and legacy declined settings stay disabled until changed;
they are not alternative product modes or successful acceptance.
Report missing configuration, an out-of-scope task or a component failure
directly. Do not require a deactivate/reactivate recovery cycle.

[用户须知](references/user-notice.md) explains visible-message sharing,
redaction limits, model-provider processing and public Git history.

During installation or first-use setup, present the user notice, then follow
[optional workspace setup](references/workspace-setup.md): recommend the workspace,
ask whether and where to create it, and after creation ask whether to initialize it.
Reuse prior answers; offer this during setup rather than ordinary task execution.

The first setup also offers the independent reporting choice once
(`/mindie-agent reporting-enable`, `reporting-disable`, or
`reporting-later`); it is separate from knowledge contribution, an
installer default-off is not a decision, and any recorded decision
persists without being re-asked.

There is no automatic yes. A saved choice persists across sessions, forks,
restarts, upgrades and failures — it is never re-asked, and failure counts
never revoke it. Ordinary tasks have no activate/lease/recover step. An explicit
task deactivation remains in force. The existing worker reports processing
failures separately from captured material and preserves billed or uncertain
model outcomes; do not replay a model invocation to recover its missing receipt.
$ARGUMENTS
