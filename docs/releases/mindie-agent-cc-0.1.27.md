# mindie-agent-cc 0.1.27：修复自动更新自锁死（`#364/#365`）、接入 `mindie-harness` 共享底座与跨连接作业句柄互通

[English](mindie-agent-cc-0.1.27.en.md)

- **根除自动更新失败后的单向自锁死（修复 `#364/#365`）**：候选环境准备阶段新增 `>= 2.5 GiB` 可用磁盘预检，失败的更新尝试仅标记候选失败并无损保留既有活跃解释器与配置，消除 `needs_repair` 类本地永久阻断；`.mcp.json` 切换至纯文件入口 `scripts/plugin_entry.py`（零 `python -c` 内联脚本）。
- **接入 `mindie-harness v0.1.0` 共享底座与跨连接后台作业句柄互通**：打包并校验 `assets/mindie-harness-seed.pyz` + `seed-lock.json`，将原子写、文件锁与 RSI 循环委托至 `mindie_harness`；共享 `REMOTE_DEV_STATE_DIR` 使后台远端作业句柄（`job_id`）跨 MCP 重连与子会话无缝可查可控。
- **`0.1.13` 全量组件正式版锁定**：在 `pyproject.toml` 与 `uv.lock` 中同步锁定 `mindie-harness v0.1.0`、`mindie-knowledge v0.13.23`、`remote-dev v0.11.13`、`mindie-diagnostics v0.5.16`、`mindie-debugger v0.1.4`、`mindie-profiler v0.5.16`、`mindie-sim v0.9.18` 与 `mindie-coordinator v0.7.13`，并对齐 `--profile host-plugin` PR 门禁。
