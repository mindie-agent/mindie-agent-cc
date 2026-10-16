# MindIE Agent Claude Code 插件文档索引

中文 | [English](README.en.md) · [返回仓库首页](../README.md)

本目录汇总 `mindie-agent-cc`（Claude Code 原生插件）的架构边界、宿主接口证据、服务连续性、故障诊断与框架稳定性规范。所有文档均提供中英双语版本。

## 核心技术文档

| 中文文档 | English | 说明 |
|---|---|---|
| [原生插件与运行时边界](native-boundary.zh.md) | [Native Plugin & Runtime Boundary](native-boundary.md) | 宿主插件清单、`CLAUDE_PLUGIN_DATA` 隔离运行时、Hook 入队与 MCP 接线边界 |
| [Harness 接口兼容性证据](harness-interface-evidence.zh.md) | [Harness Interface Evidence](harness-interface-evidence.md) | `harness-compatibility.json` 登记的 Claude Code 接口、支持版本区间与实机核对依据 |
| [服务连续性与空闲切换](service-continuity.zh.md) | [Service Continuity](service-continuity.md) | 后台知识服务归属、配置指纹校验、原生小模型整理与更新连续性边界 |
| [故障诊断与自动上报](failure-diagnostics.zh.md) | [Failure Diagnostics](failure-diagnostics.md) | 本地结构化故障日志、诊断关联 ID 与独立授权的 GitHub Issue 上报 |
| [框架稳定性与测试契约](framework-stability.zh.md) | [Framework Stability](framework-stability.md) | 持久配置原子读写、锁定依赖校验与集成测试边界 |
