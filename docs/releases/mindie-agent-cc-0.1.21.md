# mindie-agent-cc 0.1.21：后台更新阶段归因、传输分类与退役组件清理

[English](mindie-agent-cc-0.1.21.en.md)

后台更新与运行时准备子进程（`locked-runtime-install`、`component-floor`、`component_update`、`binding_prepare`、`runtime_activation`）统一启用传输层错误分类并透传真实失败阶段，避免子进程网络波动被笼统记录为 `stage=native_update` 的内部故障（`mindie-agent#342`），同时保证源码完整性校验异常继续作为可上报内部故障保留。

独立组件发现与跨插件版本继承流程自动过滤已退役的旧组件条目（`mindie-bench`），防止从历史版本升级时因已退役组件目录或绑定残留阻断组件更新。

版本清单、锁定文件、原生 hook 与 MCP 引导路径同步推进至 `0.1.21`。
