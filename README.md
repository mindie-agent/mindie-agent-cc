# MindIE Agent for Claude Code

Claude Code adapter for MindIE Agent. This repository owns the native plugin entry, task identity, hooks, transcript parsing, installation and runtime switching. Shared knowledge and remote development stay in their own repositories.

Development is in progress. This is not a released or fully accepted implementation.

MindIE Agent inherits all nine [VAWS design principles](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.md). See the [Harness boundary and lifecycle contract](https://github.com/mindie-agent/mindie-agent/blob/main/docs/harness-boundary-and-lifecycle.md).

Acceptance requires actual local Claude Code runs and observed external outcomes. Passing unit tests or fabricated hook input does not establish native acceptance. macOS is the first real host; Windows desktop acceptance remains pending.

## Install on macOS

Requires Python 3.11+, Git and an installed, authenticated Claude Code with
native plugin support. The installer creates its own pinned persistent runtime,
retains the plugin source, installs and reads back the native package, and
registers model-free update checks. Community contribution stays off.

The knowledge interpreter also needs SQLite 3.43.0 or newer with FTS5 and
`contentless_delete` support. Installation checks the actual SQLite library;
the Python version alone does not establish this capability.

```bash
git clone https://github.com/mindie-agent/mindie-agent-cc.git
cd mindie-agent-cc
python3 scripts/setup.py
```

After setup succeeds, the downloaded source can be moved or removed. Keep the
runtime and launchers under `${XDG_DATA_HOME:-$HOME/.local/share}/mindie-agent/cc`.
Configuration is `${XDG_CONFIG_HOME:-$HOME/.config}/mindie-agent/cc.json`.

For isolated development only, setup accepts `--config`, `--root`,
`--claude-config-dir` and `--no-schedule`. `--knowledge-python` is an operator
override; an interpreter inside a disposable checkout is unsuitable for a
persistent install. `--no-native-install` writes MindIE files only.
The normal installer already performs these native installation operations:

```bash
claude plugin marketplace add /path/to/host-package
claude plugin install mindie-agent@mindie-agent-cc --json -y
claude plugin list --json
```

Explicit task entry is `/mindie-agent:init`. First use offers recommended
public contribution, read-only knowledge, or later configuration. There is no
default consent; the selected choice persists. Contribution needs explicit
repository, account, project root and public visibility:

```text
/mindie-agent:sharing-enable --repository owner/repo --account USER --project-root /absolute/path --visibility public
```

Component checks: `python3 -m unittest discover -s tests -v`. These are not host acceptance.

With contribution off there is no Stop transcript collection, capture or
organizer call. Remote-dev works independently of knowledge activation.
Knowledge is optional reference material; no task must query, write or vote.

## Updates and maintenance

The existing OS schedule checks remote main and the public knowledge feed
every five minutes without a model. Pinned dependencies, source, interpreter
and configuration switch together. An actual in-flight call defers switching;
an idle task grant does not. Failures remain visible without replaying work.

Select a retained launcher from the installed configuration, then use it from
any directory after the downloaded source is removed:

```sh
MINDIE_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/mindie-agent/cc.json"
MINDIE_LAUNCHER=$(python3 - "$MINDIE_CONFIG" <<'PYCODE'
import json, sys
from pathlib import Path
adapter = json.loads(Path(sys.argv[1]).read_text())
update = Path(adapter["state_dir"]) / "update"
current = json.loads((update / "current.json").read_text())
print(update / "launch" / (current["sha"] or "bootstrap") / "mindie_launch.py")
PYCODE
)
python3 "$MINDIE_LAUNCHER" --config "$MINDIE_CONFIG" updater status
```

Replace `status` with `check` to check main, or `uninstall-schedule` to stop
automatic checks. `install-schedule` restores scheduling. A known temporary
network or certificate-trust failure is retried by the next scheduled check
when its recorded retry time is due. TLS validation stays enabled.
`recover` is only for quarantined content or an unknown failure; status
gives the absolute launcher command for that case.

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

Updater checks perform bounded offline log maintenance even with reporting off.
launchd output goes to the null device; updater state and bounded diagnostics carry
failure evidence. No unbounded `scheduler.log` is created. Native-host/reporting
acceptance is separate from component checks; see the acceptance documentation.

## Stop or uninstall

`/mindie-agent:deactivate` ends the current task's knowledge access.
`/mindie-agent:sharing-disable` stops contribution for the configured scope.
These do not uninstall the plugin or stop automatic updates.

Close tasks using the plugin. Run the retained launcher's `updater
uninstall-schedule` first; cancellation failure preserves its plist and reports
failure. Then use the native uninstall command:

```sh
claude plugin uninstall mindie-agent@mindie-agent-cc --scope user --keep-data --json
```

Use the same Claude profile selected at installation. Keep runtime data,
receipts and retained launchers while old tasks may use them. Do not delete
the shared diagnostics directory or unrelated Claude settings. Removing this
adapter does not disable the shared reporter; disable reporting separately
only if you want that choice to apply across all adapters.

See [native acceptance](docs/acceptance.md) for proven behavior and remaining
limits. Windows real-machine acceptance follows on the user's dedicated machine
after merge. Old business Skills and profiling remain deferred.
