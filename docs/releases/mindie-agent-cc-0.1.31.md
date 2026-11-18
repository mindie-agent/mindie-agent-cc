# mindie-agent-cc 0.1.31：Pre-`0.1.30` 在线平滑自升级交接兼容修复

[English](mindie-agent-cc-0.1.31.en.md)

- **恢复 `v0.1.29 -> v0.1.31` 原生插件平滑自升级（Issue [`mindie-agent#378`](https://github.com/mindie-agent/mindie-agent/issues/378)）**：补齐纯标准库 `scripts/component_update.py` 交接桩（供 `v0.1.29` `bootstrap._component_override` 在旧解释器下探测并回退至 `v0.1.31` 锁定基线运行时），并在 `scripts/setup.py` 中支持旧版 `--source <installed> --config <base_config>` 阶段化准备与 `--activate <prepared_config>` 原子切流（含 `<state_dir>/update/operation.lock` 排他锁、`component_source_commands` / `domain_source_configs` CAS 校验与旧知识服务空闲退役）。
- **保持完整 `mindie-cc-installation/1` 绑定不变量**：`entry.py:initialize_profile` 与 `setup.py` 始终保持 `base_config`、`adapter_config`、`state_dir`、`engine_config`、`python`、`runtime_scripts` 与 `community_config` 字段完备一致，确保旧会话收尾与新 `mindie-harness` 会话无缝共处。
- **自动化 `n-1 -> n` 自升级契约回归测试**：新增 `tests/test_legacy_self_upgrade.py`，在隔离环境中端到端覆盖 `component_update.py` -> `setup.py --source ... --config ...` -> `v0.1.29 read_binding/_complete_binding` 校验 -> `setup.py --activate`（含并发 `operation.lock` 延迟与 `component_commands` CAS 保护）-> `mindie_launch.py` 运行时解析全链路。
