---
name: init
description: Bind MindIE Agent for this Claude Code task (alias of the mindie-agent entry), or show first-use choices.
disable-model-invocation: true
---

The mindie-agent skill is the single entry; this command is its alias.
Present the MindIE status from the UserPromptExpansion hook context. Do not
invent a session id, repository, account, or project scope. Do not call tools
to bind or to enable sharing.

First use offers the one-time choices only if no choice was ever saved:
1. Recommended: contribute public experience for the current project via `/mindie-agent:sharing-enable --repository owner/repo --account USER --project-root /absolute/path --visibility public`.
2. Read-only knowledge; no contribution (`/mindie-agent:init read-only`).
3. Configure later; sharing stays off (`/mindie-agent:init later`).

There is no automatic yes. A saved choice persists for this installation and
is never re-asked. Remote-dev works without this binding.
$ARGUMENTS
