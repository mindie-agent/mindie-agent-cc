"""Timeout identity survives local helpers and stays explicit at protocol boundaries."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import candidate_validate
import community_config
import diagnostic_support
import entry
import mindie_launch
import product_contract


def timeout(*args, **kwargs):
    raise TimeoutError("local operation budget exhausted")


def test_status_snapshot_timeout_is_not_unavailable_state(monkeypatch):
    from mindie_knowledge.loop import diagnostics

    monkeypatch.setattr(diagnostics, "snapshot", timeout)
    monkeypatch.setattr("paths.engine_config_path", lambda: Path("/unused/config.json"))
    with pytest.raises(TimeoutError, match="budget exhausted"):
        entry._diagnostics("session")


def test_community_write_timeout_does_not_claim_bytes_were_unchanged(tmp_path, monkeypatch):
    target = tmp_path / "community.json"
    target.write_text('{"enabled": false}')

    def interrupted_write(*args, **kwargs):
        target.write_text('{"enabled": true}')
        raise TimeoutError("write receipt was not received")

    monkeypatch.setattr(community_config.bounded, "run", interrupted_write)
    with pytest.raises(TimeoutError, match="receipt was not received"):
        community_config.configure(target, {})
    assert json.loads(target.read_text()) == {"enabled": True}


@pytest.mark.parametrize("error_type", [TimeoutError, mindie_launch.bounded.CommandTimedOut])
def test_facade_dispatch_keeps_timeout_for_protocol_deadline(monkeypatch, error_type):
    def expired(*args, **kwargs):
        raise error_type("local operation budget exhausted")

    monkeypatch.setattr(mindie_launch, "installed_binding", expired)
    request = {"id": "timeout-call", "method": "tools/call",
               "params": {"name": "remote_bash", "arguments": {"command": "true"}}}
    with pytest.raises(error_type, match="budget exhausted"):
        mindie_launch._dispatch("remote", json.dumps(request).encode(), request["id"])


def test_candidate_timeout_receipt_reaches_callers(monkeypatch, capsys, tmp_path):
    def validate(*args):
        return candidate_validate.checked("source", "source_invalid", timeout)

    monkeypatch.setattr(candidate_validate, "validate", validate)
    monkeypatch.setattr(sys, "argv", [
        "candidate_validate.py", "--source", str(tmp_path), "--identity", "{}",
    ])
    assert candidate_validate.main() == 1
    failure = product_contract.failure_receipt(capsys.readouterr().out, {}, 1)
    assert failure.stage == "validation" and failure.code == "timeout"


def test_diagnostic_metadata_timeout_cannot_replace_original_operation(monkeypatch, capsys):
    original = OSError("original transport failure")
    monkeypatch.setattr(diagnostic_support, "build_metadata", timeout)
    monkeypatch.setattr(diagnostic_support, "_warned", False)
    result = diagnostic_support.failure("mcp.call", "finish_bind", "local_storage", exception=original)
    assert result == {"logging_failed": True, "error_type": "TimeoutError"}
    assert "original outcome unchanged" in capsys.readouterr().err
