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
    outer = mindie_launch._dispatch_budget("knowledge", message)
    assert outer >= mcp_server.KNOWLEDGE_SYNC_TIMEOUT + 5 + 5 + 5 + (2 + 2 + 2)
    native = json.loads((Path(__file__).resolve().parents[1] / ".mcp.json").read_text())
    assert native["mcpServers"]["knowledge"]["timeout"] >= outer * 1000


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


def _adapter_without_scanner(tmp_path, monkeypatch):
    config = tmp_path / "cc.json"
    engine = tmp_path / "cc.engine.json"
    redactor = tmp_path / "redactor"
    redactor.write_text("", encoding="utf-8")
    engine.write_text(json.dumps({
        "root": str(tmp_path / "domain"),
        "domain": "vllm-ascend",
        "admission_path": str(tmp_path / "domain" / "admission.sqlite3"),
        "capture_mode": "public-transcript",
        "redactor_executable": str(redactor),
        "transcript_adapter": str(Path(__file__).resolve().parents[1] / "scripts" / "transcript.py"),
        "summary_command": [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" / "organizer.py")],
        "feeds": [],
    }))
    config.write_text(json.dumps({
        "state_dir": str(tmp_path / "state"),
        "engine_config": str(engine),
        "python": sys.executable,
        "adapter_config": str(config),
        "base_config": str(config),
    }))
    monkeypatch.setenv("MINDIE_CC_CONFIG", str(config))
    return engine


def test_sync_cleanup_failure_does_not_flip_successful_result(tmp_path, monkeypatch):
    _adapter_without_scanner(tmp_path, monkeypatch)
    admission.gate().activate(SESSION, project_root=str(tmp_path))
    payload = {
        "result": "ok",
        "results": [{"repository": "fixture/ok", "status": "synced"}],
        "cleanup_failed": True,
        "cleanup": [{"message": "reap_process"}],
    }
    with patch("maintenance.sync_public_feed", return_value=dict(payload)):
        result = call_sync({})
    assert not result["isError"], result
    body = result["structuredContent"]
    assert body["result"] == "ok"
    assert body["cleanup_failed"] is True
    assert body["receipt_unconfirmed"] is True
    assert "cleanup_warning" in body
    assert "receipt_warning" in body
    assert "repeat" in body["cleanup_warning"]


def test_sync_failed_result_stays_error_when_cleanup_also_failed(tmp_path, monkeypatch):
    _adapter_without_scanner(tmp_path, monkeypatch)
    admission.gate().activate(SESSION, project_root=str(tmp_path))
    payload = {
        "result": "sync_failed",
        "results": [{"repository": "fixture/unavailable", "status": "unavailable"}],
        "cleanup_failed": True,
    }
    with patch("maintenance.sync_public_feed", return_value=dict(payload)):
        result = call_sync({})
    assert result["isError"], result
    assert result["structuredContent"]["result"] == "sync_failed"
    assert "receipt_unconfirmed" not in result["structuredContent"]


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
