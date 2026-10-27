# mindie-agent-cc 0.1.7

本版本固定正式 remote-dev 0.10.8（`4b1c5cab5d36cdaff79d27e4288c6d5f60667c72`），补齐远端工具的端点参数说明，并将共享组件的 `input_repair` 传回 Claude Code。

直接 SSH 端点需同时传入 host 与 port；port=22 仅是示例。alias、已注册 resolver 与完整省略端点字段时的配置默认值继续受支持。漏传 port 或操作参数时，模型可见文本明确指出缺少的字段与纠正动作，结构化结果保留 `category=caller`、`submission_state=not_sent` 与 `input_repair`。适配器不会自动重放请求，也不会把传输或副作用不确定的失败改写成可安全重试的缺参错误。

合同测试通过真实共享 resolve_endpoint/call_tool 产生缺参异常，检查公开错误、保密异常文本过滤和未提交语义。此版本的 Claude Code 覆盖是组件合同与三系统 CI；真实模型自主补齐 port 的发布前实测在 Codex CLI 与 DSH 的原生 MCP 客户端中完成，不据此声称已重新验证 Claude Code 的完整宿主升级路径。正式发布附带精确 tag 的测试、微基准与校验和。

依赖同时保持 mindie-knowledge 0.13.4（`6e921b205c00d592c25f849653cbfb72b73fe318`）及 mindie-diagnostics 0.5.4（`5ae643e19223f2181ecdbaa3edaf464137c4805e`）。现有会话授权、MCP 超时和空闲升级路径保持原契约；安装版本与宿主加载版本分别核验。
