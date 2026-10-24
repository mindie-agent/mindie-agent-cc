# 故障诊断与本地进程取消

<b>中文</b> | <a href="failure-diagnostics.md">English</a> · <a href="README.md">返回文档索引</a>

遵循 [MindIE Agent 工程设计原则](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.md)中“工具可观察、可定位，禁止隐藏错误”的要求，`mindie-agent-cc` 提供纯只读的状态诊断、确定性的本地子进程取消清理，以及与知识贡献共用授权的脱敏产品故障上报机制。

---

## 1. 纯只读状态检查（`/mindie-agent:status` 与 `mindie_status`）

- **诊断输出内容**：报告客观的控制面事实状态（`configured`、`knowledge_enabled`、当前的知识库配置、后台服务与存储健康状况、`this_session` 手动激活租约状态、诊断失败计数、`reporting` 状态，以及与当前任务关联的最近 5 次采集和贡献批次）。MCP `mindie_status` 工具（`entry.status_facts(session)`）直接返回上述事实状态，不注入首次引导提示字段（`first_use`、`next`、`recovery`）。
- **严格无副作用**：执行 `status` 或 `mindie_status` 绝不会初始化数据库、启动模型或后台服务、隐式激活或绑定任务、清零失败计数或重放已消费的输入。
- **损坏配置直接报错**：若已有的适配器配置文件损坏或格式非法，`status` 会直接报告配置诊断错误，绝不会将其误判为“首次安装”而重新引导覆盖。
- **远端开发独立可用**：即使本地知识服务不可用，原生 Shell/SSH 以及独立的 `remote-dev` 远端昇腾开发服务依然保持正常可用。

---

## 2. 本地请求取消与远端昇腾作业的边界

- **前端保持实时响应**：在有界工具请求执行期间，MCP stdio 前端持续响应 `ping`、取消指令（Cancel）与标准输入关闭（`EOF`）。
- **精准回收本地子进程**：取消前台工具调用或遇到 `EOF` 时，仅清理当前请求所拥有的本地辅助子进程树。
- **远端持久作业生命周期独立**：取消本地的日志观察或轮询请求，**并不代表**远端昇腾服务器上的编译或测试作业已经停止。返回结果会完整保留远端作业 ID 与投递状态；在发起新任务前，请先显式查询或停止该远端作业。远端轮询单次等待上限为 30 秒，不会修改远端任务本身的超时设置。

---

## 3. 原生整理器失败的脱敏诊断投影

当用于生成分块检索导航的隔离 Claude 调用失败时，适配层会在私有结果回执中记录有界的 `mindie-cc-native-failure/1` 诊断投影：
- **仅保留安全枚举与计数**：包括已知的原生终止枚举（如轮次超限 `turn-limit`、预算耗尽 `cost-budget`、结构化输出重试耗尽 `structured-output exhaustion`）、校验后的轮次数值、HTTP 状态码与错误字段计数。
- **剔除一切敏感与自由文本**：绝不保留模型或服务商返回的自由文本、提示词、会话 ID 或凭证信息。
- **禁止静默重放已计费调用**：原始 Token 用量与失败状态保持权威记录，失败或结果不确定的模型调用绝不自动重试。

---

## 4. 可选的产品故障自动上报（`/mindie-agent:reporting-*`）

`mindie-agent-cc` 集成了共享的 `mindie-diagnostics` 故障上报组件：
- **统一贡献开关（`mindie-consent.json`）**：显式开启社区贡献（`contribute`）会同时授权配置范围内的知识 PR 与脱敏故障 Issue。配置时 Hook 返回待启动交接项（`reporting.run_outside_hook=true`），由 Agent 在同次配置流程中于 Hook 外启动后台 reporter 并核验就绪状态（`reporting.status == "running"`）。缺少统一授权版本（`choice_revision`）的旧版仅知识 `contribute` 记录在升级后不会自动获得故障上传权限，状态显示 `needs_choice` 并等待用户重新显式确认一次。系统严格区分**（1）本地授权配置已保存**、**（2）后台 reporter 进程运行中**与**（3）外部 GitHub Issue 已实际创建**三种状态。旧版 reporting 变更命令仅引导至统一贡献配置流程，`/mindie-agent:reporting-status` 保持纯只读。
- **极简脱敏载荷**：仅针对经确认的产品自身故障生成记录；开启上报后，后台 Worker 会在不调用大模型、不打扰日常任务的情况下提交脱敏 GitHub Issue。公开载荷**仅包含**静态产品阶段、错误类型与已安装代码版本，绝不包含任务对话、命令、路径、环境变量或原始异常栈。
- **指纹去重与对账**：相同故障共享同一指纹；若 Issue 提交结果因网络中断处于不确定状态，会在后续动作前先完成远端对账，绝不盲目重复建单。

---

## 5. 验证范围说明

- **macOS 与组件级验证**：已覆盖任务隔离的 SQLite 只读诊断、损坏配置识别、子进程/孙进程树取消回收、繁忙态 `ping` 响应、`EOF` 清理、输出背压以及三类原生整理器终止边界。
- **Windows 验收边界**：合成日志/发件箱/模拟 Issue 检查及 Windows 进程身份核验已在 `mindie-agent/diagnostics` 仓库中验证；原生 Windows 管道写入阻塞与宿主 UI 取消事件作为独立实机验收项管理。
