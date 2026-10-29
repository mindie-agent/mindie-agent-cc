# mindie-agent-cc 0.1.9

`mindie-agent-cc 0.1.9` (paired with `mindie-agent 0.1.3`) completes the native three-server MCP registration and tool-contract surface (`knowledge`, `remote`, and `components`) for Claude Code alongside the persistent SSH authentication integration from `remote-dev 0.11.1`.

## 1. Key Changes & Behavior

### 1. Three-Server Native MCP Registration & `components` Surface (PR `#52`)

- `.mcp.json` registers three stdio MCP servers—`knowledge`, `remote`, and `components`—all routed through the immutable `CLAUDE_PLUGIN_DATA/releases/0.1.9/entry.py` runtime entrypoint with `MCP_TOOL_TIMEOUT=660000` (`11 minutes`), preserving `outer_timeout >= inner_timeout (600s) + cleanup_grace`.
- The `components` MCP server (`scripts/component_tools.py` & `scripts/mcp_server.py`) exposes 5 always-on tools without requiring `/mindie-agent` session activation:
  - `diagnostics_status` and `diagnostics_bundle` (backed directly by bundled `mindie-diagnostics 0.5.4`);
  - `coordinator`, `profiler`, and `sim` CLI bridges (discovering optional `mindie-coordinator`, `mindie-profiler`, and `mindie-sim` commands in the configured Python environment, `PATH`, or `component_commands`, with bounded output budgets and process-group termination on timeout).
- The `knowledge` MCP server Remains strictly gated by explicit `/mindie-agent` activation in the current Claude Code session (`PreToolUse` native session identity binding), while `remote` and `components` are available by default.

### 2. Active Runtime & Test Component Pins

| Component | Role | Version / Tag | Source Commit |
| --- | --- | --- | --- |
| `mindie-knowledge` | Runtime dependency | `0.13.4` / `v0.13.4` | `6e921b205c00d592c25f849653cbfb72b73fe318` |
| `remote-dev` | Runtime dependency | `0.11.1` / `v0.11.1` | `2a750ec8c74d7f053410d48dd820aeb542e7d3b1` |
| `mindie-diagnostics` | Runtime dependency | `0.5.4` / `v0.5.4` | `5ae643e19223f2181ecdbaa3edaf464137c4805e` |
| `mindie-profiler` | Test & optional CLI pin | `0.5.1` / `v0.5.1` | `25c789cd42389c893234c5db20eca67e63b8878a` |
| `mindie-sim` | Test & optional CLI pin | `0.7.1` / `v0.7.1` | `15f93f3f0247b2653d0e9441eae56959e4d16d4e` |
| `mindie-coordinator` | Test & optional CLI pin | `0.7.0` / `v0.7.0` | `e4a86c972a99c442c78ad7f7ba627af7da5ef7f2` |

## 2. Verification & Evidence Scope

- Verified via the locked multi-platform CI matrix (Ubuntu, macOS, Windows), `scripts/product_contract.py`, `scripts/harness.py`, and MCP `tools/list` + `tools/call` stdio protocol tests (`tests/test_mcp.py`, `tests/test_component_tools.py`).
- Formal tag release workflows attach `mindie-plugin-release.json`, `benchmark-results.json`, `benchmark-results.md`, and `SHA256SUMS` built from the exact tagged commit.
