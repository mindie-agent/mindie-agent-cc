# MindIE Agent Claude Code adapter

Inherit all nine VAWS principles from https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.md. Keep this a native Claude Code adapter: no copied knowledge engine, publication database, independent harness or legacy VAWS path.

Knowledge reads bind to the native tool call and do not require an entry invocation or a capture lease; remote development is available independently of contribution. Sharing is opt-in. With contribution configured, an eligible in-scope native Stop is captured automatically: the task association is internal, uses the task's own transcript artifact and the native SessionStart fork boundary, is never a consent prompt, and never undoes an explicit deactivate. Fork SessionStart may establish the existing Admission from the exact named child artifact and native project scope before inherited history is copied; Stop never adopts inherited parent scope. Without contribution, Stop performs no transcript collection or summary-worker invocation. Hooks never continue the business model.

The summary worker is the adapter's only model call: one bounded native `claude` invocation per admitted block batch, with explicit outcome and usage receipts; an uncertain or billed outcome is reported, never silently retried.

Use native Claude interfaces and real local Claude acceptance. Keep implementation and component checks separate from host and full-loop evidence. Preserve unrelated user configuration.

Prefer GitHub connector; fall back to gh only if unavailable or incapable.
