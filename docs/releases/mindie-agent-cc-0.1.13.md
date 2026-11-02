# mindie-agent-cc 0.1.13

`mindie-agent-cc 0.1.13` makes Claude Code host version checks forward-compatible across the `2.x` line so routine Claude Code updates never break MCP tools or hooks, and upgrades the locked runtime and contract suite to the latest Ascend AI Infra components (`knowledge 0.13.10`, `remote-dev 0.11.4`, `diagnostics 0.5.5`, `debugger 0.1.0`, `profiler 0.5.6`, `sim 0.9.2`, and `coordinator 0.7.3`).

## What's New (Human-Readable Summary)

- **No More Version-Lock Breakage on Claude Code Updates**: Replaces single-point version locks (`2.1.269` and `2.1.299`) in `harness-compatibility.json` with a forward-compatible `2.x` SemVer range (`>=2.1.269 <=2.999.999`, `include_prerelease: true`), allowing minor, patch, and prerelease Claude Code upgrades to start MCP servers, hooks, and background updates seamlessly.
- **Upgraded Component Suite & 950PR / A5 Simulation Support**:
  - Locked runtime: `mindie-knowledge 0.13.10` (`7936a4fda67f6808d8fa73824363a4d7345766e5`), `remote-dev 0.11.4` (`7c67876d716feb3949e4dbf33eb5e06980ff0d36`), and `mindie-diagnostics 0.5.5` (`f2f2f946c172afc99342a84c0c1ed244f6d44e01`).
  - Contract & analysis components: `mindie-debugger 0.1.0` (`e9406b2b178335190d5cb59dff4d6985894497ba`), `mindie-profiler 0.5.6` (`79d97e75bff7351b0472b93c03b072c1cf47f5e7`), `mindie-sim 0.9.2` (`ced2dba5741af2d98f3a75b2a9cad77b9b94c40d`), and `mindie-coordinator 0.7.3` (`79a8b28eef72d57f965a01c6660849f024fbb1c0`).
  - `product-contract.json` `verified_commit` advances to `a0f890b70e73919ffe5d917febcb2595793422ef` (`knowledge-vllm-ascend`, 297 units).
- **Clearer Remote CANN Log Triage**: Refines the `debugger` MCP tool guidance for analyzing `plog` and ACLGraph failures inside remote NPU containers.
