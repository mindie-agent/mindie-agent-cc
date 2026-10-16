# 原生插件与运行时边界

中文 | [English](native-boundary.md) · [返回文档索引](README.md)

本文定义 `mindie-agent-cc` 在 Claude Code 宿主中的原生插件目录、独立运行时环境、Hook 事件与 MCP 服务边界。

## 1. 原生插件加载与独立运行时

- 插件通过 `.claude-plugin/plugin.json`、`.claude-plugin/marketplace.json`、`hooks/hooks.json`、`.mcp.json` 与 `skills/` 向 Claude Code 声明能力。
- 宿主的插件缓存目录仅作为入口分发载体；长期运行的后台知识服务与 `uv` 锁定的非 editable Python 运行时安装在宿主要求的持久化数据目录（如 `CLAUDE_PLUGIN_DATA`）下，避免宿主刷新缓存导致在途服务中断。
- 已刪除旧版自研的多阶段 `updater.py`、`genstate.py` 与全局 `current` 指针，统一由单一安装绑定与 `product-contract.json` 校验。

## 2. Hook 与 MCP 职责分离

- **`SessionStart` Hook**：负责校验或准备锁定运行时、按间隔触发公共知识同步与诊断离线维护，不阻塞正常对话。
- **`Stop` Hook**：仅对已验证归属且处于有效授权项目范围内的原生任务执行快速同步 SQLite 准入，并返回中性协议输出，绝不在 Hook 内同步等待模型摘要或执行网络升级。
- **MCP 服务（`knowledge` 与 `remote-dev`）**：按需提供知识检索、状态查询与 SSH 远端开发能力，不修改用户无关的全局 Claude Code 配置。
