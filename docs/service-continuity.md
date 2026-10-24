# Service Continuity & Smooth Updates

<a href="service-continuity.zh.md">中文</a> | <b>English</b> · <a href="README.en.md">Documentation Index</a>

`mindie-agent-cc` uses native Claude Code marketplace selection paired with an immutable `uv` Python runtime. A single adapter configuration file binds the active installation, while the shared `mindie-knowledge` Core owns the background service identity and process lifecycle.

---

## 1. Immutable Runtime Preparation & Admission Locking

### Preparation Outside Active Traffic
- **First Installation**: Initializes the adapter profile after validating durable storage formats.
- **Background Release & Component Preparation**: When a compatible new plugin Release or an independent shared-component Release (discovered directly from official component repositories) is selected, `uv` builds the immutable candidate runtime outside the active operation lock, preserving the installed component version floor across plugin upgrades and validating the candidate via isolated package inventory checks and a model-free API probe without opening the active database or starting a knowledge worker.
- **Shared Lock for Active Requests**: Normal MCP tool calls, read-only status checks, and request cancellations acquire a shared lock and read the current complete runtime binding. Long-running tool calls never block their own cancellation or status inspection.

### Atomic Idle Cutover
- **Exclusive Lock Only When Idle**: Activating a prepared Release requires an exclusive admission lock without killing in-flight work. If the Core service is busy, the switch is deferred until the active operation finishes.
- **Safe Service Handoff**: Once idle, the previous service owner is retired and the canonical installation binding configuration (`~/.config/mindie-agent/cc.json` by default) is published atomically; the background knowledge worker starts only on a subsequent authorized manual knowledge call.
- **No Unintended Work Termination**: The 30-second control observation window bounds how long the switcher waits for idle state—it never terminates active tasks or turns a timeout into a false success receipt.
- **Live MCP Process Continuity**: Already-running MCP stdio processes stay alive across an update and automatically dispatch subsequent requests to the newly committed runtime helper without requiring a manual session restart.

---

## 2. `Stop` Queue Durability & User Data Persistence

### `Stop` Handoff During Updates
- `Stop` reads one immutable binding snapshot without waiting for service admission and appends the capture reference to the persistent SQLite queue.
- Even if a previous helper was just retired during an update, it can safely enqueue an authorized `Stop` event without restarting its retired service owner; the new service owner consumes the same persistent queue and deduplicates native event IDs.
- Known capture failures surface a concise static warning in Claude Code without forcing the model to continue.

### Persistent User Preferences
- Community knowledge sharing and automatic tool fault reporting share a single unified contribution switch in `<profile>/.mindie/mindie-consent.json`, stored alongside approved project scope as persistent user data decoupled from code versions.
- Installing an update or reloading Claude Code never re-prompts for consent, resets project scope, modifies saved `disabled` / `later` / `read-only` choices, or silently upgrades legacy knowledge-only `contribute` records lacking the unified consent revision (`needs_choice`).

---

## 3. Isolated Organizer Model Execution & Service Reuse

- **Exact Fingerprint Matching**: Reusing a running background knowledge service requires matching the exact engine configuration fingerprint, active installation binding, and worker health. A reachable local port from an older generation is never mistakenly reused, and the adapter never spawns a competing second worker on error.
- **Isolated Organizer Environment**: When invoking Claude Code headlessly to generate block navigation metadata, the worker imports only the supported provider, model, and effort fields from the user's selected profile—never loading user hooks, MCP servers, tools, or project-level prompts, and never writing API secrets into status receipts.

---

## 4. Verification Scope

- **Local Daemon & Native Acceptance**: Covered live service handoff, absent service, revoked lease, and rollback paths on macOS (`2.1.269`), native Windows, and WSL (`2.1.299`), confirming that active task leases survive updates and that all owned processes clean up on disablement.
