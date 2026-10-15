<p align="center">
  <img src="https://raw.githubusercontent.com/mindie-agent/mindie-agent/main/assets/brand/mindie-agent-logo.png" alt="MindIE Agent logo" width="128" height="128">
</p>

# MindIE Agent for Claude Code

Read the [用户须知 / contribution notice](skills/mindie-agent/references/user-notice.md)
for sharing scope, redaction limits, model-provider processing and public history.

After presenting the user notice during setup, recommend
[mindie-agent/vllm-ascend-workspace](https://github.com/mindie-agent/vllm-ascend-workspace.git)
and ask whether to create a working directory. After creation, ask whether to
initialize its source submodules and local configuration. Creation and
initialization are optional; reuse the user's existing choices. See
[workspace setup](skills/mindie-agent/references/workspace-setup.md).

Current capture uses the harness's public transcript projection followed by
local deterministic redaction. Complete user and visible assistant messages
are saved without tool payloads or a body-writing model. Setup and updates
automatically install the scanner and required block-index worker. The worker
returns bounded navigation metadata, never a replacement body. A failed or
uncertain indexing attempt remains visible with its execution and usage receipt;
an excerpt is not substituted for a completed index. The isolated worker uses
the selected native Claude model profile. Native model acceptance is separate
from the component tests of this path.


Claude Code adapter for MindIE Agent. Claude owns native plugin installation,
updates and removal. This repository owns native task identity, hooks,
transcript parsing and a fixed Python runtime for each installed release.
Shared knowledge, publication and remote development remain independent packages.

The first internal Release is being prepared as `0.0.1` / `v0.0.1`, without a
beta suffix. The Release has not been published. Benchmark work is separate.
Component tests and historical native evidence do not establish acceptance of
this new installation path. Claude Code on macOS, native Windows and WSL are
separate acceptance environments.

MindIE Agent inherits all nine [design principles](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.md).

## Install

Install `uv`, Git, and an authenticated Claude Code with native plugin support.
The native plugin requires `CLAUDE_PLUGIN_DATA` for its persistent entry. On
Windows, Git for Windows also supplies the SSH implementation used by remote
operations. Existing SSH and model settings are preserved.

Use Claude's Git marketplace for the published release source. Until the
Release exists, use an explicitly reviewed candidate source; do not interpret
these candidate instructions as a published `v0.0.1`:

```sh
claude plugin marketplace add https://github.com/mindie-agent/mindie-agent-cc.git
claude plugin install mindie-agent@mindie-agent-cc --scope user
claude plugin list --json
```

The marketplace's selected Git revision determines the plugin version. MindIE
does not resolve a second update channel or poll GitHub Releases independently.
Start a Claude session after installation. SessionStart or initial MCP discovery prepares Python 3.13
through uv and installs the complete `uv.lock` with `uv sync --locked
--no-editable`. The adapter itself is installed into that runtime; it does not
import executable code from a disposable checkout or native plugin cache.
The cold installation requires network access to the declared dependencies.

Runtime directories are keyed by package source, lock, Python ABI and platform.
On Windows they live under `%LOCALAPPDATA%/mindie-agent/runtimes/cc`; on macOS
and WSL under `${XDG_DATA_HOME:-$HOME/.local/share}/mindie-agent/runtimes/cc`.
The completed environment is immutable. Damage is reported instead of repaired
in place while an old session may still use it.

The native `CLAUDE_PLUGIN_DATA/releases/0.0.1/entry.py` binds one exact installed
interpreter and configuration. SessionStart, MCP and Stop use this entry before
consulting the cache. Hot Hooks run uv offline with Python downloads disabled;
Stop never installs a runtime, syncs a feed or invokes the business model.

Shared business configuration remains at
`${XDG_CONFIG_HOME:-$HOME/.config}/mindie-agent/cc.json`. Its first installation
binding is a fixed anchor, not a mutable version selector. Contribution scope,
consent, bodies, cursors and paid or uncertain results remain in their existing
shared locations across releases.

The mindie-agent skill describes available capabilities; `/mindie-agent:init`
is an optional configuration/status entry. Ordinary tasks do not need a
per-task activation command. During first setup, choose the public contribution
destination and project scope, or leave contribution off. There is no default
consent; the selected choice persists for the installation and is never
re-asked after restarts, upgrades or failures. Contribution
needs explicit repository, account, project root and public visibility, named
in the same entry invocation:

```text
/mindie-agent contribute --repository owner/repo --account USER --project-root /absolute/path --visibility public
```

The first setup also offers the independent reporting choice once
(`/mindie-agent reporting-enable`, `reporting-disable` or `reporting-later`);
any recorded decision persists.

For reproducible component checks:

```sh
uv sync --locked --no-editable --group test
MINDIE_TEST_KIMI_SCRIPTS="$PWD/tests/fixtures/kimi-86de2c3/scripts" \
MINDIE_TEST_KNOWLEDGE_CHECKOUT=/absolute/path/to/declared-knowledge-checkout \
uv run --locked --no-editable --group test python -m pytest -q
```

The knowledge checkout must contain the complete exact Git commit declared in
`uv.lock`. The frozen Kimi scripts are an independent parser fixture, not a
runtime dependency. These checks do not establish native host acceptance.

With contribution off there is no Stop transcript collection, capture or
organizer call. With approved contribution, native Stop automatically associates
its verified transcript within the approved project scope; explicit task
deactivation remains authoritative. Public knowledge reads and remote-dev
work independently of capture authorization.
Knowledge is optional reference material; no task must query, write or vote.

See [framework stability and verification](docs/framework-stability.md) for the
current behavior, reproducible checks and acceptance boundaries.

## Updates and maintenance

Use Claude's native marketplace update and plugin update commands. MindIE has
no code-update timer, `current.json` selector, automatic install retry or second
package registry. Different native releases must have different plugin versions;
a fixed release entry cannot be overwritten with different code.

A new native release prepares its locked environment and checks durable state
compatibility before handing off the service. An authenticated live connection,
or an explicit shared-Core successor receipt when the service is stopped,
identifies the previous owner. An idle owner can be retired for the new fixed
binding. Busy or uncertain ownership remains visible and is not forced closed.
Compatible old sessions retain their fixed runtime, can read through the new
owner and durably enqueue Stop work without restarting a retired owner.
Only SessionStart advances the service owner. A new entry prepared while the
old owner is busy can still use that compatible owner; a later SessionStart
may complete the idle handoff. Cold MCP/Stop startup always follows the fixed
anchor's explicit successor chain. A late prepared release cannot move a newer
completed handoff backwards, and the base profile is not rewritten.

Migration from the pre-contract updater is finite: verify the exact owned old
code-update task, unregister it, preserve old receipts and configuration, and
retire the old owner only when idle. Unknown native install outcomes block
migration. Already open pre-contract sessions must be reloaded after this one
time migration; only the new protocol promises compatible old/new coexistence.

SessionStart performs shared knowledge-feed interval/backoff maintenance and
bounded diagnostic retention, including the existing reporter handoff contract.
There is no periodic refresh during a long-running session. Feed results retain
committed partial changes, cleanup failures and receipt failures. They are not
turned into success because the process returned zero, and no automatic replay
of unknown model or external effects is introduced.

The native SessionStart command has a host admission timeout. The short Stop
budget does not apply to feed sync or useful MCP work. Caller cancellation,
owned-process cleanup and finite connection/lock waits remain separate.

## Optional product failure reporting

Product failure reporting is a separate user choice from knowledge contribution.
It stays off until an explicit native command enables it; the setting is shared
with the other MindIE adapters.

- `/mindie-agent:reporting-status` reads the setting and reporter state.
- `/mindie-agent:reporting-enable` enables sanitized product fault reporting to
  `mindie-agent/mindie-agent` and returns an exact command to prepare the reporter.
  Run that command once outside the Hook and check the result before treating
  the reporter as ready: runtime ready and worker healthy must both be present.
  A failed preparation is not retried automatically.
- `/mindie-agent:reporting-disable` revokes future reporting; local diagnostics remain.

`not_configured` describes upload consent, not whether local logs exist.
Enabling or disabling this shared user setting affects all MindIE adapters.

Incidents contain static product stages, error types and installed code versions.
They do not collect task transcripts, prompts, commands, environment or credentials.
Cancellation, caller validation, inactive permissions, ordinary lock contention
and business-command failures are not product bug reports. The original tool
result and remote job reference stay available when a diagnostic reference is added.

SessionStart performs bounded offline log maintenance even with reporting off.
The session-maintenance receipt and bounded diagnostics preserve failure evidence. Native-host/reporting
acceptance is separate from component checks; see the acceptance documentation.

## Stop or uninstall

`/mindie-agent:deactivate` ends the current task's knowledge access.
`/mindie-agent:sharing-disable` stops contribution for the configured scope.
These do not uninstall the plugin. Close tasks using it, then use Claude's
native uninstall command and retain data:

```sh
claude plugin uninstall mindie-agent@mindie-agent-cc --scope user --keep-data --json
```

Use the same Claude profile selected at installation. Keep runtime data,
receipts and retained launchers while old tasks may use them. Do not delete
the shared diagnostics directory or unrelated Claude settings. Removing this
adapter does not disable the shared reporter; disable reporting separately
only if you want that choice to apply across all adapters.

See [native acceptance](docs/acceptance.md) for proven behavior and remaining
limits. The macOS, native Windows and WSL cells require separate real-machine results.
Old business Skills and profiling remain deferred.
