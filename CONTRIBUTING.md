# 贡献指南 (`mindie-agent-cc`)

English version: [`CONTRIBUTING.en.md`](CONTRIBUTING.en.md)

感谢参与 `mindie-agent-cc`（Claude Code 原生插件适配层，`v0.1.32`）开发。提交变更前请遵循以下工程准则：

## 1. 核心架构与红线

1. **静默语义 Skill 与薄适配层边界（`HOST-NATIVE` / `HOST-WIRE`）**：
   - `skills/` 下仅保留单一 `skills/mindie-agent/` 目录（`user-invocable: false`），由宿主按需语义加载，不向用户暴露斜杠命令负担。
   - `.mcp.json` 与 `hooks/hooks.json` 直接调用 `${CLAUDE_PLUGIN_ROOT}/scripts/plugin_entry.py`，禁止内联 `python -c` 脚本片段。
   - 共享锁、原子写、9 领域知识绑定（`ensure_knowledge_binding(..., already_locked=True)`，生成内容寻址的 `engines/cc/<20hex>/<domain>.engine.json`）、MCP 路由与 RSI 循环委托给 `mindie-harness`（校验 `assets/seed-lock.json`，并在 [`pyproject.toml`](pyproject.toml) 与 [`product-contract.json`](product-contract.json) 中完整锁定含 `npu-top 0.3.5` 在内的全部 9 个运行时包）；`host_adapter.py:adapter_plugin_root` 负责在源码检出与 Wheel 安装布局下统一暴露 `scripts/transcript.py`。
   - 冷态 `SessionStart` 保持 `< 30ms` 纯标记写入（仅写 `pending-update-check.json`，零 `uv sync`、零 SQLite）；冷启动种子引导使用 `stdin=subprocess.DEVNULL` 并在未产出有效运行时解释器（剔除 `WindowsApps` 空别名）时显式非零失败退出。
2. **正交 MCP 工具面、显式同意采集与撤权永久性（`HOST-ADMISSION`）**：
   - 三套 MCP 服务严格正交且默认零前置门禁就绪：`knowledge` 仅暴露 5 个 `knowledge_*` 工具；`components` 暴露 11 个本地/组件工具（`mindie_status`、`mindie_doctor`、`diagnostics_status`、`diagnostics_bundle`、`profiler`、`sim`、`coordinator`、`npu_top`、`debugger`、`rsi_loop`、`experiment_ledger`）；`remote` 暴露 13 个 `claude` 工具。只读调用不创建空的 `admission.sqlite3`；`REMOTE_DEV_STATE_DIR` 共享 `state_dir() / "remote"`，确保子代理与重连会话可查询和终止父会话启动的 `job_id`。
   - `Stop` Hook 在打开 `admission.sqlite3` 或拉起采集进程前优先校验 `mindie-consent.json` 与授权 `project_roots`（未配置、已关闭或越界时在 `< 30ms` 内静默退出且零 SQLite 副作用），并通过 `Admission.inspect(session)` 与 `run_stop_capture` 保证已显式停用（`inactive`）的会话永不被复活。
3. **升级状态诚实与故障分类（`HOST-UPGRADE`）**：
   - 外部 `claude plugin` CLI 退出码非零但已正常回收时，必须记录 `native_effect = "failed_cleanly"`（允许下一轮自动重试 `marketplace add`），严禁标记为 `"completed"` 或 `"unknown"`（`"unknown"` 仅限超时/未结算子进程），且外部 CLI/Git/网络故障一律标记 `reportable=False`。
   - 保持 `installation.json` / `cc.json` 跨版本升级与回滚兼容（含 `v0.1.29 -> v0.1.32` 在线自升级兼容桩与单锁 `deferred` 保护）；主操作成功后本地记账失败必须保留 `isError=False` 并附加 `receipt_warning` + `receipt_unconfirmed=True`。
4. **行尾与模块预算**：
   - 所有文本文件统一采用 Unix `LF`（`\n`）行尾（通过 `python3 .github/scripts/check_lf_eol.py` 校验）。
   - `scripts/` 保留 8 个核心薄文件及 4 个 `v0.1.29` 在线升级兼容桩，Python 总行数保持 `<= 1,350` 目标 / `<= 1,500` 硬上限（当前 `1,344` 行），并在每个模块前 30 行内包含 `[Role]` / `[Invariants]` / `[Timeouts & Locks]` / `[Side-Effect Policy]` 契约头；CI 通过 `verify-host-plugin.yml`（`host: cc`）执行原生宿主验收。

## 2. 提交前本地检查

```bash
python3 .github/scripts/check_lf_eol.py
pytest -q -m "not live_npu"
python3 scripts/setup.py release-metadata --source . --tests tests --revision "$(git rev-parse HEAD)"
python3 .github/scripts/check_pr_checklist.py --profile host-plugin --body-file <pr_body.md>
```
