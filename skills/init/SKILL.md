---
name: init
description: Bind MindIE Agent for this Claude Code task (alias of the mindie-agent entry), or show first-use choices.
disable-model-invocation: true
---

The mindie-agent skill is the single entry; this command is its alias.
Present the MindIE status from the UserPromptExpansion hook context. Do not
invent a session id, repository, account, or project scope. Do not call tools
to bind or to enable sharing.

First use offers the one-time choices only if no choice was ever saved, and
every choice completes through the same entry:
1. Recommended: contribute public experience for the current project —
   `/mindie-agent contribute --repository owner/repo --account USER
   --project-root /absolute/path --visibility public`.
2. Read-only knowledge; no contribution (`/mindie-agent read-only`).
3. Configure later; sharing stays off (`/mindie-agent later`).

The independent reporting choice is offered once with the first setup
(`/mindie-agent reporting-enable`, `reporting-disable`, or
`reporting-later`); any recorded decision persists.

There is no automatic yes. A saved choice persists for this installation and
is never re-asked. Remote-dev works without this binding.
$ARGUMENTS
