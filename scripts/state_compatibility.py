"""Read persisted format declarations before retiring or installing a runtime.

Unknown and incompatible durable formats fail here. This check does not
delete, reset, migrate, or rewrite bodies, cursors, paid worker results, or
external execution receipts.

Knowledge state stays under the engine root. Remote jobs and uncertain
receipts stay under the adapter state root that ``mcp_server.remote_call``
already writes: ``paths.state_dir(adapter) / "remote" / <native session>``.
That directory is also the value assigned to ``REMOTE_DEV_STATE_DIR``. The
caller resolves it and passes the remote base explicitly. This module does
not invent a second store and does not relocate an existing session.
"""

import json
import os
from pathlib import Path
import sys

import receipt_layout


def check(engine_config, remote_base):
    from mindie_knowledge.state_layout import state_root

    try:
        config = (engine_config if isinstance(engine_config, dict)
                  else json.loads(Path(engine_config).read_text(encoding="utf-8")))
        if not isinstance(config, dict):
            raise ValueError("engine config is not an object")
        if not isinstance(remote_base, str) or not os.path.isabs(remote_base):
            raise ValueError("remote state base must be an absolute path")
        state_root(config["root"], config["domain"])
        receipt_layout.receipt_root(remote_base)
    except Exception as exc:
        message = str(exc)
        if "preserved" not in message:
            raise ValueError(message + "; existing state was preserved") from exc
        raise
    return dict(status="compatible")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: state_compatibility.py ENGINE_CONFIG REMOTE_BASE")
    print(json.dumps(check(sys.argv[1], sys.argv[2])))
