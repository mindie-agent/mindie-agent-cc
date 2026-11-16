# mindie-agent-cc 0.1.25：存储生命周期治理与存量历史包袱静默回收

[English](mindie-agent-cc-0.1.25.en.md)

- **组件正式版锁定**：将运行时组件固定到已审查的不可变提交——`mindie-knowledge v0.13.22`（`c991b64`）、`mindie-diagnostics v0.5.15`（`96c4004`）、`remote-dev v0.11.12`（`c4d38f9`）、`mindie-coordinator v0.7.11`（`33e2c69`）、`mindie-profiler v0.5.15`（`609d555`）、`mindie-sim v0.9.15`（`9dbab02`）、`mindie-debugger v0.1.3`（`33052a2`）。`uv.lock` 与 `[tool.uv] override-dependencies` 同时锁定，避免浮动引用。
- **插件升级只构建一套运行时**：组件 floor 已安装时，`bootstrap.py` 先用现有解释器调用新版 `component_update.py` 构建合并运行时并直接复用；仅在无需组件覆盖或现有解释器不可用时才构建 baseline，消除“baseline + 合并运行时”双份 `.venv`。`uv sync` 在默认缓存跨卷（含 Windows 跨盘符）时自动改用 `runtimes/cc/.uv-cache` 同卷缓存，恢复 `clonefile`/hardlink。
- **隐藏启动根同步解绑**：组件更新或激活切换 `cc.json["python"]` 后，原子刷新 `$CLAUDE_PLUGIN_DATA/releases/<active>/entry.py` 指向新解释器，并按有界策略清理已被取代且无活进程引用的旧 `releases/<old>`，保留当前版本与最近回滚版本。
- **常驻 MCP 持有共享运行租约**：`native_mcp.serve()` 与独立常驻 `mcp_server.serve()` 对 `owning_dir(sys.executable)` 及当前绑定的运行时目录持有 `acquire_runtime_lease` 共享租约，进程退出或崩溃时由内核释放，避免清理误删正在使用的代。
- **存量自动回收与过期会话清理**：组件切换后、后台发布检查周期、Slash 命令入口与构建前均按有界预算调用 `sweep_legacy_installations` / `prune_component_runtimes`，静默回收 `runtimes/cc/*`、`adapter-state/installations/*`、`adapter-state/component-runtimes/*`；同时按 WAL + `busy_timeout` 规则淘汰超过 7 天的终态 `mcp-binds.sqlite3` 行与无活跃进程/锁的死亡 `remote/<session>`。`bound`/`claimed` 绑定与任何 `submission_state=uncertain|unknown` 的未决远端写状态永不触碰。
- **跨平台删除纪律**：所有目录回收统一经由 `mindie_knowledge.storage.retire_directory`；POSIX 锁留在目标目录内，Windows 不在被重命名的目录内持有句柄。NTFS `PermissionError` 只记为 `deferred_in_use`，源目录保持原样，不做半删重试。Windows 新构建使用 24 字符短键，同时向后读取既有 64 字符目录与 `.ready.json`。
- **状态诚实**：主流程成功后，清理遇占用或预算耗尽仅追加 `cleanup_warning` / `deferred_in_use`，不改变工具结果、激活状态或安装状态，也不写入单向 `fault`。
- **sim 输出预算**：新版 `mindie-sim` 的 SLA 前沿为约 220 KiB 结构化结果，`components` 的 sim 默认输出预算提升至 512 KiB（所有组件仍受 1 MiB 上限约束），未显式指定预算的正式 CLI 调用不再被截断。

关联设计：共享存储生命周期由 `mindie-knowledge v0.13.22` 的 `mindie_knowledge.storage` 统一提供；本版本仅负责 Claude Code 侧的启动根、租约与会话状态接入。
