# Claude Code Harness 接口兼容性与验证证据

<b>中文</b> | <a href="harness-interface-evidence.md">English</a> · <a href="README.md">返回文档索引</a>

[`harness-compatibility.json`](../harness-compatibility.json) 是 `mindie-agent-cc` 所依赖的 Claude Code 宿主接口的唯一机器可读注册表。当前 `v0.0.2` 发行版经实机验证声明支持两个精确的 Claude Code 版本：**`2.1.269`** 与 **`2.1.299`**。

在 CI 运行组件测试前，`scripts/setup.py release-metadata` 会自动校验该注册表：核对每一个登记的生产代码符号、测试用例 ID 与本地证据文档均真实存在，并将兼容性元数据绑定到不可变的 Git 提交哈希上。

---

## 1. 已登记宿主接口验证矩阵

以下实验均在隔离的原生 Claude Code 配置目录（Profile）中完成；控制面验证不发送用户提示词，也不发起外部大模型请求。

| 登记接口类别 | 实机验证行为（`2.1.269` & `2.1.299`） | 自动化契约测试 |
|---|---|---|
| **Git 插件市场与安装记录** | `2.1.269` 与 `2.1.299` 均支持通过显式 Git Tag 安装插件，并通过 `claude plugin list --json` 返回版本号、作用域、启用状态与安装路径。薄启动器优先解析 `$CLAUDE_PLUGIN_DATA/releases/<ver>/entry.py`，否则回退到保留的 `scripts/`。 | `tests/test_thin_plugin_contract.py`：校验插件市场/版本清单与零内联启动解析。 |
| **插件市场独立设置** | 指定 Tag 安装仅更新本插件在 `extraKnownMarketplaces` 中的 `source.ref` 与 `autoUpdate` 字段，完整保留用户显式设置的 `enabled: false` 及其他无关配置。 | `tests/test_thin_plugin_contract.py`：校验插件市场/版本清单与零内联启动配置。 |
| **无会话初始化（`--init-only`）** | `claude --init-only` 可在不启动对话的情况下完成 `SessionStart` 运行时准备。鉴于 Windows 下仅凭退出码 `0` 不能证明 MCP 加载成功，工具发现与运行时回读作为独立门禁校验。 | `tests/test_launch_and_mcp_surfaces.py`：校验被动的 `SessionStart` 入队与 pending-update 标记。 |
| **Hook 载荷与静态告警** | 两个版本的真实 `Stop` 事件均提供 `session_id`、`transcript_path` 与 `cwd`。静态 `systemMessage` 告警仅向终端展示提示，不阻塞会话结束，也不向模型注入上下文。 | `tests/test_launch_and_mcp_surfaces.py`：在无模型条件下校验损坏配置、缺失字段、非法 Schema 以及关闭共享时的静默退出。 |
| **MCP Stdio 传输** | 原生 SDK 初始化可在不发起模型对话的情况下发现 `mindie-knowledge` 与 `remote-dev` 两个 MCP 服务；stdio 前端支持独立于后台执行的请求取消。 | `tests/test_launch_and_mcp_surfaces.py`：验证 `knowledge`、`remote` 与 `components` 的 stdio 工具面隔离。 |
| **结构化输出提交（Structured Output）** | 在 macOS（`2.1.269`）、原生 Windows（`2.1.299`）与 WSL（`2.1.299`）上通过本地合成 HTTP 服务实机验证：Claude Code 原生支持最多 5 次 Schema 校验修正，超限后返回 `error_max_structured_output_retries`。若模型仅返回普通文本而无 `structured_output`，适配层直接判定失败，绝不从自由文本中硬抠 JSON。 | 结构化输出采集已委托 `mindie_harness`；`tests/test_transcript.py` 校验该流程消费的公开对话投影。 |

### 结构化输出与用量记账说明
- **单次准入调用记账**：`model_calls` 统计 Worker 发起的原生 CLI 调用次数（1 次），而 Token 用量完整记录 Claude Code 在内部自动修正回合中累计消耗的全部 Token。
- **脱敏的 Schema 诊断**：Worker 通过 `stream-json --verbose` 仅记录提交/拒绝次数，以及与标准校验关键字（`required`、`enum`、`maxLength`）配对的 Schema 字段路径。模型生成的自由文本、提交值、工具 ID 与原始校验报错字符串均不会写入诊断回执。
- **官方文档对照**：
  - [Claude Code CLI 参考](https://code.claude.com/docs/en/cli-reference)
  - [Claude Code 环境变量](https://code.claude.com/docs/en/env-vars)（原生 `MAX_STRUCTURED_OUTPUT_RETRIES` 默认值为 5）
  - [Agent SDK 结构化输出](https://platform.claude.com/docs/en/agent-sdk/structured-outputs)

---

## 2. 分支会话归属（`--fork-session`）

当用户通过 `claude --resume <ID> --fork-session` 从现有会话派生子会话时，Claude Code 会在第一条子会话消息产生前触发 `source: "fork"` 的 `SessionStart` 事件，并提供新的子会话 `session_id`、`transcript_path` 与 `cwd`。

### `mindie-agent-cc` 如何隔离父子会话历史
1. **Fork 子会话独立归属**：`--fork-session` 派生的子会话**不会**继承父会话的租约令牌，也不会在 `SessionStart (source: "fork")` 时触发隐式关联或同步，保持 `SessionStart` 轻量被动。
2. **识别并排除复制的父会话记录**：Claude Code 生成子会话 JSONL 文件时，会将复制过来的父会话记录的 `sessionId` 改写为子会话 ID（保留原时间戳），但在文件开头会先写入属于子会话自身的初始 `enqueue` 队列时间戳。
3. **确定性的子会话增量采集**：当子会话所在项目已显式开启社区贡献时，`Stop`（`transcript.capture_source`）同时校验子会话自身的初始 `enqueue` 队列锚点（在常规 `--resume` 与 `/compact` 压缩后依然保持稳定）与子会话租约的 `activated_at` 时间戳（取 `max(created_at, activated_at)`），仅采集子会话自身创建之后产生的新公开对话。若归属元数据缺失或不可读，`Stop` 会显式报错，绝不回退去采集从父会话继承的历史内容。归属头部读取严格限制在前 64 行 / 1 MiB 以内。
