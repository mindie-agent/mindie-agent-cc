"""Read persisted format declarations before retiring or installing a runtime.

Unknown and incompatible durable formats fail here. This check does not
delete, reset, migrate, or rewrite bodies, cursors, paid worker results, or
external execution receipts.
"""

import json
from pathlib import Path
import sys

import receipt_layout


def remote_receipt_dir(engine):
    """Receipts live beside adapter state, never inside a knowledge body."""
    configured = engine.get("remote_receipt_dir")
    if isinstance(configured, str) and configured:
        return Path(configured)
    root = engine.get("root")
    if not isinstance(root, str) or not root:
        raise ValueError("engine config has no durable root for remote receipts")
    return Path(root) / "remote-receipts"


def check(engine_config):
    from mindie_knowledge.state_layout import state_root

    try:
        config = json.loads(Path(engine_config).read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("engine config is not an object")
        state_root(config["root"], config["domain"])
        receipt_layout.receipt_root(remote_receipt_dir(config))
    except Exception as exc:
        message = str(exc)
        if "preserved" not in message:
            raise ValueError(message + "; existing state was preserved") from exc
        raise
    return dict(status="compatible")


if __name__ == "__main__":
    print(json.dumps(check(sys.argv[1])))
