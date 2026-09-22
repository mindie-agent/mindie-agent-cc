# Upgrading from the pre-diagnostics Claude adapter

The real updater at `a9ae74a412367e67594991f5ba2c9c8fe47e86be` copies only
`mindie_launch.py` and `bounded.py` into a versioned launch directory. Applying
that packager to `4c4c0b928e36a280ae76400c9828b15f8fd70a97` reproduced
`ModuleNotFoundError: diagnostic_support` before a sharing-off Stop could run.
Its native package also registers only six slash matchers, omitting the three
new reporting commands. A fresh installation did not expose these failures.

## Implemented behavior

The launcher loads the diagnostic module from its own directory, or from its
exact SHA's existing generation after checking the completion marker. It does
not select that module through `current.json`, ambient `PYTHONPATH`, a network
install, or changes to an existing launch file. When an older packager has not
emitted `diagnostic-build.json`, the module reads the same generation's
completion record and stamped native manifest. Missing version facts remain
absent. Unrelated module copies and symlink metadata are rejected.

On the next updater `check`, outside Hooks, the committed generation compares
its native package with its own hooks, MCP declarations, manifest and skills.
An old-generated package receives a separate package directory and a
deterministic `.pkg.<declaration-digest>` native version. The updater retains
the previous package, selects the new package in the committed tuple, and
verifies the complete native hooks/MCP cache. Rollback verifies the previous
package's own declarations. The optional package pointer must belong to this
SHA under `update/native-packages`; an invalid pointer fails visibly instead
of falling back to bootstrap.

Refresh uses the existing check/operation locks, idle check, native installer,
absolute deadline and service handoff. Before package construction it records
an intent in `failed.json`; failure or interruption suppresses another install
for that SHA until explicit recovery. A busy pre-install operation restores
the preceding failure record. Failure to clean up that intent is reported as
`degraded / state-cleanup-failed`, preserving whether native adoption already
succeeded. It does not roll back a confirmed success or automatically install
again. Partial staging directories cannot be reused as completed packages.

Diagnostic `version` continues to identify the code build using its immutable
stamp or same-generation legacy manifest. The native cache's packaging version
may additionally contain `.pkg.<digest>`; both identify the same code revision.
Logging does not consult mutable current state or rewrite old stamps to match
the wrapper version.

Implementation provenance: the bootstrap fixes followed a reproduced import
failure. A single bounded local Grok development run produced the refresh
implementation and focused checks, ending naturally after 412.97 seconds
(exit 1, no restart). Independent review supplied narrow corrections for
partial packages, pointer ownership, package identity and visible cleanup
failures. This does not change admission, Hook budgets or reporting consent.

## Actual macOS acceptance

All runs used Claude Code 2.1.269 and an isolated native profile. Runtime
packages came from the canonical Git pins, without editable installs:

- Knowledge: `87deb0717e9b03603b5ce46b490da14f3119c37d`.
- Remote: `fb441aa18f0dcd5aff9b4a2f94a39dc4070c85e3`.
- Diagnostics: `4a7e50622492c92089c2318d80dbd2a24d41f145`.

The first upgrade used the old controller and old packager, installed the new
bootstrap, connected both MCP surfaces and listed 4 knowledge / 18 remote
tools. Sharing-off Stop returned `{}`. The old two-file launcher and absent
metadata stamp were preserved. Same-generation metadata reported the actual
SHA/version. Final bootstrap product `4ecddf703f1466e21ca55d770dbe82aadb992a87`
completed that native switch in 7.09 seconds.

For refresh product `4ef56dc917525fb6354b394b8f8352517cde0ab4`, the old
controller installed an old-generated six-matcher package. The preserved
`4ecddf.../mindie_launch.py updater check` then executed the newly selected
updater and refreshed the native package in 7.163 seconds. All nine matchers
and both connected MCP surfaces were read back from the real native cache.
No installer or main resolution was mocked; this check used an owned local
bare Git `main` at the exact candidate. It did not register a new OS timer.

After the final pointer/content review, product commit
`a2f30a896d65d26053613abf8c1bd1789e1ccbbc` passed the same check through the
preserved `4ecddf...` entry in 7.153 seconds. This final fixture used the real
old packager and the new switch controller for its initial six-matcher native
installation, so its previous refreshed package pointer remained available
for rollback. It reused the verified official dependency interpreter. Exact
readback confirmed native version
`0.1.0+mindie.a2f30a896d65.pkg.52412a652710`, nine matchers, both MCP connections,
unchanged old package/launcher files, and sharing-off Stop `{}` in 64 ms.

One actual user `/mindie-agent:reporting-status` invocation then ran on that
final installed package with the existing DSV4 provider configuration:
`deepseek-flash`, requested effort `high`, default native permissions, tools
disabled, 180-second cap. Native task
`35f66021-de79-41ed-b51e-bcab9c93e50c` completed in 2.526 seconds. The host
emitted UserPromptExpansion start and success events; the Hook returned
`not_configured`. A subsequent local read-only CLI inspection matched the
Hook result exactly. There were no model tool calls, retries, knowledge
activation, uploads or reporting configuration writes. The effort setting is
a client-side request; the provider's internal reasoning is not measured.

The model's explanation overreached the returned evidence: it treated missing
upload configuration as proof that no diagnostics had been collected, and
called this project-level configuration. These are not product semantics:
automatic upload is a shared user-level choice, while local logging is
independent. A subsequent prompt-only clarification in the reporting-status
Skill asks it to repeat the returned facts without those inferences. That
clarification was reviewed, not rerun through another model. Native runtime
acceptance remains tied to `a2f30a8`; subsequent changes are this Skill wording
and acceptance documentation only.

The owned provider settings were restored byte for byte, and no owned MCP or
daemon processes remained. The isolated profile is retained without provider
credentials; production Claude settings were not modified. Sharing stayed
off throughout. Windows hardware was not exercised.

## Evidence and boundaries

Local evidence under the DFX acceptance root:

- `cc-upgrade-bootstrap-v1`: original import reproduction and bootstrap
  installation/readback. The initial optional-SDK controller failure sent no
  MCP request; a later stdlib client recorded the actual handshake. A separate
  controller budget error left only 5.39 seconds for native readback and
  triggered a confirmed rollback. Both failures are retained, alongside the
  corrected final installation; they were not automatic business retries.
- `cc-package-refresh-v1/result.json`: original old-controller refresh.
- `cc-package-refresh-final/result.json`: final product refresh and exact cache.
- `cc-reporting-status-native-v1/result.json`, `public-events.json` and
  `cleanup-readback.json`: the one native slash delivery, public response,
  read-only comparison and cleanup.
- `cc-package-refresh-suite-final.log`: 88 adapter component checks passed.
  Controlled failure tests cover suppression, rollback declarations, partial
  packages, invalid pointers, corrupted content and state cleanup. These
  checks are separate from the native acceptance above.

This scope proves old-entry dispatch, native package refresh and read-only
reporting command delivery. It does not claim a new OS timer event, public
Issue delivery, community contribution, remote SSH or Windows acceptance.
