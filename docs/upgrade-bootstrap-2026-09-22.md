# Upgrading from the pre-diagnostics Claude adapter

The real updater at `a9ae74a412367e67594991f5ba2c9c8fe47e86be` copies only
`mindie_launch.py` and `bounded.py` into a versioned launch directory. Applying
that packager to `4c4c0b928e36a280ae76400c9828b15f8fd70a97` reproduced
`ModuleNotFoundError: diagnostic_support` before a sharing-off Stop could run.
Its native package also registers only six slash matchers, omitting the three
new reporting commands. A fresh installation did not expose these failures.

## Current replacement

The 0.0.1 candidate now delegates code installation and updates to Claude's
native Git marketplace. `pyproject.toml` and the complete `uv.lock` install the
adapter and dependencies noneditably into an immutable runtime. Each native
release binds a persistent `CLAUDE_PLUGIN_DATA` entry to that exact runtime.
No production updater, package refresh controller, scheduler or mutable
`current.json` selection remains.

Unpublished development bindings are not migrated. Their configuration,
services, user choices, data and uncertain outcomes are preserved, and a
mismatch is reported explicitly. The current candidate only initializes an
empty profile or verifies the identical installation. Fixed entries and every
MCP request check the selected binding; there is no cross-version sorting,
successor chain or alternate owner selection.

SessionStart owns cold preparation and session-triggered feed/diagnostic
maintenance. Stop only performs bounded admission; it cannot download Python or
install a runtime. Useful sync and MCP execution do not inherit a timer cadence
as their execution deadline. Caller cancellation and ownership cleanup remain.

The evidence below describes the earlier implementation at its stated commits.
It is retained as history and is not acceptance of this replacement.

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
