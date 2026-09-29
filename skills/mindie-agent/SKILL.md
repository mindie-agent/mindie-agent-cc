---
name: mindie-agent
description: The single MindIE Agent entry. Invoke it once in a vLLM-Ascend task to bind the task internally and use knowledge, optional community sharing, and remote-dev. One-time setup persists for the installation and is never re-asked.
disable-model-invocation: true
---

This skill is the only entry a user needs; Claude Code may show it bare or
namespaced — both are the same entry. Present the MindIE status from the
UserPromptExpansion hook context. Do not invent a session id, repository,
account, or project scope. Do not call tools to bind or to enable sharing.

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

The first setup also offers the independent reporting choice once
(`/mindie-agent reporting-enable`, `reporting-disable`, or
`reporting-later`); it is separate from knowledge contribution, an
installer default-off is not a decision, and any recorded decision
persists without being re-asked.

There is no automatic yes. A saved choice persists across sessions, forks,
restarts, upgrades and failures — it is never re-asked, and failure counts
never revoke it. Binding has no activate/lease/recover step for the user.
Remote-dev works without the entry. Transient failures are recovered by the
existing background worker, including one bounded retry of a
deadline-interrupted region.
$ARGUMENTS
