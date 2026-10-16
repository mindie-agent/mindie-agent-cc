# MindIE Agent for Claude Code · Documentation Index

<a href="README.md">中文</a> | <b>English</b> · <a href="../README.en.md">Back to Repository Home</a>

This directory contains the architecture guides, Harness interface verification records, service continuity design, failure diagnostics, and testing specifications for `mindie-agent-cc`. Every document is available in both English and Chinese.

## Technical Guides

| English Document | 中文 | Summary |
|---|---|---|
| [Native Plugin & Runtime Boundary](native-boundary.md) | [原生插件与运行时边界](native-boundary.zh.md) | Task identity binding, `PreToolUse` / `Stop` hook mechanics, public transcript filtering, and shared component boundaries |
| [Harness Interface Evidence](harness-interface-evidence.md) | [Harness 接口兼容性与验证证据](harness-interface-evidence.zh.md) | Registered Claude Code interfaces in `harness-compatibility.json`, structured-output retry boundaries, and `--fork-session` attribution |
| [Service Continuity & Updates](service-continuity.md) | [服务连续性与平滑更新](service-continuity.zh.md) | Immutable `uv` runtime preparation, shared/exclusive admission locks, idle service handoff, and `Stop` queue durability |
| [Failure Diagnostics & Cancellation](failure-diagnostics.md) | [故障诊断与本地进程取消](failure-diagnostics.zh.md) | Read-only status checks, local process cleanup on cancel, sanitized failure projections, and opt-in product fault reporting |
| [Framework Stability & Testing](framework-stability.md) | [框架稳定性与测试契约](framework-stability.zh.md) | Everyday usage invariants, Windows atomic configuration I/O, reproducible component tests, and acceptance boundaries |
