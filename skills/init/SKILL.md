---
name: init
description: Explicitly activate MindIE Agent for this Claude Code task, or show first-use choices.
disable-model-invocation: true
---

Present the MindIE status from the UserPromptExpansion hook context. Do not invent a session id, repository, account, or project scope. Do not call tools to activate or to enable sharing.

First use offers three choices:
1. Recommended: contribute public experience for the current project via `/mindie-agent:sharing-enable --repository owner/repo --account USER --project-root /absolute/path --visibility public`.
2. Read-only knowledge; no contribution (`/mindie-agent:init read-only`).
3. Configure later; sharing stays off (`/mindie-agent:init later`).

There is no automatic yes. Remote-dev works without this activation.
$ARGUMENTS
