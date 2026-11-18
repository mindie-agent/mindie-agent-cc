# Claude Code Harness Interface Evidence

<a href="harness-interface-evidence.zh.md">中文</a> | <b>English</b> · <a href="README.en.md">Documentation Index</a>

[`harness-compatibility.json`](../harness-compatibility.json) is the single machine-readable registry of Claude Code interfaces used by `mindie-agent-cc`. Release `0.0.2` declares support for two exact tested Claude Code versions: **`2.1.269`** and **`2.1.299`**.

`scripts/setup.py release-metadata` validates this registry in CI before component tests run: it checks that every declared production symbol, test identifier, and local evidence document exists, and binds the compatibility metadata to an immutable Git commit SHA.

---

## 1. Registered Interface Verification Matrix

All experiments below ran in isolated native Claude Code profiles. Control-plane checks sent no user prompts and made no external model calls.

| Registered Interface | Verified Native Behavior (`2.1.269` & `2.1.299`) | Automated Contract Tests |
|---|---|---|
| **Git Marketplace & Install Record** | Both `2.1.269` and `2.1.299` install the package from an explicit Git tag and report version, scope, enabled state, and install path via `claude plugin list --json`. The thin launcher resolves `$CLAUDE_PLUGIN_DATA/releases/<ver>/entry.py` before falling back to the retained scripts. | `tests/test_thin_plugin_contract.py` verifies the marketplace/plugin manifests and the zero-inline launcher resolution. |
| **Per-Marketplace Settings** | Tag-based installation updates only this plugin's `extraKnownMarketplaces` entry (`source.ref` and `autoUpdate`), preserving `enabled: false` and unrelated user settings. | `tests/test_thin_plugin_contract.py` verifies the marketplace/plugin version and zero-inline launcher manifests. |
| **Headless Initialization (`--init-only`)** | `claude --init-only` executes `SessionStart` preparation and exits without starting a conversation. Because exit code `0` alone does not guarantee MCP readiness on Windows, tool discovery and runtime readback are verified as separate gates. | `tests/test_launch_and_mcp_surfaces.py` verifies passive `SessionStart` enqueue and the pending-update marker. |
| **Hook Payloads & Neutral Warnings** | Real `Stop` events on both versions deliver `session_id`, `transcript_path`, and `cwd`. Static `systemMessage` warnings surface configuration or capture issues without blocking termination or injecting model context. | `tests/test_launch_and_mcp_surfaces.py` verifies malformed configs, missing fields, invalid schemas, and quiet exit when sharing is off. |
| **MCP Stdio Transport** | Native SDK control initialization discovers both plugin MCP servers (`mindie-knowledge` and `remote-dev`) without a model turn. The stdio facade handles request cancellation independently of active worker execution. | `tests/test_launch_and_mcp_surfaces.py` verifies `knowledge`, `remote`, and `components` stdio tool isolation. |
| **Structured Output Submission** | Tested against a local synthetic HTTP provider on macOS (`2.1.269`), Windows (`2.1.299`), and WSL (`2.1.299`). Claude Code natively retries up to 5 schema-invalid submissions before returning `error_max_structured_output_retries`. If the model returns plain prose without `structured_output`, the adapter explicitly rejects the result rather than parsing prose as a fallback. | Structured-output capture is delegated to `mindie_harness`; `tests/test_transcript.py` verifies the public-record projection consumed by that pipeline. |

### Structured Output & Usage Accounting Notes
- **Single Admitted Invocation**: `model_calls` counts the single native CLI invocation initiated by the worker, while token usage reflects the total accumulated tokens reported by Claude Code across its internal structured-output correction turns.
- **Sanitized Schema Diagnostics**: Using `stream-json --verbose`, the worker records only submission/rejection counts and schema property paths paired with standard validator keywords (`required`, `enum`, `maxLength`). Submitted text, model prose, tool IDs, and raw validator messages are never stored in diagnostic receipts.
- **Official References** (checked against native behavior):
  - [Claude Code CLI Reference](https://code.claude.com/docs/en/cli-reference)
  - [Claude Code Environment Variables](https://code.claude.com/docs/en/env-vars) (`MAX_STRUCTURED_OUTPUT_RETRIES` defaults to 5)
  - [Agent SDK Structured Outputs](https://platform.claude.com/docs/en/agent-sdk/structured-outputs)

---

## 2. Fork Attribution (`--fork-session`)

When a user forks a conversation via `claude --resume <ID> --fork-session`, Claude Code emits a `SessionStart` event with `source: "fork"`, a new child `session_id`, the new `transcript_path`, and `cwd` before the first child prompt is written.

### How `mindie-agent-cc` Isolates Forked Sessions
1. **Independent Fork Session Attribution**: Forked child sessions do **not** inherit the parent session's lease token and are never auto-associated on `SessionStart (source: "fork")`, keeping `SessionStart` passive with zero eager feed sync unless an explicit lease already exists.
2. **Excluding Copied Parent History**: When Claude Code creates the child transcript file, it rewrites copied parent records to carry the child's `sessionId` while keeping their historical timestamps—and prepends the child's own initial `enqueue` queue record before those copied records.
3. **Deterministic Child-Only Capture**: When community sharing is enabled for the child session's project root, `Stop` (`transcript.capture_source`) verifies both the child session's initial native `enqueue` queue timestamp (surviving normal `--resume` and `/compact` operations) and the child lease's `activated_at` timestamp (`max(created_at, activated_at)`), capturing only new public messages created within the child session after its own creation boundary. If attribution metadata is missing or unreadable, `Stop` fails visibly and never falls back to capturing inherited parent history. Attribution header reads are bounded to the first 64 lines / 1 MiB.
