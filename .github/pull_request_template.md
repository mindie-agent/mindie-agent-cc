## 变更摘要 (Summary)

- 简述本次变更解决的问题、影响范围与核心设计取舍：

## 设计原则 Review (Pre-PR Checklist — `host-plugin`)

- [ ] **HOST-NATIVE** — 原生宿主契约保持不变：静默语义 `skills/mindie-agent`（`user-invocable: false`）、MCP 服务（`knowledge`、`remote`、`components`）、分叉感知转录投影及 Windows 零弹窗（`CREATE_NO_WINDOW`）全部通过校验。
- [ ] **HOST-WIRE** — 薄适配层委托与协议边界：共享锁、原子写与 RSI 已委托 `mindie-harness`（含 `assets/seed-lock.json` 校验），`.mcp.json` 通过 `scripts/plugin_entry.py` 启动且无 `python -c` 内联脚本，模块行数 `< 800` 且具备顶部四段契约注释。
- [ ] **HOST-ADMISSION** — 准入与跨会话可见性：全量 MCP 工具（含 `knowledge_*`、`remote`、`components`）默认就绪且零前置会话绑定门禁，`REMOTE_DEV_STATE_DIR` 共享 `state_dir() / "remote"` 以支持子代理查询/停止父 `job_id`（`AD-02`），`Stop` 采集优先校验 `mindie-consent.json` 与授权 `project_roots`。
- [ ] **HOST-UPGRADE** — 升级/回滚连续性与收据诚实：兼容 `installation.json` / `cc.json` 布局，`release_update.py` 正确区分 `failed_cleanly` / `completed` / `unknown` 且外部 CLI 故障标记 `reportable=False`（`#364/#365`），主操作成功后本地记账异常保留 `isError=False`。

### 自查证据 (Evidence)

- **HOST-NATIVE**:
- **HOST-WIRE**:
- **HOST-ADMISSION**:
- **HOST-UPGRADE**:

## 验证结果 (Verification)

- 本地测试、构建冒烟与静态门禁执行命令及结果：
