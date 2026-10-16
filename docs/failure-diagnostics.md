# Failure Diagnostics & Local Cancellation

<a href="failure-diagnostics.zh.md">中文</a> | <b>English</b> · <a href="README.en.md">Documentation Index</a>

Following Principle 9 of the [MindIE Agent Engineering Design Principles](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.en.md) (*observable, diagnosable tools; never hide errors*), `mindie-agent-cc` exposes clear read-only diagnostics, deterministic local process cancellation, and an independent opt-in channel for sanitized product fault reporting.

---

## 1. Read-Only Status Inspection (`/mindie-agent:status`)

- **What It Reports**: Current knowledge configuration, background service and storage health, the active task's binding state and diagnostic failure counter, and the latest 5 captures and contribution batches associated with the bound task.
- **Strictly Read-Only**: Calling `status` never initializes databases, starts models or background services, binds an unbound task, resets failure counters, or replays consumed inputs.
- **Corrupt Config Detection**: If an existing adapter configuration file is corrupted or malformed, `status` reports an explicit configuration diagnostic rather than treating the corruption as a "first-time install" and re-running onboarding.
- **Decoupled Remote Execution**: Even if the local knowledge service is unavailable, native shell/SSH and the `remote-dev` MCP server remain fully operational.

---

## 2. Local Cancellation vs. Remote Ascend Jobs

- **Responsive Stdio Frontend**: The MCP stdio frontend remains responsive to control messages (`ping`, cancellation, and `EOF`) while tool requests are running.
- **Local Process Cleanup**: Cancelling a tool call or closing stdin immediately terminates the local helper process tree owned by that specific request.
- **Durable Remote Job Separation**: Cancelling a local observation or polling call does **not** mean a remote compilation or test job on the Ascend server has stopped. The response preserves the original remote job ID and delivery certainty so you can explicitly inspect or stop the remote job before starting a new one. Single remote poll requests wait up to 30 seconds without altering the remote workload's own timeout.

---

## 3. Sanitized Native Organizer Diagnostics

When an isolated native Claude call for block-index generation fails, the adapter records a bounded `mindie-cc-native-failure/1` diagnostic projection inside the private outcome receipt:
- **Included**: Known native terminal/subtype enums (such as turn-limit, cost-budget, or structured-output retry exhaustion), validated turn counts, HTTP status codes, and field counts.
- **Excluded**: Provider or model free text, prompts, session IDs, unknown labels, and credentials.
- **No Silent Replay**: The original token usage and failed outcome remain authoritative; failed or uncertain paid calls are never automatically re-invoked.

---

## 4. Opt-In Product Fault Reporting (`/mindie-agent:reporting-*`)

`mindie-agent-cc` integrates with the shared `mindie-diagnostics` reporter:
- **Independent Opt-In**: Product fault reporting is off by default and completely separate from community knowledge sharing. Saving `/mindie-agent:reporting-enable` records your preference and returns a one-time terminal setup command.
- **Sanitized Public Payload**: Confirmed product defects generate local diagnostic records and—when reporting is enabled—submit redacted GitHub Issues containing **only** static product stages, error categories, and installed code versions. Task prompts, transcripts, raw stack traces, commands, file paths, and credentials are never included.
- **Deduplication & Reconciliation**: Duplicate faults share a fingerprint, and any uncertain GitHub Issue creation attempt is reconciled before retrying so duplicates are never spammed.

---

## 5. Verification Scope

- **macOS & Component Verification**: Covered task-isolated SQLite diagnostics, corrupt config detection, child/grandchild process tree cancellation, busy-state `ping`, `EOF` handling, output backpressure, and all three native organizer terminal limits.
- **Windows Hardware Boundary**: Synthetic log/outbox/Issue checks and Windows process-identity checks are verified in `mindie-agent/diagnostics`; native Windows pipe-write backpressure and full host UI cancellation remain separate hardware acceptance items.
