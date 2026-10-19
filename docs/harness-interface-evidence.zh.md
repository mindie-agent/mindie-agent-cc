# Claude Code Harness 接口兼容性与验证证据

<b>中文</b> | <a href="harness-interface-evidence.md">English</a> · <a href="README.md">返回文档索引</a>

[`harness-compatibility.json`](../harness-compatibility.json) 是 `mindie-agent-cc` 所依赖的 Claude Code 宿主接口的唯一机器可读注册表。当前 `v0.0.1` 发行版经实机验证声明支持两个精确的 Claude Code 版本：**`2.1.269`** 与 **`2.1.299`**。

在 CI 运行组件测试前，`scripts/harness.py` 会自动校验该注册表：核对每一个登记的生产代码符号、测试用例 ID 与本地证据文档均真实存在，并将兼容性元数据绑定到不可变的 Git 提交哈希上。

---

## 1. 已登记宿主接口验证矩阵

以下实验均在隔离的原生 Claude Code 配置目录（Profile）中完成；控制面验证不发送用户提示词，也不发起外部大模型请求。

| 登记接口类别 | 实机验证行为（`2.1.269` & `2.1.299`） | 自动化契约测试 |
|---|---|---|
| **Git 插件市场与安装记录** | `2.1.269` 与 `2.1.299` 均支持通过显式 Git Tag 安装插件，并通过 `claude plugin list --json` 返回版本号、作用域、启用状态与安装路径。（注：`2.1.269` 不接受裸 Commit SHA 作为分支引用，因此安装器使用不可变 Release Tag 并在安装前校验检出提交的精确 SHA。） | `tests/test_release_update.py`：在更新前校验目标 SHA，并防止从过期快照误判在途写入状态。 |
| **插件市场独立设置** | 指定 Tag 安装仅更新本插件在 `extraKnownMarketplaces` 中的 `source.ref` 与 `autoUpdate` 字段，完整保留用户显式设置的 `enabled: false` 及其他无关配置。 | `test_native_first_install_policy_preserves_exact_source_and_other_choices`、`test_native_source_declaration_preserves_other_settings_and_explicit_off` |
| **无会话初始化（`--init-only`）** | `claude --init-only` 可在不启动对话的情况下完成 `SessionStart` 运行时准备。鉴于 Windows 下仅凭退出码 `0` 不能证明 MCP 加载成功，工具发现与运行时回读作为独立门禁校验。 | `tests/test_setup_install.py`：覆盖首次构建、版本兼容检查与保留状态的激活流程。 |
| **Hook 载荷与静态告警** | 两个版本的真实 `Stop` 事件均提供 `session_id`、`transcript_path` 与 `cwd`。静态 `systemMessage` 告警仅向终端展示提示，不阻塞会话结束，也不向模型注入上下文。 | `tests/test_launch_entry.py`：在无模型条件下校验损坏配置、缺失字段、非法 Schema 以及关闭共享时的静默退出。 |
| **MCP Stdio 传输** | 原生 SDK 初始化可在不发起模型对话的情况下发现 `mindie-knowledge` 与 `remote-dev` 两个 MCP 服务；stdio 前端支持独立于后台执行的请求取消。 | `tests/test_front_control.py`：验证前端进程在请求取消与后台绑定切换期间保持存活与响应。 |
| **结构化输出提交（Structured Output）** | 在 macOS（`2.1.269`）、原生 Windows（`2.1.299`）与 WSL（`2.1.299`）上通过本地合成 HTTP 服务实机验证：Claude Code 原生支持最多 5 次 Schema 校验修正，超限后返回 `error_max_structured_output_retries`。若模型仅返回普通文本而无 `structured_output`，适配层直接判定失败，绝不从自由文本中硬抠 JSON。 | `tests/test_independent_cc.py` 中的 `WorkerEnvelopeTests`：验证 `structured_output` 唯一权威性、内部多轮修正的累计 Token 记账以及单批次单调用边界。 |

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
1. **提示词前的边界锚定**：在收到 `SessionStart (source: "fork")` 时，适配层立即建立子会话的独立准入范围与采集起点，无需扫描父会话历史文件或维护额外的血缘数据库。
2. **识别并排除复制的父会话记录**：Claude Code 生成子会话 JSONL 文件时，会将复制过来的父会话记录的 `sessionId` 改写为子会话 ID（保留原时间戳），但在文件开头会先写入属于子会话自身的初始 `enqueue` 队列时间戳。
3. **确定性的子会话增量采集**：在 `Stop` 阶段，适配层校验子会话自身的初始队列锚点（在常规 `--resume` 与 `/compact` 压缩后依然保持稳定），仅采集子会话自身产生的新公开对话。若归属元数据缺失或不可读，`Stop` 会显式报错，绝不回退去采集从父会话继承的历史内容。归属头部读取严格限制在前 64 行 / 1 MiB 以内。
