#!/usr/bin/env python3
"""Legacy pre-0.1.30 `release_update.py` background poll shim.

[Role] Accepts background poll spawns from still-open `v0.1.29` sessions after `cc.json` activates `0.1.31+` and delegates to `mindie_harness.runtime.wheelhouse.update_runtime`.
[Invariants] Always exits `0` with a JSON status object (`reportable=False`) so legacy sessions never raise maintenance faults.
[Timeouts & Locks] Delegates single-instance locking and backoff to `update_runtime`.
[Side-Effect Policy] Delegates runtime wheelhouse checks only.
"""
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def main() -> int:
    from mindie_harness.runtime.wheelhouse import update_runtime
    import setup

    print(json.dumps(update_runtime(setup._profile(), background=True), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
