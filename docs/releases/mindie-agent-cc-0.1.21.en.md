# mindie-agent-cc 0.1.21: update stage attribution, transport classification, and retired component filtering

[中文](mindie-agent-cc-0.1.21.md)

Background update and runtime preparation subprocesses (`locked-runtime-install`, `component-floor`, `component_update`, `binding_prepare`, and `runtime_activation`) now classify transport errors and preserve the actual failing stage instead of collapsing post-install child failures into `stage=native_update` internal faults (`mindie-agent#342`), while keeping release source-integrity mismatches reportable.

Independent component discovery and cross-plugin inheritance filter retired legacy component inventory (`mindie-bench`) so upgrades from older profiles never stall on retired bindings.

Version manifests, lockfile, native hooks, and MCP bootstrap paths advance together to `0.1.21`.
