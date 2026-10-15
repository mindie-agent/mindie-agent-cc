"""Actual OS lifetime evidence plus exact retirement receipt failure gates."""
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from support import SCRIPTS, redactor_path
from service_lifetime_fixture import run_probe

sys.path.insert(0, str(SCRIPTS))
import service_handoff


def test_real_helper_reuses_service_and_index_until_core_retirement(tmp_path):
    run_probe(tmp_path / "service", SCRIPTS, redactor_path())


@pytest.mark.skipif(os.name != "nt", reason="real Windows Job ownership")
def test_windows_service_transfer_preserves_ordinary_descendant_cleanup(tmp_path):
    fixture = Path(__file__).with_name("windows_lifecycle_fixture.py")
    output = subprocess.check_output(
        [sys.executable, str(fixture), "--root", str(tmp_path), "--scripts", str(SCRIPTS)],
        text=True, timeout=35,
    )
    cases = json.loads(output)["cases"]
    for name in ("default-explicit", "service-ordinary"):
        if name == "default-explicit" and "spawn_error" in cases[name]:
            # A base interpreter may be directly inside the non-breakaway Job;
            # a Windows venv redirector adds a nested Job and can start a child
            # which is still retained by the outer Job. Both must stay owned.
            assert cases[name]["spawn_error"]["winerror"] == 5
            assert cases[name]["completed"] is None
            continue
        steps = cases[name]["sequence"]
        assert steps[0]["wait"] == 258
        assert steps[1]["wait"] == 0
        assert steps[2]["exit_code"] == 1
        assert cases[name]["completed"] is None
    steps = cases["service-explicit"]["sequence"]
    assert steps[0]["wait"] == steps[1]["wait"] == 258
    assert steps[2]["wait"] == 0 and steps[2]["exit_code"] == 0
    assert cases["service-explicit"]["completed"] == {"released": True}


def test_stop_does_not_reinterpret_unconfirmed_ownership():
    with patch.object(service_handoff, "retire_service", side_effect=OSError("ownership unconfirmed")):
        with pytest.raises(OSError, match="ownership unconfirmed"):
            service_handoff.stop("engine.json")


def test_stale_endpoint_with_released_consumer_retires_without_tcp(tmp_path):
    from mindie_knowledge.loop.cli import connection_path
    from mindie_knowledge.loop.locks import StartLock
    engine = tmp_path / "engine.json"
    config = dict(root=str(tmp_path / "data"), domain="test",
                  redactor_executable=str(redactor_path()), transcript_adapter=str(SCRIPTS / "transcript.py"))
    engine.write_text(json.dumps(config))
    consumer = StartLock(tmp_path / "data" / "test" / "consumer.lock")
    consumer.acquire()
    consumer.release()
    target = connection_path(config)
    target.write_text(json.dumps(dict(url="http://127.0.0.1:9", token="fixture")))
    with patch("mindie_knowledge.loop.transport.rpc", side_effect=AssertionError("TCP is not lifetime authority")):
        result = service_handoff.stop(str(engine))
    assert result["service"] == "absent"
    assert result["retirement"]["status"] == "retired"
    assert not target.exists()


def test_restore_requires_exact_retirement_before_service_spawn(tmp_path):
    from mindie_knowledge.loop.lifecycle import retire_service
    engine = tmp_path / "engine.json"
    engine.write_text(json.dumps(dict(root=str(tmp_path / "data"), domain="test",
                                     redactor_executable=str(redactor_path()),
                                     transcript_adapter=str(SCRIPTS / "transcript.py"))))
    retirement = retire_service(engine)["retirement"]
    wrong = dict(retirement, operation_id="0" * 32)
    with patch.object(service_handoff, "ensure_service") as ensure:
        with pytest.raises(ValueError, match="exact completed retirement"):
            service_handoff.restore(str(engine), wrong)
    ensure.assert_not_called()
