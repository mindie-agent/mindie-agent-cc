# mindie-agent-cc 0.1.11

This maintenance release aligns the native component bridge, post-verification
convergence, and `<800`-line module boundaries with `mindie-knowledge 0.13.8`,
`remote-dev 0.11.2`, and `mindie-coordinator 0.7.1`.

## Runtime and verification

The locked runtime selects `mindie-knowledge 0.13.8`
(`9a4325858e6fadb10715275826a7795843cbbdf4`), `remote-dev 0.11.2`
(`85be999811771e03ecc7d78e5e560fac1636e7ef`), and `mindie-diagnostics 0.5.4`
(`5ae643e19223f2181ecdbaa3edaf464137c4805e`). Optional test component pins are
updated to `mindie-profiler 0.5.3` (`22ed2cadff6656c0b26be8cc77ea2bf48a1c0d21`),
`mindie-sim 0.8.1` (`f842e7c0ebbfabae836534152af39665419e7908`), and
`mindie-coordinator 0.7.1` (`d146c55c414bfb92136cf5fb7fa11ae14c673ef0`).
`product-contract.json` `verified_commit` is updated to
`1c7bcad61f1156389dcf714468acd74c34754268` (`knowledge-vllm-ascend`).
