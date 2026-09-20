# Claude Code native boundary

Status: implementation in progress; host identity capability observed on macOS with Claude Code 2.1.269. Full adapter acceptance remains pending.

This adapter inherits all nine [VAWS design principles](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.md). It translates native events into the shared runtime; it does not add a second knowledge engine or harness.

## Entry and identity

A user invokes `/mindie-agent:init`. Claude's `UserPromptExpansion` event supplies the current task and working directory; the adapter activates only this task. The entry Skill disables automatic model invocation. Generic prompts, knowledge tool discovery and remote work do not grant knowledge access.

Before a tool call, Claude's `PreToolUse` event supplies the native `tool_use_id`, task, tool name and input. The ensuing MCP request carries the same ID in `_meta["claudecode/toolUseId"]`. The adapter can therefore bind a call to its native task without asking the model to create identity parameters. The binding is consumed once and covers the exact tool and argument digest. Missing or mismatched bindings fail closed. New tasks and forks require their own explicit activation.

The first explicit entry offers contribution, read-only use, or later configuration. Contribution requires a user choice and an explicit public repository, account and project scope. The choice persists. Task authorization and sharing permission are different: read-only use does not enable collection.

## Stop and records

The Stop hook returns silently within its short time budget. It never returns a continuation request to the business model. With sharing disabled, it performs no transcript read, capture-store creation or organizer call. Shared admission, deduplication, bounded work and failure pause govern authorized collection.

Only the public current-task user, assistant and tool material is eligible. Claude records use `sessionId`, timestamps and public content blocks. Thinking blocks, injected `isMeta` messages, attachments, system records and sidechains are excluded. The activation boundary prevents inherited or earlier material from being treated as newly authorized.

## Shared behavior and updates

The shared knowledge component owns retrieval, redaction, organization reservations, feedback, publication receipts and cleanup after exact GitHub confirmation. The adapter owns Claude invocation, transcript translation and native installation. Remote development stays usable without knowledge activation.

An update selects one complete source/interpreter/config generation. Active operations finish under their generation lock before switching. Idle task authorizations are preserved and do not block updates. Failed candidates keep the previous generation; the same failed candidate is not repeatedly installed by the timer. Native plugin installation and readback determine installation state; private Claude registries and trust state are never forged.

## Native evidence

The initial capability probe used a real local Claude Code 2.1.269 process, configured model `deepseek-flash`, effort `high`, task `645fae2e-ef23-4e7d-9559-f4dd0cb9db6a`. It completed in 2.8 seconds with no retries. UserPromptExpansion, PreToolUse, MCP metadata correlation and a single silent Stop were observed. This establishes the host integration seam only; it is not a completed knowledge or update loop.

Official references: [hooks](https://code.claude.com/docs/en/hooks), [plugins](https://code.claude.com/docs/en/plugins-reference), [Skills](https://code.claude.com/docs/en/skills).
