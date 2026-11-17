# 贡献指南 (`mindie-agent-cc`)

English version: [`CONTRIBUTING.en.md`](CONTRIBUTING.en.md)

感谢参与 `mindie-agent-cc`（Claude Code 原生插件适配层）开发。提交变更前请遵循以下工程准则：

## 1. 核心架构与红线

1. **单一入口与薄适配层边界（`HOST-NATIVE` / `HOST-WIRE`）**：
   - `skills/` 下仅保留单一 `skills/mindie-agent/` 入口，子命令（`status`、`deactivate`、`recover`、`sharing-*`、`reporting-*`）统一经由 `/mindie-agent <subcommand>` 分发。
   - `.mcp.json` 直接调用 `${CLAUDE_PLUGIN_ROOT}/scripts/plugin_entry.py`，禁止内联 `python -c` 脚本片段。
   - 共享锁、原子写与 RSI 循环委托给 `mindie-harness`（并校验打包的 `assets/seed-lock.json`），本仓仅维护 Claude Code 专有的钩子绑定、分叉感知转录提取（`transcript.py`）与宿主插件安装协调。
2. **准入门禁与跨子代理作业可见性（`HOST-ADMISSION`）**：
   - `remote` 与 `components` 工具满足 `P2.7` 零前置会话门禁；`REMOTE_DEV_STATE_DIR` 共享 `state_dir() / "remote"`，确保子代理与重连会话可查询和终止父会话启动的 `job_id`。
   - `knowledge_*` 工具严格受 `P2.1` `/mindie-agent` 显式会话租约保护，未激活会话不得隐式放行或创建空的 `admission.sqlite3`。
3. **升级状态诚实与故障分类（`HOST-UPGRADE`）**：
   - 外部 `claude plugin` CLI 退出码非零但已正常回收时，必须记录 `native_effect = "failed_cleanly"`（允许下一轮自动重试 `marketplace add`），严禁标记为 `"completed"` 或 `"unknown"`（`"unknown"` 仅限超时/未结算子进程），且外部 CLI/Git/网络故障一律标记 `reportable=False`。
   - 保持 `installation.json` / `cc.json` 跨版本升级与回滚兼容；主操作成功后本地记账失败必须保留 `isError=False`。
4. **行尾与模块规范**：
   - 所有文本文件统一采用 Unix `LF`（`\n`）行尾（通过 `python3 .github/scripts/check_lf_eol.py` 校验）。
   - 核心 Python 模块保持 `< 800` 行，并在前 30 行内包含 `[Role]` / `[Invariants]` / `[Timeouts & Locks]` / `[Side-Effect Policy]` 契约头。

## 2. 提交前本地检查

```bash
python3 .github/scripts/check_lf_eol.py
pytest -q -m "not live_npu"
python3 scripts/harness.py --source . --tests tests --revision "$(git rev-parse HEAD)"
python3 .github/scripts/check_pr_checklist.py --profile host-plugin --body-file <pr_body.md>
```
