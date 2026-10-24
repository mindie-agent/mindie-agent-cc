# 原生插件与运行时边界

<b>中文</b> | <a href="native-boundary.md">English</a> · <a href="README.md">返回文档索引</a>

`mindie-agent-cc` 遵循 [MindIE Agent 工程设计原则](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.md)，定位为轻量的 Claude Code 原生插件适配层。它负责将 Claude Code 的插件生命周期、Hook 事件和公开对话记录桥接到共享运行时，不在本仓库内复制知识引擎或维护第二套状态库。

---

## 1. 入口与任务身份自动绑定

### 知识按会话手动启用 vs 确定性工具默认常驻
`mindie-agent-cc` 将确定性工程工具与按会话启用的知识飞轮明确解耦：
- **远端昇腾开发（`remote-dev`）与事实状态查询（`mindie_status`）**：在普通会话中默认直接可用，无需输入 `/mindie-agent`，也与知识服务及社区贡献开关完全独立。
- **领域知识与经验飞轮（`mindie-knowledge` 工具集与 `Stop` 采集）**：需在当前会话中显式输入一次 `/mindie-agent`（由 `UserPromptSubmit` / `UserPromptExpansion` 设置 `entry_enabled = 1`）。启用后在本会话后续轮次持续有效，直至执行 `/mindie-agent deactivate`；新建会话、`--fork-session` 分支会话与子代理均不会自动继承该授权。
- **统一入口（`/mindie-agent` 或 `/mindie-agent:init`）**：激活当前会话的知识租约并提供交互式配置与状态管理。其 `UserPromptExpansion` 事件会自动传入当前任务 ID 与工作目录，直接复用已保存的偏好，仅在用户需要开启公开贡献但缺少目标仓库、账号或项目根目录时提示补充。

### `PreToolUse` 到 MCP 调用的确定性绑定
在每次工具调用执行前，Claude Code 的 `PreToolUse` Hook 会提供原生 `tool_use_id`、会话 ID、工具名称与参数；紧接着发起的 MCP 请求会在 `_meta["claudecode/toolUseId"]` 中携带同一个 ID：
- 适配层利用这一原生机制在内部自动完成“工具调用 ↔ 当前会话任务”的身份绑定，无需让大模型自己生成或传递会话参数。
- 每次绑定仅消费一次，并严格校验工具名与参数摘要；缺失或不一致的绑定将直接拒绝（Fail-Closed）。
- 消费 `PreToolUse` 绑定仅证明原生会话身份，不代表已开启知识系统：`knowledge_*` 工具还会进一步校验 `admission.knowledge_lease(session)`（`entry_enabled == 1`）后才会连接知识服务，而 `remote-dev` 与 `mindie_status` 仅凭该单次绑定即可直接执行。
- 新建会话与 `--fork-session` 分支会话各自拥有独立绑定，绝不继承父任务的后台作业归属、手动激活租约或历史采集范围。

---

## 2. `Stop` Hook 与公开对话提取

### 快速、非阻塞的 `Stop` 交接
`Stop` Hook 在极短的本地交接窗口内静默返回中性结果，绝不向业务大模型返回“继续执行”指令：
- **当当前会话未调用 `/mindie-agent` 或社区贡献处于关闭状态时**：`Stop` 先检查 `admission.knowledge_lease(session)` 并直接短路返回 `{}`——不读取对话文件、不创建采集记录，也不发起任何整理模型调用。
- **当当前会话已通过 `/mindie-agent` 启用且当前项目已开启社区贡献时**：`Stop` 校验当前 Claude Profile 下的会话文件、核对已保存的项目授权范围，并将采集引用写入本地队列交由后台 Worker 异步处理。
- 写入采集队列仅代表完成本地准入；后续的分块索引生成与 GitHub PR 发布由后台 Worker 独立生成回执。任何失败或结果不确定的模型调用均保留真实记录，绝不静默重放。

### 确定性的公开消息筛选
仅当前已激活 `/mindie-agent` 会话中用户与助手的公开可见文本会被纳入采集范围：
- 解析器仅提取带有效 `sessionId`、时间戳的公开文本块。
- 内部思考过程（Thinking blocks）、工具调用输入与返回载荷、注入的 `isMeta` 消息、附件、系统记录与支线会话（Sidechains）均被确定性剔除。
- 采集起点不早于 `max(created_at, activated_at)`——即“会话/Fork 创建时间”与“当前会话执行 `/mindie-agent` 激活时间”中的较晚者。

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
