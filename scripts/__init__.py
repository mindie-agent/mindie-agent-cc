"""MindIE's installed Claude Code adapter; native hosts own plugin selection.

[Role]
Package marker for the Claude Code (`mindie-agent-cc`) adapter scripts.

[Invariants]
- Native Claude Code hosts own plugin discovery and lifecycle invocation.

[Timeouts & Locks]
- None at package import time.

[Side-Effect Policy]
- Pure package marker; performs no I/O on import.
"""
