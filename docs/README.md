# MindIE Agent for Claude Code · 技术文档索引

<b>中文</b> | <a href="README.en.md">English</a> · <a href="../README.md">返回仓库首页</a>

本目录提供 `mindie-agent-cc`（Claude Code 原生插件）的架构设计、宿主接口验证、服务连续性、故障诊断与测试规范文档。所有文档均提供结构对称的中英双语版本。

## 核心文档列表

| 中文文档 | English | 内容概要 |
|---|---|---|
| [原生插件与运行时边界](native-boundary.zh.md) | [Native Plugin & Runtime Boundary](native-boundary.md) | 任务身份绑定、`PreToolUse` / `Stop` Hook 工作机制、公开对话提取规则与共享组件职责划分 |
| [Harness 接口兼容性与验证证据](harness-interface-evidence.zh.md) | [Harness Interface Evidence](harness-interface-evidence.md) | `harness-compatibility.json` 登记的 Claude Code 接口、结构化输出重试边界与 `--fork-session` 会话归属 |
| [服务连续性与平滑更新](service-continuity.zh.md) | [Service Continuity & Updates](service-continuity.md) | 不可变 `uv` 运行时准备、并发锁保护、空闲服务切换与 `Stop` 采集队列可靠性 |
| [故障诊断与本地进程取消](failure-diagnostics.zh.md) | [Failure Diagnostics & Cancellation](failure-diagnostics.md) | 只读状态诊断、本地进程取消边界、失败原因脱敏投影与可选产品故障上报 |
| [框架稳定性与测试契约](framework-stability.zh.md) | [Framework Stability & Testing](framework-stability.md) | 日常无感使用保障、Windows 原子配置读写、组件测试复现与验收边界说明 |
