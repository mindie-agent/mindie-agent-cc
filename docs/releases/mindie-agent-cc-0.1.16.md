# mindie-agent-cc 0.1.16

`mindie-agent-cc 0.1.16` hardens Windows zero-console-popup execution across bounded subprocesses (`scripts/bounded_win32.py`), stops classifying transient `native_update` environment misses as `update_internal_failure` (`#61`), and aligns all runtime and component pins (`mindie-knowledge 0.13.12`, `remote-dev 0.11.5`, `mindie-diagnostics 0.5.7`, `mindie-coordinator 0.7.4`).

## What's New (Human-Readable Summary)

- **Windows Zero Console Popup Hardening (`scripts/bounded_win32.py`)**: Explicitly combines `subprocess.CREATE_NO_WINDOW` with `CREATE_NEW_PROCESS_GROUP` on Windows so background updater, hook, and MCP subprocesses never flash console windows from GUI hosts.
- **Transient Update Environment Miss Handling (`#61`)**: Stops treating transient `native_update` environment misses as `update_internal_failure`.
- **Upgraded Runtime & Component Contracts**:
  - Locked runtime (`pyproject.toml`, `uv.lock`): `mindie-knowledge 0.13.12` (`1840bd4e238e718676768489058917818d18cd3c`), `remote-dev 0.11.5` (`131b24533479e9cefc572144adccdcb345f70846`), and `mindie-diagnostics 0.5.7` (`f338788cecc56f7aff1dccab1d53426c9e28fe7f`).
  - Component contracts (`test` group): `mindie-debugger 0.1.0` (`e9406b2b178335190d5cb59dff4d6985894497ba`), `mindie-profiler 0.5.6` (`79d97e75bff7351b0472b93c03b072c1cf47f5e7`), `mindie-sim 0.9.3` (`dbadff04b60937a0722b0a227cdad01beed083cd`), and `mindie-coordinator 0.7.4` (`14d7cec7247bdcbea9b9878db4770e3a05a6ea41`).
- **Unified `setup.py sharing` & Native Step 5 Onboarding Popup**: Adds `setup.py sharing --status|--enable|--disable` with fast stdlib status inspection, incremental `GIT_CONFIG_COUNT` enterprise network compatibility, and native `AskUserQuestion` onboarding popup.
