"""Knowledge sync uses the real standalone CLI after current-domain admission."""
import json
import os
from pathlib import Path
import sys
import uuid
from unittest.mock import patch

import pytest
from support import make_config

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import admission
import identity
import knowledge_service
import mcp_server
import mindie_launch

SESSION = "645fae2e-ef23-4e7d-9559-f4dd0cb9db6a"


def call_sync(arguments):
    call_id = "sync_" + uuid.uuid4().hex
    identity.publish_bind(
        SESSION, call_id,
        identity.canonical_native_name("knowledge", "knowledge_sync"), arguments,
    )
    return mcp_server.handle("knowledge", {
        "id": call_id, "method": "tools/call", "params": {
            "name": "knowledge_sync", "arguments": arguments,
            "_meta": {"claudecode/toolUseId": call_id},
        },
    })["result"]


@pytest.fixture
def sync_profile(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setenv("MINDIE_CC_CONFIG", str(config))
    adapter = json.loads(config.read_text())
    engine_file = Path(adapter["engine_config"])
    engine = json.loads(engine_file.read_text())
    engine["feeds"] = []
    engine.pop("community_config")
    engine_file.write_text(json.dumps(engine))
    return engine_file, engine


def test_sync_runs_empty_feed_cli_without_starting_knowledge_service(sync_profile, tmp_path):
    admission.gate().activate(SESSION, project_root=str(tmp_path))
    with patch.object(knowledge_service, "ensure_service") as service:
        result = call_sync({})
    assert not result["isError"], result
    assert result["structuredContent"] == {"result": "ok", "results": []}
    service.assert_not_called()
    message = {"method": "tools/call", "params": {"name": "knowledge_sync", "arguments": {}}}
    assert mindie_launch._dispatch_budget("knowledge", message) >= mcp_server.KNOWLEDGE_SYNC_TIMEOUT + 15


def test_sync_preserves_real_failed_feed_row_as_mcp_error(sync_profile, tmp_path):
    engine_file, engine = sync_profile
    engine["feeds"] = [{
        "repository": "fixture/unavailable", "domain": engine["domain"],
        "url": (tmp_path / "absent.git").as_uri(), "ref": "main",
    }]
    engine_file.write_text(json.dumps(engine))
    admission.gate().activate(SESSION, project_root=str(tmp_path))
    with patch.object(knowledge_service, "ensure_service") as service:
        result = call_sync({})
    assert result["isError"], result
    rows = result["structuredContent"]["results"]
    assert rows[0]["repository"] == "fixture/unavailable"
    assert rows[0]["status"] == "unavailable"
    service.assert_not_called()


def test_default_domain_activation_does_not_authorize_another_feed(sync_profile, tmp_path):
    _engine_file, engine = sync_profile
    admission.gate().activate(SESSION, project_root=str(tmp_path))
    other = dict(engine, domain="operators", root=str(tmp_path / "operators"),
                 admission_path=str(tmp_path / "operators-admission.sqlite3"))
    other_file = tmp_path / "operators.json"
    other_file.write_text(json.dumps(other))
    config = Path(os.environ["MINDIE_CC_CONFIG"])
    adapter = json.loads(config.read_text())
    adapter["domain_configs"] = {"operators": str(other_file)}
    config.write_text(json.dumps(adapter))
    with patch("maintenance.sync_public_feed") as sync:
        result = call_sync({"domain": "operators"})
    assert result["isError"], result
    assert "/mindie-agent" in result["content"][0]["text"]
    sync.assert_not_called()
