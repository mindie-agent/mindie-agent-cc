---
name: recover
description: Optional explicit inspection of one contribution batch, not ordinary recovery of a transient local or network failure.
disable-model-invocation: true
---

Use this only when the user explicitly invokes it. A transient local or network sharing failure is recovered by the existing worker; sharing status is how a problem is seen. Authentication, trust, rejected content, or invalid configuration can need an explicit user or operator action.

The UserPromptExpansion hook names exactly one core CLI command. Do not run recovery inside the hook.

If the hook result includes `command_line`, run that one command with Bash once, then show its output. Do not chain inspect+reconcile+retry+compact. Do not retry an unknown write; reconcile first. Compact only a confirmed batch. This does not rerun the organizer.

Examples:
- `/mindie-agent:recover --batch ID inspect`
- `/mindie-agent:recover --batch ID reconcile`
- `/mindie-agent:recover --batch ID retry`
- `/mindie-agent:recover --batch ID compact`
$ARGUMENTS
