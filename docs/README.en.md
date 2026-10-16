# MindIE Agent Claude Code Plugin Documentation Index

[中文](README.md) | English · [Back to Repository Home](../README.en.md)

This directory contains the architectural boundary, Harness interface evidence, service continuity, failure diagnostics, and framework stability specifications for `mindie-agent-cc`. All documents are available in both English and Chinese.

## Technical Specifications

| English Document | 中文 | Description |
|---|---|---|
| [Native Plugin & Runtime Boundary](native-boundary.md) | [原生插件与运行时边界](native-boundary.zh.md) | Plugin manifest, isolated `CLAUDE_PLUGIN_DATA` runtime, Hook admission, and MCP wiring boundaries |
| [Harness Interface Evidence](harness-interface-evidence.md) | [Harness 接口兼容性证据](harness-interface-evidence.zh.md) | Registered Claude Code interfaces in `harness-compatibility.json`, supported version ranges, and verification evidence |
| [Service Continuity](service-continuity.md) | [服务连续性与空闲切换](service-continuity.zh.md) | Background knowledge service ownership, configuration fingerprint verification, and isolated organizer invocation |
| [Failure Diagnostics](failure-diagnostics.md) | [故障诊断与自动上报](failure-diagnostics.zh.md) | Bounded local structured logs, diagnostic correlation IDs, and opt-in GitHub Issue fault reporting |
| [Framework Stability](framework-stability.md) | [框架稳定性与测试契约](framework-stability.zh.md) | Atomic configuration reads/writes, locked dependency verification, and test suite boundaries |
