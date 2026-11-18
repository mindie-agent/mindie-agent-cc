# mindie-agent-cc 0.1.29：静默默认启用架构演进与零前置会话绑定门控

[English](mindie-agent-cc-0.1.29.en.md)

- **`knowledge_*` MCP 默认就绪与零前置绑定门控**：`knowledge` MCP 面启动即暴露 `knowledge_query`、`knowledge_explain`、`knowledge_feedback`、`knowledge_distill`、`knowledge_sync` 与 `mindie_status`；移除 `PreToolUse` 强制 nonce 门控与手动斜杠命令激活前置条件，支持可选 `claim_bind` 提示并平滑回退至连接级 `_CONNECTION_SESSION_ID`（主会话、Claude Code `Task` 子智能体与分支会话开箱即用），在需要活跃租约的路径按需自动初始化会话租约（`not_before=0.0`），同时保留收据诚实性（`receipt_warning` + `receipt_unconfirmed=True`）与只读工具（`readOnlyHint=True`）独立并发线程池（`read_pool`）。
- **轻量被动 Hook 与显式同意优先的 `Stop` 采集快速路径**：移除 `hooks/hooks.json` 中的 `UserPromptExpansion` 斜杠命令展开门控，`PreToolUse` 与 `SessionStart` 保持被动无阻塞且未配置时不创建 SQLite 文件；`Stop` Hook 在打开 `admission.sqlite3` 或拉起采集进程前优先校验 `mindie-consent.json` 与授权 `project_roots`，未配置、已关闭或超出授权目录时在 `< 100ms` 内静默退出且零 SQLite 副作用，仅在授权生效时自动确保会话租约并执行脱敏轨迹采集。
- **语义静默加载 `SKILL.md`（`user-invocable: false`）**：更新 `skills/mindie-agent/SKILL.md` frontmatter 为 `user-invocable: false`，从斜杠菜单隐藏入口并支持围绕昇腾 NPU、vLLM-Ascend、xLLM、MindIE-SD、AscendC、Triton 与 CANN 任务的按需语义加载，同步精简运行时参考与中英文档。
