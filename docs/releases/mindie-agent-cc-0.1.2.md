# mindie-agent-cc 0.1.2 Release Notes

**中文** | [English](https://github.com/mindie-agent/mindie-agent-cc/blob/v0.1.2/docs/releases/mindie-agent-cc-0.1.2.en.md)

`mindie-agent-cc 0.1.2` 是面向 Claude Code 的维护更新版本（配套主产品 `mindie-agent 0.1.1`，保留既有 `0.1.1` 插件标签不变），新增共享运行时组件的独立正式 Release 自动更新能力，并收敛无模型冷启动切换与按会话手动开启 `/mindie-agent` 知识循环的边界。

## 核心更新

- **共享组件独立自动更新与组件版本下界继承**：
  - 当没有更高版本的插件 Release 需要切换时，后台更新检查直接从各官方组件仓库发现正式 GitHub Release，按“当前已安装版本、插件基线版本、组件最新正式 Release”三者最大值选定目标组件版本，在旁路隔离目录构建带完整 `uv.lock` 的独立运行环境并保留原生插件包。
  - 当插件自身升级到新版本 Release 时，自动继承当前已安装组件的版本下界，避免插件升级将已独立更新的组件回退到旧基线。
- **无模型候选校验、进程内可取消检查与空闲原子切换**：
  - 移除候选准备阶段拉起知识后台服务的逻辑；改用隔离包清单校验、无模型候选运行时探针、空闲服务退役与安装绑定配置文件（默认 `~/.config/mindie-agent/cc.json`）冷态原子发布，仅在后续已授权的手动知识调用时按需启动后台知识服务。
  - `scripts/release_update.py` 中的 `BackgroundCheck` 在现有常驻 MCP 进程内按 `CHECK_INTERVAL = 300`（300 秒）运行可取消等待循环（对 `deferred` / `incomplete` 组件状态采用激活退避），不新增独立知识 worker 或后台守护进程。
- **探索上下文原样透传与会话显式授权边界**：
  - 知识检索的探索载荷直接透传 `mindie-knowledge` 引擎输出，适配层不再二次剥离或重建事实包；后台更新绝不自动授予会话知识权限或修改已保存的用户贡献同意，会话知识能力仍仅在当前会话手动输入 `/mindie-agent` 后生效。

## 运行时组件与验证范围

- **配套运行时组件**：锁定正式发布的 `mindie-knowledge 0.13.3`（`38a42f6f778cfa555d8918fdcacd12cee4a181f5`）、`remote-dev 0.10.6`（`0463a13fce9618ec9a51904869d61acb0cdb8538`）与 `mindie-diagnostics 0.5.2`（`801a01b2789aed7f58e971a909205a7d40b61077`）；兼容的 Claude Code 版本范围以随包发布的 `uv.lock`、`product-contract.json` 与 `harness-compatibility.json` 为准。
- **验证与验收层级**：
  1. **macOS `arm64` 原生候选实测（Claude Code `2.1.269`）**：默认新会话直接调用 `knowledge_query` 在执行前被拒绝、不初始化知识服务且不重试；显式输入 `/mindie-agent` 正常激活 `entry_enabled=1` 并由同会话续跑继承，独立新建会话不继承该授权；默认 `remote_job_status` 直接走确定性远端路径（查询不存在的 `job_id` 正常返回调用方未找到错误，不受知识门禁阻塞）。在隔离 `CLAUDE_CONFIG_DIR` 原生安装下完成组件独立更新，实测对齐 `mindie-knowledge 0.13.3`、`remote-dev 0.10.6` 与 `mindie-diagnostics 0.5.2`（保留原生插件包），更新后新拉起 CLI 会话正常就绪；`cc.json` 绑定切换阶段约 `15ms`（`activate_binding.seconds`，不含前置构建）。
  2. **自动化测试与各 OS CI**：组件更新与候选校验测试（`13 passed, 31 subtests`）及发布更新测试套件（`62 passed`）通过，跨平台契约由各 OS CI 流水线覆盖。
  3. **正式发布后回读待办**：待四端 `0.1.2` 正式 GitHub Release 发布后，执行面向已发布正式包的本地自动更新回读确认。
