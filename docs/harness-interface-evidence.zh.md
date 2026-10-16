# Harness 接口兼容性证据

中文 | [English](harness-interface-evidence.md) · [返回文档索引](README.md)

本文为 [`harness-compatibility.json`](../harness-compatibility.json) 中登记的 Claude Code 宿主接口提供中文说明与验证边界摘要。精确锚点与原始实机证据保存在英文版 [`harness-interface-evidence.md`](harness-interface-evidence.md) 中，供 `scripts/harness.py` 自动核对。

## 登记接口概览

| 接口分类 | 关键契约与验证要点 |
|---|---|
| **插件清单与市场安装（Plugin Manifest & Marketplace）** | 验证 `.claude-plugin/plugin.json` 与 `marketplace.json` 在 Claude Code `>=2.1.269, <=2.1.299`（macOS、原生 Windows、WSL）下的安装、升级与卸载行为。 |
| **持久化数据目录（`CLAUDE_PLUGIN_DATA`）** | 实机确认 `SessionStart`、`Stop` 与 MCP 子进程获得一致的插件数据目录环境变量，用于存放锁定运行时与持久状态。 |
| **生命周期 Hook（`SessionStart` / `Stop`）** | 核对 Hook 标准输入载荷（含会话 ID、Transcript 路径与工作目录）、中性 JSON 输出协议、超时边界及 `claude -p` 退出语义。 |
| **MCP 服务接线（`.mcp.json`）** | 验证 stdio MCP 启动、工具发现、`MCP_TIMEOUT` 连接窗口以及在途调用取消行为。 |
| **原生小模型整理调用（Organizer CLI）** | 验证隔离配置下的 `claude -p` 单回合整理调用，确保不递归触发插件自身 Hook、MCP 或业务工具。 |
