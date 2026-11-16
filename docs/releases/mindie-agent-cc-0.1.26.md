# mindie-agent-cc 0.1.26

- **确定性工具零前置 `PreToolUse` 绑定门禁（`P2.7`）**：`scripts/identity.py` 引入 `MissingBind`，仅在无 `PreToolUse` 记录（如子 Agent 或无 Hook 场景）时将 `components` 与 `remote` 平滑回退至连接级稳定标识 `MINDIE_MCP_CONNECTION_ID`；对已发布绑定的参数篡改、会话冲突与重放仍严格拒绝，`knowledge` 继续强制原生会话与 `/mindie-agent` 授权门禁（`P2.1`）。
- **提示词与文档对齐**：清理并润色 `SKILL.md` 与 `AGENTS.md`，对齐设计原则五大铁律。
