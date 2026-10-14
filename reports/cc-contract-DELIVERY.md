# Claude Code contract delivery

Date: 2026-10-15 (Asia/Shanghai)

Base: `12f77a7923cad53c99b6b1c5d87d7ca705825755`

This delivery preserves K3's uncommitted port and completes the observed
integration failures. It is a local commit for integration, not a published
release or evidence that native product acceptance has passed.

## Inherited K3 work

- Move the summary worker to the current block-index request, policy identity
  and `mindie-summary-outcome/1` contracts. A single isolated native Claude
  invocation reads model/provider settings from the selected native profile.
- Return outcome, native call count, usage and cleanup receipts without
  replaying an uncertain or billed request. Remove an arbitrary total model
  deadline while retaining cancellation, owner checks and output limits.
- Associate an authorized, in-scope native Stop automatically from its own
  transcript artifact; preserve explicit task deactivation.
- Remove the per-task capture lease prerequisite from public knowledge reads;
  update immutable task/block reads, runtime pins and CI fixtures.
- Add the core-store preparation helper for installation integration.

Reviewed runtime pins:

- knowledge: `add5c56313b5257410d52dc736544a0b8e30d55f`
- remote-dev: `9c8ae5ef70687ce55d77e85b2dbdd79a0844f013`

The seven-file early review snapshot had diff SHA-256
`c7bd00e16ecff16582ac7ad0e461c50211740066da68a663f8d39b300467efc3`.
This is provenance for the inherited port, not the final delivery identity.

## Integration repairs

- Windows reader threads use `read1` so a small complete terminal envelope
  reaches the owner while the native child is still alive. Inspect final
  buffered output after thread completion, preserving a fast error envelope
  followed by exit 1.
- A reported failed native turn retains valid usage counters and reports
  known billing. Cleanup failures retain completed metadata and usage.
- Successful POSIX process-group cleanup is recorded and not signalled
  twice, matching Codex `a5de698`; failed termination is not marked successful.
- Cleanup stages remain visible. Callers with receipts retain stdout and
  cleanup details; callers without receipts receive `CommandCleanupError`
  with completed stdout and an explicit instruction not to repeat the
  operation. Primary execution errors keep precedence over cleanup errors.
- Check Windows Job termination and close results. Do not synchronously close
  a pipe still held by a blocked reader after cleanup failure.
- A damaged explicit native profile selection does not silently switch the
  Stop attribution reader to another profile.
- Replace stale manual-activation and offset-pagination tests with the
  current automatic Stop and immutable block contracts. Verify duplicate
  handling, deactivation, transcript scope and bounded Unicode wire payloads.
- Update Skill and native-boundary wording so entry is configuration/status,
  not a mandatory per-task activation or knowledge-read step.

## Validation

All tests used the existing `test-venv`, isolated disabled diagnostics
configuration and synthetic native executables. No real model/provider
requests were made.

- Focused bounded-process suite: 16 tests passed.
- Full suite: 192 tests passed in 28.780 seconds.
- `git diff --check`: passed.
- Two existing subprocess `ResourceWarning` messages appeared during the
  entry contribution test; both referenced PIDs were absent after completion.

Full-suite evidence:
`../dispatch/cc-contract-delivery-tests.log`

The integration fixture uses a disposable full knowledge clone at the exact
pin, recorded in `../dispatch/cc-contract-knowledge-fixture.json`. This keeps
the full-history test's precondition intact rather than weakening it for the
shallow release checkout.

## Design principles review

Reviewed all nine principles against the final diff and affected call paths.
Current design checkout revision:
`8bbbf2a0e54903b7640f2769a322bacf99138801`;
`docs/design-principles.md` blob:
`62cdd5ae40553df2f0f7f7d022141123792d2944`.

1. Total cost: remove repetitive task activation, stuck Windows waits and
   uncertainty-driven repeat calls. No new recurring work or model request.
2. Closed problems: the adapter only binds native identity, invokes one
   admitted index request and returns a validated outcome.
3. Understanding burden: users do not manage internal leases or worker state.
   Failure receipts describe the completed operation and cleanup stage.
4. Boundaries: the shared core continues to own admission, storage, queues
   and indexing policy; the adapter does not introduce a parallel store.
5. Informational Skill: describe capabilities and configuration; remove
   mandatory per-task invocation language.
6. Reference knowledge: no certification or required reading/writing is
   introduced. Bodies remain immutable; model output is bounded metadata.
7. On demand: only configured contribution plus a verified native task and
   project scope authorizes Stop capture. Deactivation remains authoritative.
8. Reuse: preserve K3's port, use the pinned core and existing venv, and reuse
   Codex's successful-group cleanup contract. Synthetic checks target actual
   observed failures.
9. Visible errors: preserve known failed-turn usage, output and primary
   errors; expose cleanup and invalid-profile failures; do not silently
   replay uncertain model execution. Fault-injection tests cover these paths.

Native platform behavior remains outside the completed validation boundary.

## Integration and remaining acceptance

- Integrate the root-owned Grok setup/updater/state work and reporting work.
  This tree deliberately does not edit their reserved reporting paths.
- `capture_config.prepare_store` exists here, but this tree's setup/updater
  do not call it yet. Confirm the integrated installation path prepares and
  validates the selected core store before declaring the installation ready.
- Root owns the `mcp_server.remote_call` store-layout preparation wrapper.
- Root must run the integrated suite and final native Claude acceptance on
  both macOS and Lenovo Windows, including the actual configured backend,
  automatic Stop, worker outcome and consumer retrieval receipts.
- Synthetic calls to the Windows reader strategy on macOS prove the pipe
  handling regression; they do not validate Windows Job APIs or native
  Claude installation. CI, component checks and native acceptance are
  separate evidence.
- No push, merge, tag or Release publication was performed. Benchmark work
  remains separate from first-release preparation.
