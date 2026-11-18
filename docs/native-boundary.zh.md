# 原生插件与运行时边界

<b>中文</b> | <a href="native-boundary.md">English</a> · <a href="README.md">返回文档索引</a>

`mindie-agent-cc` 遵循 [MindIE Agent 工程设计原则](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.md)，定位为轻量的 Claude Code 原生插件适配层。它负责将 Claude Code 的插件生命周期、Hook 事件和公开对话记录桥接到共享运行时，不在本仓库内复制知识引擎或维护第二套状态库。

---

## 1. 入口与任务身份自动绑定

### 全量工具静默默认就绪与语义 Skill 加载
`mindie-agent-cc` 将默认就绪的研发工具集与显式授权的经验采集清晰分层：
- **全量 MCP 服务默认就绪（`knowledge`、`remote`、`components`）**：插件启用期间在主会话、`Task` 子智能体与 `--fork-session` 分支会话中开箱即用，无需输入斜杠命令。只读检索（`knowledge_query`、`knowledge_explain`、`mindie_status`）绝不创建空的 `admission.sqlite3`；`knowledge_distill` 与作用域内 `knowledge_feedback` 在未显式停用时按需自动初始化会话租约（`not_before=0.0`）。
- **静默语义 Skill（`skills/mindie-agent/SKILL.md`）**：声明 `user-invocable: false`，从斜杠菜单隐藏命令入口，仅在涉及昇腾 NPU 研发任务时由 Claude 按需语义加载。

### 可选 `PreToolUse` 身份提示与连接级回退
在主会话工具调用前，被动的 `PreToolUse` Hook 记录原生 `tool_use_id`、会话 ID、工具名称与参数摘要，紧随其后的 MCP 请求携带 `_meta["claudecode/toolUseId"]`：
- 若存在匹配的 `PreToolUse` 绑定记录，`mcp_server.py` 单次消费并严格校验工具名与参数摘要（拒绝重放或篡改绑定）。
- 若未触发 `PreToolUse`（如 `Task` 子智能体或直接 MCP 调用），`mcp_server.py` 平滑回退至已验证的 `_meta`/环境会话 ID 或连接级 `_CONNECTION_SESSION_ID`。
- 新建会话与 `--fork-session` 分支会话各自拥有独立会话身份，同时共享 `REMOTE_DEV_STATE_DIR`（`state_dir() / "remote"`）以支持子智能体轮询或停止父会话 `job_id`。

---

## 2. `Stop` Hook 与公开对话提取

### 快速、同意优先的 `Stop` 交接
`Stop` Hook 在极短的本地交接窗口内静默返回中性结果，绝不向业务大模型返回“继续执行”指令：
- **当社区贡献未配置、已关闭或 `cwd` 超出授权 `project_roots` 时**：`Stop` 先校验 `mindie-consent.json` 与 `community_config`，在 `< 100ms` 内直接短路返回 `{}`——不打开或创建 `admission.sqlite3`、不读取对话文件、不创建采集记录，也不发起任何整理模型调用。
- **当社区贡献已开启且当前项目处于授权 `project_roots` 内时**：`Stop` 校验当前 Claude Profile 下的会话文件、按需自动确保会话租约（`not_before=0.0`）、在首次采集前初始化缺失的 `state-v4.sqlite3`，并将采集引用写入本地队列交由后台 Worker 异步处理。
- 写入采集队列仅代表完成本地准入；后续的分块索引生成与 GitHub PR 发布由后台 Worker 独立生成回执。任何失败或结果不确定的模型调用均保留真实记录，绝不静默重放。

### 确定性的公开消息筛选
仅已获授权会话中用户与助手的公开可见文本会被纳入采集范围：
- 解析器仅提取带有效 `sessionId`、时间戳的公开文本块。
- 内部思考过程（Thinking blocks）、工具调用输入与返回载荷、注入的 `isMeta` 消息、附件、系统记录与支线会话（Sidechains）均被确定性剔除。
- 采集起点不早于 `max(created_at, activated_at)`——即“会话/Fork 创建时间”与租约 `activated_at` 边界中的较晚者。

---

## 3. 组件职责划分与平滑更新

| 层级 | 核心职责 |
|---|---|
| **Claude Code 宿主** | 原生插件市场目录、安装/卸载状态记录、Hook 事件投递与 MCP stdio 传输。 |
| **`mindie-agent-cc`（本适配层）** | 原生插件清单（`.claude-plugin/`、`.mcp.json`、`hooks/`、`skills/`）、任务身份关联、公开对话解析，以及 `CLAUDE_PLUGIN_DATA` 下的固定 Python 3.13 运行时管理。 |
| **共享运行时组件** | `mindie-knowledge`（本地检索、确定性脱敏、索引队列与 PR 发布）、`remote-dev`（SSH 远端执行与作业轮询）、`mindie-diagnostics`（本地诊断回执与可选故障上报）。 |

在版本更新时，新版本会在独立目录中准备好完整的源码、锁文件与 Python 运行时；在途操作在共享锁保护下继续完成，待核心服务空闲时再原子切换绑定。若候选版本准备失败，原有版本保持完好可用，不会陷入死循环重试。

---

## 4. 验证范围说明

- **已验证能力**：基于真实 Claude Code `2.1.269` 进程（macOS 隔离配置，`deepseek-flash`）完成原生接口探针验证，确认 `UserPromptExpansion`、`PreToolUse` → `_meta["claudecode/toolUseId"]` 身份关联以及静默 `Stop` 收尾全链路正常。
- **验收边界**：宿主 Hook 关联测试用于验证适配层接缝契约；完整的端到端经验发布闭环与原生 Windows 宿主验收作为独立项管理（详见 [框架稳定性与测试契约](framework-stability.zh.md)）。
- **Claude Code 官方文档参考**：[Hooks](https://code.claude.com/docs/en/hooks) · [Plugins](https://code.claude.com/docs/en/plugins-reference) · [Skills](https://code.claude.com/docs/en/skills)
