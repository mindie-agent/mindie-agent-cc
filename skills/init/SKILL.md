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

The independent reporting choice is offered once with the first setup
(`/mindie-agent reporting-enable`, `reporting-disable`, or
`reporting-later`); any recorded decision persists.

There is no automatic yes. A saved choice persists for this installation and
is never re-asked. Remote-dev works without this binding.
$ARGUMENTS
