---
name: mindie-agent
description: The single MindIE Agent entry. Invoke it once in a vLLM-Ascend task to bind the task internally and use knowledge, optional community sharing, and remote-dev. One-time setup persists for the installation and is never re-asked.
disable-model-invocation: true
---

This skill is the only entry a user needs; Claude Code may show it bare or
namespaced — both are the same entry. Present the MindIE status from the
UserPromptExpansion hook context. Do not invent a session id, repository,
account, or project scope. Do not call tools to bind or to enable sharing.

Invoking the entry binds the current task internally and automatically,
reusing the saved install-level choice. First use offers the one-time
choices only if no choice was ever saved, and every choice completes
through this same entry:
1. Recommended: contribute public experience for the current project — the
   user names the public destination in ordinary conversation and invokes
   the entry with `contribute --repository owner/repo --account USER
   --project-root /absolute/path --visibility public`.
2. Read-only knowledge; no contribution (`/mindie-agent read-only`).
3. Configure later; sharing stays off (`/mindie-agent later`).

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
