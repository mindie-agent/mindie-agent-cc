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

Version `0.0.1` / `v0.0.1` is the first internal release, distributed as an
ordinary GitHub Release without a beta suffix or prerelease flag. Benchmark
work is separate. See the [current acceptance checkpoint](docs/acceptance.md)
for the tested source identities and remaining scope limits. Claude Code on
macOS, native Windows and WSL are separate acceptance environments.

MindIE Agent inherits all nine [design principles](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.md).

## Install

Install `uv`, Git, and an authenticated Claude Code with native plugin support.
The native plugin requires `CLAUDE_PLUGIN_DATA` for its persistent entry. On
Windows, Git for Windows also supplies the SSH implementation used by remote
operations. Existing SSH and model settings are preserved.

Inside WSL, use Linux-native Claude, Git and uv, and keep the profile, SQLite
state and runtime on the Linux filesystem. On Linux and WSL, service-backed
knowledge reads and automatic capture/indexing require a reachable systemd
user manager, systemd 254 or newer, and a unified cgroup hierarchy. This also
applies to knowledge reads with contribution off; independent remote-dev
operations do not use that knowledge service. A missing manager, unsupported
version or unverified containment is a service error, with no unprotected
fallback. Check the user manager with
`systemctl --user show --property=Version`.

Use Claude's Git marketplace with the immutable `v0.0.1` tag. Confirm the
release is available in [GitHub Releases](https://github.com/mindie-agent/mindie-agent-cc/releases)
before installing:

Run these commands in a macOS/WSL shell or native Windows PowerShell, using the
same Claude profile throughout:

```sh
git --version
uv --version
claude --version
claude plugin marketplace add "https://github.com/mindie-agent/mindie-agent-cc.git#v0.0.1"
claude plugin install mindie-agent@mindie-agent-cc --scope user
claude --init-only
claude plugin list --json
claude mcp list
```

Release `0.0.1` declares exactly Claude Code `2.1.269` and `2.1.299`,
not every version between them. Check the actual CLI version and confirm that
the native installed record reports `mindie-agent@mindie-agent-cc`, version
`0.0.1`, enabled with user scope in the intended profile.

Each Release declares the Claude Code versions and native interfaces it supports.
The shared Release selector chooses the highest compatible official Release;
Claude's native Git marketplace and installer own its download and installation
records. This marketplace's unconditional native auto-update is disabled so it
cannot bypass that selection. Other marketplaces and native settings are preserved.
The native `--init-only` command completes installation through SessionStart and
exits without starting a conversation or calling a model. Check that both MindIE
MCP servers report `Connected` before treating installation as complete.
Claude starts MCP connections concurrently with SessionStart in an ordinary
session; its connection window may expire before a cold dependency build finishes.
The explicit native initialization stage prepares Python 3.13 through uv and
installs the complete `uv.lock` with `uv sync --locked
--no-editable`. The adapter itself is installed into that runtime; it does not
import executable code from a disposable checkout or native plugin cache.
The cold installation requires network access to the declared dependencies.

Runtime directories are keyed by package source, lock, Python ABI and platform.
On Windows they live under `%LOCALAPPDATA%/mindie-agent/runtimes/cc`; on macOS
and WSL under `${XDG_DATA_HOME:-$HOME/.local/share}/mindie-agent/runtimes/cc`.
The completed environment is immutable. Damage is reported instead of repaired
in place while an old session may still use it.

The native `CLAUDE_PLUGIN_DATA/releases/0.0.1/entry.py` binds one exact installed
facade and profile. Each operation reads the selected complete helper, interpreter
and engine from that profile. SessionStart, MCP and Stop use this entry before
consulting the cache. Hot Hooks run uv offline with Python downloads disabled;
Stop never installs a runtime, syncs a feed or invokes the business model.

Shared business configuration remains at
`${XDG_CONFIG_HOME:-$HOME/.config}/mindie-agent/cc.json`. It records the selected installation binding. Contribution scope and consent
remain separate user data; changing code or reloading the native host never
authorizes resetting or asking again for those choices. Bodies, cursors, usage
and uncertain results remain in their existing locations.

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

After more than 24 hours since a successful Release check, the next natural MCP
use starts one asynchronous check. Ordinary tool calls use an in-process time
comparison and do not wait for network, scan releases or hash the installation.
A failed check preserves the last successful time and has a short retry backoff.
No daemon, code-update timer or second package registry is installed. Stop never
starts an update.

The update prepares the selected native package and its complete immutable uv
runtime outside operation admission. An already prepared candidate retries only
the short activation control, without refetching Releases or reinstalling.
In-flight calls hold shared admission; cancellation remains concurrent. Activation
takes exclusive admission only when it is free, defers while the Core has work,
retires the exact idle owner and observes the new owner ready before atomically
publishing the canonical binding. The 30-second observation window is a control
bound, not a deadline that kills useful work. Unknown installation effects and
incomplete cleanup remain visible in local receipts and are not blindly replayed.

Stop does not wait for this service-switch gate. It takes one complete helper
snapshot and submits the authorized event to the existing durable capture
queue. A retired helper cannot revive its old service; the new owner consumes
the queued event. Explicit off and task revocation still prevent capture.

An existing native facade reads the new complete binding on its next operation.
Updates require no approval, notification, reload command or renewed contribution
authorization. Explicit plugin disable, contribution/reporting choices, project
scope and durable outcomes remain authoritative. Old unsupported development
profiles are rejected without automatic migration. Real storage formats and
the facade/helper protocol determine compatibility; product version sorting
only selects a Release.

See [Harness interface evidence](docs/harness-interface-evidence.md) for the
registered boundaries and the distinction between component tests, zero-model
native experiments and completed business acceptance.

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
