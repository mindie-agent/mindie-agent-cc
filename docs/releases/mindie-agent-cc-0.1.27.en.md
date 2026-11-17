# mindie-agent-cc 0.1.27: Auto-Update Self-Brick Fix (`#364/#365`), `mindie-harness` Substrate Adoption, and Shared Remote Job Handles

[中文](mindie-agent-cc-0.1.27.md)

- **Eliminate Auto-Update Self-Bricking (`#364/#365`)**: Enforces a `2.5 GiB` disk floor before candidate builds while keeping receipt writes unblocked, preserves the healthy active runtime when candidate preparation fails (zero one-way `needs_repair` lockouts), and switches `.mcp.json` to file-backed `scripts/plugin_entry.py` (zero inline `python -c` blobs).
- **Shared `mindie-harness v0.1.0` Substrate & Cross-Connection Remote Job Handles**: Ships verified `assets/mindie-harness-seed.pyz` + `seed-lock.json`, delegates atomic writes, file locks, and RSI helpers to `mindie_harness`, and shares `REMOTE_DEV_STATE_DIR` so background `job_id` handles survive across MCP reconnections and subagents.
- **`0.1.13` Formal Component Baseline**: Locks `mindie-harness v0.1.0`, `mindie-knowledge v0.13.23`, `remote-dev v0.11.13`, `mindie-diagnostics v0.5.16`, `mindie-debugger v0.1.4`, `mindie-profiler v0.5.16`, `mindie-sim v0.9.18`, and `mindie-coordinator v0.7.13` in `pyproject.toml` and `uv.lock`, aligned with `--profile host-plugin` governance.
