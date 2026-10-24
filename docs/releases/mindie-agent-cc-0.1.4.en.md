# mindie-agent-cc 0.1.4

[中文](mindie-agent-cc-0.1.4.md) · **English**

Fix component inheritance during automatic Claude Code plugin upgrades after an independent component update. Bootstrap starts `component_update.py` with `python -I`, but the entry previously omitted its own script directory from the module search path. This caused `ModuleNotFoundError: No module named 'product_contract'`. The entry now selects the candidate script directory, retains isolation, and receives `-B`.

Candidate validation now starts the real isolated component CLI through its network-free, model-free `--help` path. Regression tests cover Unicode and space-containing paths, ambient `PYTHONPATH` interference, and a missing candidate helper. The complete local non-NPU suite passed; the Tag workflow delivers cross-platform contracts and formal release benchmarks.

Runtime pins remain the formal releases `knowledge 0.13.4`, `remote-dev 0.10.6`, and `diagnostics 0.5.2`. Software updates do not authorize knowledge sessions, change contribution consent, or modify Claude settings. Native automatic updates on other devices remain separate acceptance; devices already stuck on an older pending candidate may need one recovery.
