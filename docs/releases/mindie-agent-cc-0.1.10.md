# mindie-agent-cc 0.1.10

This release makes knowledge retrieval depend on missing task information and
moves standard component command bindings into the managed runtime during idle
activation.

## Task routing

A stack trace or local code that already identifies a file, function and
invariant can lead directly to code inspection, a focused fix and verification.
Knowledge is consulted when a domain information gap remains; missing runtime
facts are probed first. Exact knowledge refs can be read directly, retrieval
stops when enough information is available, and new evidence can reopen it.

Batch `refs` with `observed_conditions` are the preferred explain interface.
Single-ref compatibility requests obey the same complete dependency-closure
and final-response budget; body fields exist only when loaded. The Skill and
actual MCP descriptions explain unresolved and already-known references without
requiring extra classification, fixed retrieval rounds or skip feedback.
Claude's native activation, tool identity, verification and consent boundaries
are preserved.

## Component updates

Standard Python module bindings contribute the installed component version as
an update floor. The shared selector chooses official immutable sources and
the existing candidate preparation and idle activation rebind the commands.
The source command snapshot participates in preparation identity and is checked
before activation, so concurrent user edits defer the switch. Custom wrappers
are retained; an installation with only custom wrappers does not gain an
unnecessary component catalog check during a plugin upgrade.

The native Hook and MCP definitions now select the persistent
`CLAUDE_PLUGIN_DATA/releases/0.1.10/entry.py` entry. Knowledge, remote and
components remain separate native MCP servers with their existing permissions.

## Runtime and verification

The locked runtime selects knowledge 0.13.6
(`9af5a82ad557e2120277693854a0571613c8a115`), remote-dev 0.11.1 and diagnostics
0.5.4. `pyproject.toml` and `uv.lock` record the exact source revisions. Optional
test component pins remain profiler 0.5.1, sim 0.7.1 and coordinator 0.7.0.

Declared Claude Code support remains 2.1.269 and 2.1.299. CI covers installed
CLI execution, candidate preparation, command-edit conflicts, isolated startup,
native manifests and MCP end-to-end contracts on Linux, macOS and Windows.
The tag workflow verifies formal dependency releases and publishes the exact
wheel and source archive, compatibility metadata, checksums and
public-transcript microbenchmark reports.

These contract and CPU microbenchmark results do not establish an end-to-end
task speedup. Existing task measurements include environment noise and a
complex task gate that did not pass. A published release, a native plugin
installation and an activated runtime are verified as separate facts.
