# mindie-agent-cc 0.1.6

Pins remote-dev `4c40cb5` and mindie-diagnostics 0.5.4 (`5ae643e`). Diagnostics allocates the first/free log slot with `O_EXCL` before reader-registry/cursor coordination, fixing the intermittent CI failure that the old real-code setup hit at about 101 ms; full-window reader and pressure semantics are unchanged, with no asynchronous redesign.

Command/Bash/TaskOutput large logs use the core formatter for a bounded UTF-8 head/tail, omission note, and full remote raw-log refs and explicitly labelled local cached slices; incremental pages stay lossless. Preview failures are explicitly `preview_degraded` while retaining the real exit/result.

Updater/manifest handling keeps `MCP_TOOL_TIMEOUT=660000` and the Claude profile. No formal release or global host changes are made here. Also includes an intentional test-only forensics change: the launch-entry regression helper now asserts the recorded stage/category and includes stdout/stderr/paths when logs are absent.
