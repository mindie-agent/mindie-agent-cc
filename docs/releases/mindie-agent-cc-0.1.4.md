# mindie-agent-cc 0.1.4

**中文** · [English](mindie-agent-cc-0.1.4.en.md)

修复共享组件曾独立更新后，Claude Code 插件自动升级的组件继承入口失败。`bootstrap.py` 以 `python -I` 启动 `component_update.py`，但该脚本此前没有初始化自身目录的模块搜索路径，导致 `ModuleNotFoundError: No module named 'product_contract'`。本版本绑定候选脚本目录、保留隔离模式，并向该子进程传入 `-B`。

候选运行时校验现在实际启动隔离的组件更新 CLI，通过无网络、无模型的 `--help` 路径检查导入边界。真实子进程回归覆盖 Unicode/空格路径、环境 `PYTHONPATH` 干扰，以及候选辅助模块缺失的负对照；完整本地非 NPU 套件通过，三平台合同与正式发布基准由 Tag 流水线交付。

组件继续锁定正式 Release `knowledge 0.13.4`、`remote-dev 0.10.6`、`diagnostics 0.5.2`。升级不授予知识会话权限，不改变贡献同意，不修改 Claude 自身配置。其他设备的原生自动更新验收独立确认；已卡在旧版待激活候选的设备可能仍需一次恢复。
