# MindIE Agent for Claude Code

Claude Code adapter for MindIE Agent. This repository owns the native plugin entry, task identity, hooks, transcript parsing, installation and runtime switching. Shared knowledge and remote development stay in their own repositories.

Development is in progress. This is not a released or fully accepted implementation.

MindIE Agent inherits all nine [VAWS design principles](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.md). See the [Harness boundary and lifecycle contract](https://github.com/mindie-agent/mindie-agent/blob/main/docs/harness-boundary-and-lifecycle.md).

Acceptance requires actual local Claude Code runs and observed external outcomes. Passing unit tests or fabricated hook input does not establish native acceptance. macOS is the first real host; Windows desktop acceptance remains pending.
