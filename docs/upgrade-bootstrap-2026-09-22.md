# Upgrade from the pre-diagnostics Claude adapter

The updater at `a9ae74a412367e67594991f5ba2c9c8fe47e86be` copies only
`mindie_launch.py` and `bounded.py` into a versioned launch directory. Applying
that real packager to `4c4c0b928e36a280ae76400c9828b15f8fd70a97` reproduced
`ModuleNotFoundError: diagnostic_support` before a sharing-off Stop could run.
A fresh installation did not expose this upgrade failure.

The launcher now loads the diagnostic module from its own directory, or from
its exact SHA's existing generation after checking the completion marker.
It does not select a module through `current.json`, ambient `PYTHONPATH`, a
network install, or changes to an existing launch file. When older packaging
has not emitted `diagnostic-build.json`, the module reads the same generation's
completion record and stamped native manifest. Missing version facts remain
absent. Unrelated module copies and symlink metadata are not accepted.

Implementation provenance: these are narrow direct corrections after the
actual old copier and import failure were confirmed, with independent review.
They do not change admission, Hook budgets, retries, service startup or upload
consent.

The macOS evidence is kept separately from component checks:

- Claude Code 2.1.269 installed the old native package into a new isolated
  profile. The old updater then staged candidate `72cfefc3d9721f0c3120bdc6f032c1f0662c7ebf`,
  built its dependencies from the unchanged canonical Git pins, and completed
  the native switch in 6.90 seconds. The launcher directory still had only the
  two old-packager files, and no diagnostic-build file was added as a rescue.
- Strict readback confirmed the expected version and absolute launcher/config
  bindings. Both native MCP surfaces connected. Updater status succeeded;
  sharing-off Stop returned `{}` in 25 milliseconds. Metadata reported the
  actual staged SHA and stamped manifest version. A stdlib JSON-RPC client
  subsequently listed four knowledge and eighteen remote tools.
- The first extra tools/list controller assumed an optional MCP SDK existed;
  it failed before sending a request. That failure remains in `result.json`.
  `readonly-supplement.json` records the later stdlib-only check. No native
  install or business tool call was replayed to fix that controller error.
- After the final source review, all 77 component checks passed. An initial
  final-candidate controller incorrectly set a 120-second total budget while
  the old updater reserves rollback, feed and handoff time. Native readback
  was left only 5.39 seconds and timed out. The previous native package and
  current tuple were confirmed restored. This controller failure is retained
  in `final-candidate.json` and `final-failure-readback.json`.
- With the controller restored to the approved 580-second outer budget, the
  old packager staged final product commit
  `4ecddf703f1466e21ca55d770dbe82aadb992a87` and the native switch completed
  in 7.09 seconds. This run reused the prior candidate's unchanged official
  dependency interpreter; no dependency substitute was introduced. Exact
  native readback, both MCP connections, updater status, sharing-off Stop,
  and same-generation version metadata passed. The final stdlib handshake
  again listed 4 knowledge and 18 remote tools. Evidence is
  `final-candidate-v2.json` and `final-readonly-supplement.json`. The earlier
  controller failures remain separate; there was no automatic retry.

Evidence directory: `acceptance/cc-upgrade-bootstrap-v1` in the local DFX work
root. No model, SSH operation, sharing consent, reporting consent, admission
state, scheduler registration or production profile change was used. The
isolated profile has no copied provider credentials; owned processes exited.
Windows hardware was not exercised.

The old native packager also has a fixed six-command expansion matcher list.
It copies the new reporting skills but does not register their three new
UserPromptExpansion matchers. This bootstrap correction restores the existing
tools; it does not prove those new commands work in an old-generated package.
A native package produced by the newer packager is required for those entries.
