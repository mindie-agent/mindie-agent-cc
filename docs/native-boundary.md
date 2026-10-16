# Claude Code native boundary

[中文](native-boundary.zh.md) | English · [Documentation Index](README.en.md)

Status: the framework stability implementation passed local review on 2026-09-28. The normal generated package, pinned interpreter and two native read-only entries were verified in an isolated macOS profile. Full contribution and Windows host acceptance remain separate; see [framework stability](framework-stability.md).

This adapter inherits all nine [VAWS design principles](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.md). It translates native events into the shared runtime; it does not add a second knowledge engine or harness.

## Entry and identity

The mindie-agent entry (bare or namespaced; `/mindie-agent:init` is its alias) provides explicit setup and status. Its `UserPromptExpansion` event supplies the current task and working directory. Ordinary tasks do not need an entry invocation: knowledge reads use the native tool binding below, while approved capture associates an eligible Stop internally.

Before a tool call, Claude's `PreToolUse` event supplies the native `tool_use_id`, task, tool name and input. The ensuing MCP request carries the same ID in `_meta["claudecode/toolUseId"]`. The adapter binds a call to its native task without asking the model to create identity parameters. The binding is consumed once and covers the exact tool and argument digest. Missing or mismatched bindings fail closed. New tasks and forks receive their own bindings and never inherit a parent task's capability, job ownership or collectable history.

The explicit entry reuses approved configuration and reports only missing
public destination, account and project scope. It does not offer read-only or
later product modes. Task binding alone does not establish capture readiness;
actual processing is evidenced by capture and contribution receipts. Explicit
disable and saved legacy declines remain disabled until changed. Failures never
revoke binding.

## Stop and records

The Stop hook returns silently within its short handoff budget. It never returns a continuation request to the business model. With sharing disabled, it performs no transcript read, capture-store creation or organizer call. An eligible Stop verifies the named task file inside the selected Claude profile, derives scope and creation time from that artifact, checks saved contribution authority, and associates the task without undoing an explicit deactivation. Shared admission and deduplication govern collection. The background worker owns indexing; captured material is not evidence that indexing or publication completed, and uncertain model calls are never silently replayed.

Only visible current-task user and assistant text is eligible. Claude records use `sessionId`, timestamps and public content blocks. Thinking blocks, tool payloads, injected `isMeta` messages, attachments, system records and sidechains are excluded. The capture boundary is no earlier than both the task creation time and the saved contribution enablement time.

## Shared behavior and updates

The shared knowledge component owns retrieval, redaction, organization reservations, feedback, publication receipts and cleanup after exact GitHub confirmation. The adapter owns Claude invocation, transcript translation and native installation. Remote development stays usable without knowledge activation.

An update selects one complete source/interpreter/config generation. Active operations finish under their generation lock before switching. Idle task authorizations are preserved and do not block updates. Failed candidates keep the previous generation; the same failed candidate is not repeatedly installed by the timer. Native plugin installation and readback determine installation state; private Claude registries and trust state are never forged.

## Native evidence

The initial capability probe used a real local Claude Code 2.1.269 process, configured model `deepseek-flash`, effort `high`, task `645fae2e-ef23-4e7d-9559-f4dd0cb9db6a`. It completed in 2.8 seconds with no retries. UserPromptExpansion, PreToolUse, MCP metadata correlation and a single silent Stop were observed. This establishes the host integration seam only; it is not a completed knowledge or update loop.

Official references: [hooks](https://code.claude.com/docs/en/hooks), [plugins](https://code.claude.com/docs/en/plugins-reference), [Skills](https://code.claude.com/docs/en/skills).
