# mindie-agent-cc 0.1.13

This release replaces the single-point Claude Code version locks (`2.1.269` and
`2.1.299`) in `harness-compatibility.json` with a forward-compatible `2.x`
SemVer range (`>=2.1.269 <=2.999.999`, `include_prerelease: true`) so minor,
patch, and prerelease Claude Code upgrades continue to start MCP servers, hooks,
and background updates without self-bricking, and clarifies the remote container
CANN `plog` triage workflow in the `debugger` MCP tool description.

## Runtime and verification

The locked runtime selects `mindie-knowledge 0.13.8`
(`9a4325858e6fadb10715275826a7795843cbbdf4`), `remote-dev 0.11.2`
(`85be999811771e03ecc7d78e5e560fac1636e7ef`), and `mindie-diagnostics 0.5.4`
(`5ae643e19223f2181ecdbaa3edaf464137c4805e`). Optional test component pins
remain `mindie-profiler 0.5.5` (`7cdd56b6cc9e0e7f603368393497487dd2dbfe39`),
`mindie-sim 0.9.0` (`f5608b4943f515c401d2f9b12fffb579a1e2571b`), and
`mindie-coordinator 0.7.1` (`d146c55c414bfb92136cf5fb7fa11ae14c673ef0`).
`product-contract.json` `verified_commit` remains
`1c7bcad61f1156389dcf714468acd74c34754268` (`knowledge-vllm-ascend`).
