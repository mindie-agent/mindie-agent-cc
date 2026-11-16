# mindie-agent-cc 0.1.26

- **Zero pre-call `PreToolUse` binding gate on deterministic tools (`P2.7`)**: Introduces `MissingBind` in `scripts/identity.py` so `components` and `remote` fall back to connection-stable `MINDIE_MCP_CONNECTION_ID` when no `PreToolUse` bind row exists (e.g., subagents), while still rejecting tampered, contradictory, or replayed binds and keeping `knowledge` strictly gated (`P2.1`).
- **Prompt and guide alignment**: Polishes `SKILL.md` and `AGENTS.md` in alignment with the 5 Iron Rules.
