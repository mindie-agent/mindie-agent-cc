# Fault diagnostics — 2026-09-22

Own-tool failures now preserve the original result and attach a bounded local
incident. Trusted inner references pass through instead of creating duplicate
reports. Reporting is independently opt-in; status is read-only and install
leaves uploads off. The startup fallback is copied verbatim from the shared
package and never installs dependencies or uploads. Existing updater checks run
offline maintenance, preserve degraded results and do not replay business work.

A real Claude Code 2.1.269 task using the configured DSV4 (`deepseek-flash`,
high effort) called the candidate native MCP once. An owned helper protocol
fault produced one matching local incident. There was no SSH, retry, knowledge
state, reporting consent or upload. Temporary provider configuration and owned
processes were removed after the run.

The model retained the unconfirmed outcome, but described reporting-status too
broadly; the shared hint now explicitly states read-only and separate opt-in.
The first harness accidentally excluded plugin MCP via strict configuration;
that run performed no tool call and is not counted as successful acceptance.
This proves native tool delivery for candidate packaging, not Stop delivery or
production package selection.

A separate final package check used a fresh, non-editable virtual environment
installed from the canonical GitHub repositories at these exact published commits:

- knowledge: `87deb0717e9b03603b5ce46b490da14f3119c37d`
- remote-dev: `fb441aa18f0dcd5aff9b4a2f94a39dc4070c85e3`
- diagnostics: `4a7e50622492c92089c2318d80dbd2a24d41f145`

Each installed distribution's `direct_url.json` and imported module location
matched the expected Git commit and isolated environment. All 72 component tests
passed. The real Claude plugin installer then installed the candidate into a new
isolated profile; strict native readback confirmed enabled version
`0.1.0+mindie.fc5647262d3a`, the stable marketplace directory, and the exact
launcher/config bindings. A separate native `claude mcp list` confirmed both
knowledge and remote surfaces connected without relying on the parent shell's
adapter configuration. The version suffix identifies the tested source snapshot,
not a claimed released adapter commit.

That final package check used no model, scheduler, contribution or reporting
opt-in. Sharing remained off, no admission database or reporting policy was
created, and the isolated profile contains no copied provider credentials.
Production configuration/registry file hashes remained unchanged. The isolated
installation and bounded evidence are retained for review; owned processes
exited. Local evidence is `cc-final-canonical-v1/result.json` and `mcp-health.txt`.
This installation evidence does not repeat the model fault case or establish
native Stop, remote business execution or Windows hardware acceptance.

Shared actual log, transport, GitHub and macOS service evidence: [diagnostics acceptance](https://github.com/mindie-agent/diagnostics/blob/main/docs/dfx-acceptance-2026-09-22.md). Windows hardware remains unverified.
