---
name: recover
description: Inspect, reconcile, retry a proven-failed contribution, or compact a confirmed batch.
disable-model-invocation: true
---

The UserPromptExpansion hook names exactly one core CLI command. Do not run recovery inside the hook.

If the hook result includes `command_line`, run that one command with Bash once, then show its output. Do not chain inspect+reconcile+retry+compact. Do not retry an unknown write; reconcile first. Compact only a confirmed batch. This does not rerun the organizer.

Examples:
- `/mindie-agent:recover --batch ID inspect`
- `/mindie-agent:recover --batch ID reconcile`
- `/mindie-agent:recover --batch ID retry`
- `/mindie-agent:recover --batch ID compact`
$ARGUMENTS
