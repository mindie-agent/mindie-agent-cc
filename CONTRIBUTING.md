# 贡献指南 (`mindie-agent-cc`)

English version: [`CONTRIBUTING.en.md`](CONTRIBUTING.en.md)

感谢参与 `mindie-agent-cc`（Claude Code 原生插件适配层）开发。提交变更前请遵循以下工程准则：

## 1. 核心架构与红线

1. **静默语义 Skill 与薄适配层边界（`HOST-NATIVE` / `HOST-WIRE`）**：
   - `skills/` 下仅保留单一 `skills/mindie-agent/` 目录（`user-invocable: false`），由宿主按需语义加载，不向用户暴露斜杠命令负担。
   - `.mcp.json` 直接调用 `${CLAUDE_PLUGIN_ROOT}/scripts/plugin_entry.py`，禁止内联 `python -c` 脚本片段。
   - 共享锁、原子写与 RSI 循环委托给 `mindie-harness`（并校验打包的 `assets/seed-lock.json`），本仓仅维护 Claude Code 专有的钩子提示、分叉感知转录提取（`transcript.py`）与宿主插件安装协调。
2. **默认就绪 MCP、显式同意采集与跨子代理作业可见性（`HOST-ADMISSION`）**：
   - `knowledge`、`remote` 与 `components` 工具均默认就绪且零前置会话绑定门禁，只读调用不创建空的 `admission.sqlite3`；`REMOTE_DEV_STATE_DIR` 共享 `state_dir() / "remote"`，确保子代理与重连会话可查询和终止父会话启动的 `job_id`。
   - `Stop` Hook 在打开 `admission.sqlite3` 或拉起采集进程前优先校验 `mindie-consent.json` 与授权 `project_roots`，未配置、已关闭或超出范围时在 `< 100ms` 内静默退出且零 SQLite 副作用。
3. **升级状态诚实与故障分类（`HOST-UPGRADE`）**：
   - 外部 `claude plugin` CLI 退出码非零但已正常回收时，必须记录 `native_effect = "failed_cleanly"`（允许下一轮自动重试 `marketplace add`），严禁标记为 `"completed"` 或 `"unknown"`（`"unknown"` 仅限超时/未结算子进程），且外部 CLI/Git/网络故障一律标记 `reportable=False`。
   - 保持 `installation.json` / `cc.json` 跨版本升级与回滚兼容；主操作成功后本地记账失败必须保留 `isError=False` 并附加 `receipt_warning` + `receipt_unconfirmed=True`。
4. **行尾与模块规范**：
   - 所有文本文件统一采用 Unix `LF`（`\n`）行尾（通过 `python3 .github/scripts/check_lf_eol.py` 校验）。
   - `scripts/` 仅保留 8 个薄文件，Python 总行数保持 `<= 1,200`（目标 `<= 1,050`），并在前 30 行内包含 `[Role]` / `[Invariants]` / `[Timeouts & Locks]` / `[Side-Effect Policy]` 契约头。

## 2. 提交前本地检查

```bash
python3 .github/scripts/check_lf_eol.py
pytest -q -m "not live_npu"
python3 scripts/setup.py release-metadata --source . --tests tests --revision "$(git rev-parse HEAD)"
python3 .github/scripts/check_pr_checklist.py --profile host-plugin --body-file <pr_body.md>
```
