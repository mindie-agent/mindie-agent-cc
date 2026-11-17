# mindie-agent-cc 0.1.28：运行时 Skill 与安装引导解耦及显式手动触发边界收口

[English](mindie-agent-cc-0.1.28.en.md)

- **安装与授权文档迁出运行时 Skill 目录**：将首次安装与授权说明文档（`workspace-setup.md`、`github-authentication.md`、`user-notice.md`）由 `skills/mindie-agent/references/` 归位至仓库级 `docs/install/`，同步更新中英 `README` 与文档索引引用。
- **纯净运行时 `SKILL.md` 与操作参考边界**：从 `skills/mindie-agent/SKILL.md` 与 `skills/mindie-agent/references/setup-and-operations.md` 移除首次安装引导、`AskUserQuestion` 首次配置弹窗、`vllm-ascend-workspace` 克隆初始化、`gh auth login` 登录说明、`setup.py sharing` 引导流程与 GitHub Star 提示，仅保留运行时会话子命令、多领域路由、分层知识 schema 与剖析租约规范。
- **保留显式手动触发契约**：继续在 `skills/mindie-agent/SKILL.md` frontmatter 中维持 `disable-model-invocation: true`，确保知识系统严格受 `/mindie-agent` 会话租约（`P2.1`）保护。
