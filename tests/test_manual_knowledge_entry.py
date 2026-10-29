"""A native tool binding cannot substitute for this session's manual entry."""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from support import make_config

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import admission
import bridge
import mcp_server
import maintenance


@pytest.mark.parametrize("state", ["missing", "automatic", "other-session", "revoked"])
@pytest.mark.parametrize("name,arguments", [
    ("knowledge_query", {"query": "KDA"}),
    ("knowledge_explain", {"ref": "mindie://vllm-ascend/units/kda@1"}),
    ("knowledge_feedback", {"ref": "mindie://vllm-ascend/units/kda@1", "rating": "up"}),
    ("knowledge_distill", {"inputs": [], "task_state": "in_flight"}),
    ("knowledge_sync", {}),
])
def test_knowledge_denial_precedes_service(tmp_path, monkeypatch, state, name, arguments):
    config = make_config(tmp_path)
    monkeypatch.setenv("MINDIE_CC_CONFIG", str(config))
    gate = admission.gate()
    if state == "automatic":
        gate.associate("session", project_root=str(tmp_path), not_before=1.0)
    elif state == "other-session":
        gate.activate("parent", project_root=str(tmp_path))
    elif state == "revoked":
        gate.activate("session", project_root=str(tmp_path))
        gate.deactivate("session")
    with patch("knowledge_service.ensure_service") as ensure, \
            patch.object(maintenance, "sync_public_feed") as sync:
        with pytest.raises(ValueError, match="/mindie-agent"):
            mcp_server.knowledge_call(name, arguments, "session")
        ensure.assert_not_called()
        sync.assert_not_called()


def test_passive_stop_has_no_context_or_transcript_access(tmp_path, monkeypatch, capsys):
    config = make_config(tmp_path, sharing=True)
    monkeypatch.setenv("MINDIE_CC_CONFIG", str(config))
    event = dict(hook_event_name="Stop", session_id="645fae2e-ef23-4e7d-9559-f4dd0cb9db6a")
    with patch.object(bridge, "_read_event", return_value=event), \
            patch("sharing.load") as sharing, patch("transcript.capture_source") as transcript:
        assert bridge.handle_stop() == 0
        sharing.assert_not_called()
        transcript.assert_not_called()
    assert capsys.readouterr().out == "{}\n"
    assert not admission.gate().path.exists()


def test_diagnostic_maintenance_runs_even_when_knowledge_admission_fails(tmp_path, monkeypatch):
    from mindie_knowledge.loop.activation import AdmissionUnavailable
    config = make_config(tmp_path)
    monkeypatch.setenv("MINDIE_CC_CONFIG", str(config))
    def completed(command, *args, receipt=None, **kwargs):
        if receipt is not None:
            receipt["returncode"] = 0
            return '{"status":"ok"}'
        return '{"status":"disabled"}'
    with patch.object(maintenance, "run", side_effect=completed) as run, \
            patch("mindie_knowledge.loop.activation.Admission.knowledge_lease",
                  side_effect=AdmissionUnavailable("fixture authority failure")), \
            patch.object(maintenance, "sync_public_feed") as sync:
        with pytest.raises(AdmissionUnavailable):
            maintenance.session_start({"session_id": "ordinary-session"})
        assert run.call_count == 2
        sync.assert_not_called()


def test_discovered_status_tool_cannot_inject_knowledge_setup_context(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setenv("MINDIE_CC_CONFIG", str(config))
    import identity
    session = "645fae2e-ef23-4e7d-9559-f4dd0cb9db6a"
    name = "mcp__plugin_mindie-agent_knowledge__mindie_status"
    identity.publish_bind(session, "call_manual_status", name, {})
    result = mcp_server.handle("knowledge", dict(
        jsonrpc="2.0", id=1, method="tools/call", params=dict(
            name="mindie_status", arguments={}, _meta={"claudecode/toolUseId": "call_manual_status"})))
    assert not result["result"]["isError"]
    facts = result["result"]["structuredContent"]
    assert facts["configured"]
    assert facts["knowledge_enabled"] is False
    assert not {"hint", "next", "note", "choices", "required"}.intersection(facts)
    assert not admission.gate().path.exists()
