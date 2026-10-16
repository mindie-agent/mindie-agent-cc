# Failure diagnosis and local cancellation

Native status reports existing knowledge configuration, service/storage health,
this task's binding and diagnostic failure counter, and the latest five captures
and contribution batches associated with the bound task. An absent identity
returns no task records. A transient local or network failure is recovered by the existing worker; status is how that problem is seen. Failed batch IDs can be inspected with the optional recover command;
unknown publication results must be reconciled before any explicit retry.

Status does not initialize databases, start models/services, bind a task,
reset failure counters, or replay consumed input. SQLite read-only access may create normal
WAL reader sidecars; it does not change business records. Failure projections omit
raw provider stderr, transcript text, tokens and other task identities. Existing
malformed adapter settings produce a diagnostic instead of first-use choices.
Native shell/SSH and independent remote-dev remain available when knowledge is down.

The stdio front keeps input responsive while bounded tool requests run. Cancel and
EOF clean up only owned local processes; a cancelled observation does not establish
remote job termination. Responses preserve the original job reference and delivery
certainty. Explicitly inspect or stop that job before a new submission. Remote poll
waiting is capped at 30 seconds without changing the remote workload timeout.

Actual macOS evidence includes task-isolated SQLite diagnostics, paused state with
unchanged counters, corrupt config, child/grandchild cancellation, ping while busy,
EOF, output backpressure, update-lock cancellation and safe remote exception
classification. Development checks protect those contracts. Controlled native
identity/helper seams are not evidence that the host UI emitted a cancel event,
that a remote job stopped, or that a full contribution loop completed.

Windows remains unverified on hardware. Its blocking pipe-write and process-tree
cleanup behavior requires separate acceptance. No new retry or background service
is introduced by these changes.

## Automatic product-fault reports

The adapter inherits the existing shared diagnostics reporter. Confirmed product
faults produce bounded local diagnostic records; after the independent saved
reporting choice and one-time reporter setup, the worker automatically prepares
redacted GitHub Issues without a model call or a per-task prompt. Ordinary
business-command failures are not automatically asserted to be product defects.

The public payload contains only allowed product code metadata. It excludes
task text, raw exceptions, credentials, commands, paths and local incident IDs.
Duplicate faults share a fingerprint; an unknown Issue creation result is
reconciled before any further action and never blindly posted again.
Logging/reporting failure cannot replace the original business result.

Failed native summaries retain a bounded `mindie-cc-native-failure/1` projection
in the existing private outcome receipt's `raw_result` field. It contains only
known native subtype/terminal enums, validated turn and HTTP-status numbers,
and the shape/count of error or result fields. It never retains provider/model
free text, unknown labels, session IDs, prompts or credentials. Unknown native
values remain explicitly unknown. The original usage, billing state and failed
outcome remain authoritative; no additional model invocation is made.

Native turn-limit, cost-budget and structured-output exhaustion have separate
static shared failure reasons, so the caller's summary detail identifies the
failed boundary. The enum source was verified in the installed Claude Code
2.1.269 result schema and terminal constructors. This does not identify the
subtype of an older failure whose original envelope was already discarded.
Scratch deletion remains required after owned-process cleanup.

The failure-projection change was reviewed against all nine design principles
(design-principles blob `62cdd5ae40553df2f0f7f7d022141123792d2944`): it addresses
an observed inability to diagnose a paid failure without another invocation (1);
uses a finite projection in the existing receipt and caller failure contract
(2, 4); adds no settings or user workflow (3, 5, 7); does not promote summaries
to evidence or alter body authority (6); reuses the ledger, usage accounting,
scratch owner and valid prior native reads (8); and preserves specific failures,
known usage, secrecy and no-replay behavior (9). Synthetic tests cover all
three limits, untrusted free text/types and a terminal failure followed by a
nonzero process exit. The original paid failure is preserved, not retried.

Reporting enable saves the explicit preference before changing its policy.
Unreadable or damaged consent leaves the real policy unchanged. A policy and
saved choice that disagree are exposed as `consent_mismatch`, including the
actual `policy_enabled` fact. Existing choices are not asked again in later
tasks. The returned setup `command` is an argv array; `command_line` declares
`command_shell` and uses PowerShell quoting on Windows.

The synthetic adapter/log/outbox/mock-Issue checks and actual Windows process
identity checks are recorded in diagnostics'
`docs/production-reporting-acceptance-2026-10-15.md`. They do not demonstrate
native Claude event delivery, scheduled-task setup or a public Issue write.
