# mindie-agent-cc 0.1.30：Great Subtraction 与安装体验升级

[English](mindie-agent-cc-0.1.30.en.md)

- **大幅精简（Great Subtraction）**：删除 40 个历史重复运行时模块，`scripts/` 仅保留 8 个薄文件（`__init__`、`plugin_entry`、`session_start`、`mindie_launch`、`entry`、`host_adapter`、`transcript`、`setup`），总 Python 行数保持在 1,200 行以内；MCP、Hook、共享、更新、RSI 与锁全部委托 `mindie-harness`（`API_VERSION = 1`），并继续使用内置 `assets/{mindie-harness-seed.pyz, seed-lock.json}` 完成确定性冷启动。
- **零内联启动与严格 MCP 面隔离**：`hooks/hooks.json` 与 `.mcp.json` 统一调用文件式 `scripts/plugin_entry.py`，彻底移除 `python -c` 内联片段；`knowledge` 仅暴露 5 个 `knowledge_*`，`remote` 仅暴露 13 个 `claude` `remote_*`（零 `remote_BashOutput` / `remote_KillShell`），`components` 仅暴露组件工具且零 `knowledge_*`。`Stop` 在未配置、已关闭或 `cwd` 越界时以 `< 30ms` 静默退出且零 SQLite，并使用同盘 `os.path.commonpath` 前缀匹配避免 `/work/app-other` 误匹配 `/work/app`。
- **安装与首次配置体验**：`uv run https://github.com/mindie-agent/mindie-agent/releases/latest/download/install.py --host cc` 一条命令完成安装与运行时预热；`gh` 改为可选通道（未安装或未登录时自动推荐 `local-only`，永不阻断安装）。首次配置使用 3 个动态 `gh` 感知的 `AskUserQuestion` 问题——工作区、社区共享（`gh` 已登录推荐 `contribute`，否则推荐 `local-only`）与一步 SSH NPU 连接（终端模式可在最后通过隐藏 `--password` 登录），并在问题后附一行礼貌的 Star 提示。新增只读 `mindie_doctor` 就绪检查，失败时只报告一次具体错误并继续。
- **测试与发布校验**：`tests/` 重建为快速、隔离的薄插件契约测试，覆盖 manifest/seed 与 `release-metadata`、Hook 快速路径、MCP 工具面严格隔离、`setup.py sharing`/`doctor` 委托，以及 transcript 的 fork `queue-operation` `enqueue` 边界、sidechain/thinking/`<command-...>` 过滤与 `[start, end)` SHA-256 记账。`setup.py release-metadata` 继续校验 `harness-compatibility.json` 的 AST 符号、测试 ID 与本地证据文件。
