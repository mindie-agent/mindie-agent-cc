# Claude Code 0.0.1 ordinary Release lane

Worktree: `/Users/maoxx241/code/mindie-release-20261014/cc-grok`
Branch: `codex/cc-release-grok-20261014`
Base: `12f77a7923cad53c99b6b1c5d87d7ca705825755`
Principles reviewed: `6a81b410301a1791dcefa1f2b005b84bd853f720`
Release decisions: `design/docs/releases/0.0.1.md`

This lane adds the ordinary Release source, candidate/product checks, and
durable-state compatibility. It does not publish `v0.0.1`, push, or claim
native host acceptance. Benchmarks stay separate.

## Revisions

- Product version `0.0.1`, tag form `v0.0.1`, no beta suffix and no
  prerelease channel.
- Default update channel is `release`. `main` remains available only when
  setup or the adapter selects it, or when `MINDIE_CC_UPDATE_CHANNEL=main`.
- Intended current component pins, owned by K3 and not edited here:
  knowledge `add5c56313b5257410d52dc736544a0b8e30d55f`, remote-dev
  `9c8ae5ef70687ce55d77e85b2dbdd79a0844f013`. Remote PR 26 head `d7b3f4f`
  is recorded as identical production code; this lane did not fetch it.
- Public contract SHA256 declared in `product-contract.json`:
  `5bdb5d9e6846c21fb3f980132ea5c70a4c67018867b4b0acd371769451f85414`.
  The publication commit in that file is the Codex declaration
  `43b5c566c63b083671d01db6d91f8068d10084e0`. This lane did not re-fetch it.

`runtime-requirements.txt` still names the previous pins
`82f7d7dd4c52637e1448e3747262a20453f4bcb4` and
`4d35c026084d780e1af99073e4d89e4c63bcb5be`. K3 owns that alignment.

## What changed

- `updater.py` reads GitHub `releases/latest` for
  `mindie-agent/mindie-agent-cc`, rejects a draft, prerelease, or
  non-normal tag, and resolves `refs/tags/<tag>` with `git ls-remote`.
  An annotated tag uses the peeled commit. The fetched
  `.claude-plugin/plugin.json` version must match the Release version.
  A resolve failure stays `check-failed` and does not stage or fall back
  to `main`.
- `product-contract.json`, `scripts/product_contract.py`, and
  `scripts/candidate_validate.py` reuse the Codex candidate receipt:
  source bytes, requirements digest, runtime pins, and publication
  identity. The candidate validator runs in the candidate interpreter.
- `scripts/runtime_probe.py` checks the current block-index, result,
  usage, and `--identity` worker contract. Import success is not enough.
  SQLite FTS5 `contentless_delete` is no longer required.
- `scripts/state_compatibility.py` and `scripts/receipt_layout.py` read
  knowledge layout and remote execution receipts before a switch. A
  missing, unknown, or released foreign format fails. The check does not
  delete, reset, or rewrite bodies, cursors, paid results, or receipts.
- `setup.py` defaults `--channel release`, records `update_channel`, and
  uses the new probe. README states macOS and PowerShell install steps
  and says `v0.0.1` is not yet a published tag.

## State compatibility

Same released format `1` at release `0.0.1` is compatible. A development
layout with `release_version: null` and the same format is compatible.
A released format this runtime does not implement is rejected with the
core message that an explicit migration is required. Invalid layout JSON
is also rejected. In both rejection fixtures the database body and receipt
bytes were unchanged, and no replacement directory was created.

Remote receipts use the existing adapter path `paths.state_dir(adapter) / "remote"`,
with native session children unchanged. The layout is declared before external
work so uncertain submissions remain recognizable. Foreign formats, including
unreleased ones, are refused without relabelling or deleting their contents.
State is checked again inside the exclusive switch lock, before service
retirement, native installation or a switch receipt. Cached candidates therefore
cannot bypass a later state incompatibility.

## Process runner interface for the root integration

`scripts/bounded.py` is outside this lane's ownership. The small edit
already present is the interface the new candidate and publication checks
need. Do not treat that diff as a second protocol implementation. K3's
current `bounded.py` should be kept, with these behaviors preserved:

- `run(argv, stdin, *, timeout, ...)` accepts `timeout=None` or a positive
  number. `None` means no total lifecycle deadline. Cancellation and
  process ownership still end the process. A non-positive number remains
  an error.
- A failed checked command raises with `returncode` set to the child
  status and `mindie_output` set to the captured stdout decoded as UTF-8
  with replacement. `product_contract.probe` uses those attributes to
  keep the candidate's protocol failure (`stage`, `code`, optional
  `component`) instead of a generic process failure.
- A successful call still returns stdout text. `product_contract` also
  accepts a result object with `stdout: str` and `returncode: int` for a
  Codex-style runner.
- POSIX and Windows tree ownership stay in K3's runner. Codex `a5de698`
  records completion on the `Popen` object after a successful group kill,
  or when the group is already absent, so a later cleanup does not signal
  a reaped group. Apply that receipt in the Claude Code runner during
  integration. This lane did not edit the kill path further.

## Commands and results

The initial implementation passed its recorded focused checks. Review found
private interpreter paths and the wrong remote-state authority in that version;
the following results replace those as evidence for the final lane changes.

Run from `tests/` with the current pinned runtime interpreter:

```sh
../../test-venv/bin/python -m unittest test_state_compatibility test_fts_probe \
  test_release_source test_candidate_contract test_updater \
  test_package_refresh test_transport_retry -v
```

Result: 50 passed. Tests use `sys.executable`, temporary synthetic stores and a
synthetic old-contract import fixture; none silently skip for a maintainer's
private path. The actual `switch` entry is tested to reject incompatible state
before retirement or installation. A simulated uncertain remote submission
preserves its layout and job. Candidate validation checks installed pin identity
before importing runtime-owned state, matching the reviewed Codex fix.

K3 and Grok follow-up model sessions ended with HTTP 408. Their edits and usage
are preserved; root completed the narrow review fixes without repeating model
work. This lane has not been installed in a native Claude host.

## Integration needs for K3 and the root agent

- Replace `runtime-requirements.txt` with knowledge `add5c563…` and
  remote-dev `9c8ae5e…`. Until then, candidate pin validation fails closed
  against the file's old commits.
- Keep K3's `bounded.py` protocol and add the interface above, including
  the Codex `a5de698` group-kill receipt.
- Wrap the existing remote call in `receipt_layout.prepare_layout` before creating the session directory or invoking remote-dev. No new engine setting or moved receipt path is needed.
- Current `transcript.py` has no `history_source`. The probe therefore
  requires `FileIdentity`, `identify`, and `read_material` only. If K3
  adds historical import, the probe should require `history_source` too.
- `organizer.py`, `capture_config.py`, hooks, identity, transcript,
  service lifecycle, and their tests were not modified.
- Native install readback, rollback, and service restore in `updater.py`
  were left in place. This lane did not execute them against Claude Code.

## Native verification gaps

- No macOS or Windows Claude Code installation, plugin list readback, or
  model call was run. The root agent owns Lenovo and macOS acceptance.
- No live GitHub Release exists for `v0.0.1`. Resolver tests use synthetic
  HTTP and one local Git repository.
- Windows path and scheduled-task code remains source-level. These tests
  do not prove native Windows process or PowerShell behavior.
- Publication contract fetch is implemented and was not executed against
  the network.
- The Codex repeated-`killpg` fix is not applied in this worktree.

## Design principle review

Reviewed against `6a81b410301a1791dcefa1f2b005b84bd853f720` for this diff.

1. Total cost. A normal install follows one published Release instead of
   asking the user to track `main` or a beta channel. Checks run at update
   time, not on ordinary tasks.
2. Closed problem. Tag parsing, commit resolution, manifest equality, pin
   equality, and format equality are mechanical. The release does not
   claim a quality or cost improvement.
3. Understanding. The user sees `0.0.1` and `release` or explicit `main`.
   Receipt digests and layout files stay inside the installer.
4. One boundary. This adapter resolves and installs. Knowledge owns its
   layout. Remote receipts have one layout file. Requirements remain the
   pin authority, and this document does not replace them.
5. Skill. No skill gained a required workflow.
6. Knowledge. The publication contract is an identity check. It is not
   treated as proof that retrieved knowledge is correct.
7. On demand. Tests use temporary directories and synthetic records. No
   contribution, benchmark, or model call was started.
8. Reuse. The Codex release and candidate receipt protocol is reused.
   Existing updater rollback and package refresh stay in place. The old
   FTS probe was removed because the current core does not use it.
9. Visible errors. A bad tag, missing commit, mismatched manifest,
   incompatible layout, and validator failure are reported. They do not
   become "up to date", do not fall back to `main`, and do not rewrite
   durable state. A captured validator failure keeps its protocol code.
   Unverified native paths are listed above.
