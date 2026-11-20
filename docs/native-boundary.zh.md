# 原生插件与运行时边界

<b>中文</b> | <a href="native-boundary.md">English</a> · <a href="README.md">返回文档索引</a>

`mindie-agent-cc` 遵循 [MindIE Agent 工程设计原则](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.md)，定位为轻量的 Claude Code 原生插件适配层。它负责将 Claude Code 的插件生命周期、Hook 事件和公开对话记录桥接到共享运行时，不在本仓库内复制知识引擎或维护第二套状态库。

---

## 1. 入口与任务身份自动绑定

### 全量工具静默默认就绪与语义 Skill 加载
`mindie-agent-cc`（`v0.1.32`）将三套严格正交的默认就绪研发工具集与显式授权的经验采集清晰分层：
- **三套正交 MCP 服务默认就绪（`knowledge`、`remote`、`components`）**：插件启用期间在主会话、`Task` 子智能体与 `--fork-session` 分支会话中开箱即用，无需输入斜杠命令。`knowledge` 仅暴露 5 个 `knowledge_*` 工具（`knowledge_query`、`knowledge_explain`、`knowledge_feedback`、`knowledge_distill`、`knowledge_sync`）；`remote` 暴露 13 个 `claude` 规范 `remote_*` 工具（`remote_Read` .. `remote_artifact_push`）；`components` 暴露 11 个本地与组件工具（`mindie_status`、`mindie_doctor`、`diagnostics_status`、`diagnostics_bundle`、`profiler`、`sim`、`coordinator`、`npu_top`、`debugger`、`rsi_loop`、`experiment_ledger`），跨服务误调一律以 `unknown_operation` 拒绝。只读检索与状态检查（`knowledge_query`、`knowledge_explain`、`mindie_status`、`mindie_doctor`）绝不创建空的 `admission.sqlite3`；`knowledge_distill` 与作用域内 `knowledge_feedback` 在未显式停用时按需自动初始化会话租约（`not_before=0.0`）。
- **纯标记冷启动与确定性种子引导**：冷态 `SessionStart`（`session_start.py` -> `mindie_launch.py`）采用纯标准库标记写入，仅原子生成 `pending-update-check.json` 并在 `< 30ms` 内返回 `{}`，绝不执行 `uv sync`、构建运行时或创建 SQLite。当首个 MCP 请求到达而 `cc.json` 尚未绑定有效解释器时，`mindie_launch.py:_cold` 先核验 `assets/mindie-harness-seed.pyz` 与 `assets/seed-lock.json`，再以 `stdin=subprocess.DEVNULL`、`stdout=sys.stderr` 执行种子引导以保持 MCP JSON-RPC 流纯净，过滤 `WindowsApps` 空字节别名，并在未能产出有效运行时解释器时显式非零退出。
- **9 领域内容寻址绑定与 Wheel 布局兼容**：`entry.py:initialize_profile` 与 `setup.py:initialize_profile` 在单一 `.cc.json.update.lock` 事务内调用 `ensure_knowledge_binding(..., already_locked=True)`，生成全部 9 个领域的 `engines/cc/<20hex>/<domain>.engine.json` 配置（默认 `engine_config` 保持 `vllm-ascend.engine.json`，遇 `deferred` 时保留原 `cc.json` 不变）；`host_adapter.py:adapter_plugin_root` 同时兼容源码检出与 Wheel 安装（`mindie_cc`）布局下的 `scripts/transcript.py` 解析。
- **静默语义 Skill（`skills/mindie-agent/SKILL.md`）**：声明 `user-invocable: false`，从斜杠菜单隐藏命令入口，仅在涉及昇腾 NPU 研发任务时由 Claude 按需语义加载。

### 可选 `PreToolUse` 身份提示与连接级回退
在主会话工具调用前，被动的 `PreToolUse` Hook（冷路径 `< 30ms` 返回 `{}` 且不读 stdin）与紧随其后的 MCP 请求 `_meta["claudecode/toolUseId"]` 协同工作：
- 若存在匹配的 `PreToolUse` 绑定记录，`scripts/entry.py` 的 `ManagedMCPAdapter("cc")` 单次消费并严格校验工具名与参数摘要（拒绝重放或篡改绑定）。
- 若未触发 `PreToolUse`（如 `Task` 子智能体或直接 MCP 调用），`scripts/entry.py` 的 `ManagedMCPAdapter("cc")` 平滑回退至已验证的 `_meta`/环境会话 ID 或连接级 `_CONNECTION_SESSION_ID`。
- 新建会话与 `--fork-session` 分支会话各自拥有独立会话身份，同时共享 `REMOTE_DEV_STATE_DIR`（`state_dir() / "remote"`）以支持子智能体轮询或停止父会话 `job_id`。

---

## 2. `Stop` Hook 与公开对话提取

### 快速、同意优先且保留撤权永久性的 `Stop` 交接
`Stop` Hook 在有界交接预算（`timeout_sec=6.0`）内静默返回 `{}`，绝不向业务大模型返回“继续执行”指令：
- **当社区贡献未配置、已关闭或 `cwd` 超出授权 `project_roots` 时**：`Stop` 先校验 `mindie-consent.json` 与 `community_config`，在 `< 30ms` 内直接短路返回 `{}`——不打开或创建 `admission.sqlite3`、不读取对话文件、不创建采集记录，也不发起任何整理模型调用。
- **当社区贡献已开启且当前项目处于授权 `project_roots` 内时**：`entry.py:_stop` 优先检查 `Admission(profile.admission_db)`——若会话已被显式撤销（`status == "inactive"`），直接返回 `{}` 且永不复活该会话；否则通过 `transcript.capture_source` 校验当前 Claude Profile 下的会话文件与创建边界，并委托 `mindie_harness.runtime.knowledge_binding.run_stop_capture(..., timeout_sec=6.0)` 完成有界入队。
- 写入采集队列仅代表完成本地准入；后续的分块索引生成与 GitHub PR 发布由后台 Worker 独立生成回执。任何失败或结果不确定的模型调用均保留真实记录，绝不静默重放。

### 确定性的公开消息筛选
仅已获授权会话中用户与助手的公开可见文本会被纳入采集范围：
- 解析器仅提取带有效 `sessionId`、时间戳的公开文本块。
- 内部思考过程（Thinking blocks）、工具调用输入与返回载荷、`<command-...>` 标签块、注入的 `isMeta` 消息、附件、系统记录与支线会话（Sidechains）均被确定性剔除。
- 采集起点不早于 `max(created_at, activated_at)`——即“会话/Fork 初始 `enqueue` 创建时间”与租约 `activated_at` 边界中的较晚者。

---

## 3. 组件职责划分与平滑更新

| 层级 | 核心职责 |
|---|---|
| **Claude Code 宿主** | 原生插件市场目录、安装/卸载状态记录、Hook 事件投递与 MCP stdio 传输。 |
| **`mindie-agent-cc`（本适配层）** | 原生插件清单（`.claude-plugin/`、`.mcp.json`、`hooks/`、`skills/`）、8 个核心模块 + 4 个 `v0.1.29` 在线升级兼容桩（共 `1,344` 行 `<= 1,500`）、分叉感知 `transcript.py` 解析，以及 `CLAUDE_PLUGIN_DATA` 下的固定 Python 3.13 运行时交接。 |
| **共享底座与运行时组件** | `mindie-harness`（`ManagedMCPAdapter`、`ensure_knowledge_binding`、`run_stop_capture`、种子与 wheelhouse 更新、RSI）、`mindie-knowledge`（9 领域 `L1–L4` 检索、脱敏、采集与 PR 闭环）、`remote-dev`（SSH 远端执行与作业轮询）、`mindie-diagnostics`、`mindie-coordinator`、`mindie-debugger`、`mindie-profiler`、`mindie-sim` 与 `npu-top 0.3.5`。 |

在版本更新时，新版本会在独立目录中准备好完整的源码、锁文件与 Python 运行时；在途操作在共享锁保护下继续完成，待核心服务空闲时再原子切换绑定。若候选版本准备失败，原有版本保持完好可用，不会陷入死循环重试。

---

## 4. 验证范围说明

- **已验证能力**：覆盖 Claude Code（`>= 2.1.269 < 3.0.0`）原生接口探针、三平台（`ubuntu-latest`、`macos-latest`、`windows-latest`）锁定依赖契约测试（`tests/test_launch_and_mcp_surfaces.py`、`tests/test_thin_plugin_contract.py`、`tests/test_legacy_self_upgrade.py`、`tests/test_transcript.py`），以及 `.github/workflows/checks.yml` 接入的 `mindie-agent/mindie-bench/.github/workflows/verify-host-plugin.yml@main`（`host: cc`）自动化原生宿主验收。
- **验收边界**：自动化 CI 与宿主验收完整验证正交 MCP 工具面、冷启动/撤权契约与树外 Wheel 构建；远端昇腾 A2/A3 NPU 实机基准评测在 `mindie-bench` 中独立管理（详见 [框架稳定性与测试契约](framework-stability.zh.md)）。
- **Claude Code 官方文档参考**：[Hooks](https://code.claude.com/docs/en/hooks) · [Plugins](https://code.claude.com/docs/en/plugins-reference) · [Skills](https://code.claude.com/docs/en/skills)
