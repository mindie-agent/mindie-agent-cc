# Framework stability — 2026-09-28

Claude Code native adapter; reviewed production revision `3e36a28`.

## Everyday use

Invoke the native MindIE entry and continue the existing task: `/mindie-agent`
in Kimi or Claude Code, and `$mindie-agent` in Codex. The first use after
installation asks for a choice once. That choice persists across tasks,
forks, restarts, updates and ordinary failures. Later entries require no
renewal or manual activation/recovery steps. An explicit choice change is
still available through the entry. Automatic diagnostic reporting has its
own independent saved choice and does not follow contribution consent.

With contribution off, the framework performs no Stop collection or organizer
call. Retrieval and remote development remain available. Temporary service
or network failures use the existing worker's persisted recovery state;
corrupt configuration is reported as a fault and never triggers onboarding.

## Implementation and verification

The bootstrap writer preserves damaged, foreign and unreadable existing settings. Normal setup generates the host package with the actual pinned interpreter.

The final integrated suite passed all 152 tests, including bootstrap preservation and shared choice checks. All 24 bundled Kimi fixture files matched their recorded source revision. These are local component results for the reviewed revision. The PR
checks are the source of online CI status; local counts are not CI claims.

From the repository root, use a Python interpreter satisfying the README's
SQLite requirement. Install `pip install -e '.[test]'` for core, or
`pip install -r runtime-requirements.txt` for an adapter, then run:

```sh
MINDIE_TEST_KIMI_SCRIPTS="$PWD/tests/fixtures/kimi-86de2c3/scripts" python -m unittest discover -s tests
```

The local-candidate identity test creates a disposable Git checkout and venv
from the published pins. It requires access to those repositories and package
dependencies, and leaves the active interpreter and its metadata unchanged.

Tests use committed real parser/peer fixtures or exact Git revisions declared
in the workflow. They do not discover a user's production installation.
Missing dependencies fail explicitly. Keep failure-path, concurrency and
recovery cases bounded and deterministic; model sessions are reserved for
checks that actually need a native host.

## Integration and acceptance boundaries

Publish the exact dependency commits before adapter CI. Process the reviewed
PRs in order: knowledge core, Claude Code, Kimi, Codex. Preserve published
commit identities used by runtime and test pins.

Isolated native read-only entry and persistent choice were exercised across
the three hosts. Claude's normal generated package and pinned runtime were
verified. Evidence for unchanged entry paths can be reused; it does not
prove native contribution Stop, publication, or Windows host acceptance.
Those remain separate integration checks. Historical dated reports retain
their original scope. Domain asset import and new business workflows remain
deferred while the framework stabilizes.
