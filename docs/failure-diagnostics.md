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
