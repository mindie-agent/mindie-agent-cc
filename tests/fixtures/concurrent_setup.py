"""Exercise concurrent native preparation with real knowledge state and locks."""
from contextlib import ExitStack
import json
from pathlib import Path
import sys
from unittest.mock import patch

scripts, root, role = map(Path, sys.argv[1:])
sys.path.insert(0, str(scripts))

import setup
from mindie_knowledge.git_layout import validate_config_feed_layout
from mindie_knowledge.loop.store import Store

validation = {"schema": "mindie-candidate-check/1", "publication": {"domain": "fixture"}}
original_open = Store._open_state


def open_state(self, directory, domain):
    original_open(self, directory, domain)
    if str(role) == "first":
        print("state-open-before-layout", flush=True)
        assert sys.stdin.readline() == "continue\n"


def prepare_store(python, scripts, config):
    # Keep the real Windows feed preflight and Store ordering. The first
    # process pauses after opening the DB, before publishing state-layout.json.
    with patch("mindie_knowledge.git_layout.WINDOWS", True):
        validate_config_feed_layout(dict(config, feeds=[{
            "repository": "https://github.com/example/knowledge", "ref": "main",
        }]))
    store = Store(config["root"], config["domain"])
    store.close()


with ExitStack() as stack:
    stack.enter_context(patch("bootstrap.runtime_key", return_value="candidate"))
    stack.enter_context(patch.object(setup, "validate_candidate", return_value=validation))
    stack.enter_context(patch("candidate_validate.validate", return_value=validation))
    stack.enter_context(patch.object(setup, "identity", return_value={}))
    stack.enter_context(patch("harness.require_engine_support", return_value="2.1.269"))
    stack.enter_context(patch("release_update.disable_unfiltered_updates"))
    stack.enter_context(patch("capture_config.prepare", return_value={}))
    stack.enter_context(patch("capture_config.prepare_store", side_effect=prepare_store))
    stack.enter_context(patch.object(Store, "_open_state", open_state))
    stack.enter_context(patch.object(setup, "write_community"))
    stack.enter_context(patch.object(setup, "service_launch_context", return_value={
        "paths": {"CLAUDE_CONFIG_DIR": str(root / "claude")}, "environment": [],
    }))
    print("started", flush=True)
    result = setup.prepare_binding(scripts.parent, root / "cc.json", root=root / "data",
                                   domain="fixture", no_public_feed=True)
    print(json.dumps(result), flush=True)
