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
    run_probe(tmp_path, SCRIPTS, redactor_path())


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


def test_updater_preserves_absent_retirement_and_cleanup_failure():
    import updater
    receipt = dict(status="retired", operation_id="a" * 32)
    observation = dict(idle=True, service="absent", retirement=receipt)
    for cleanup_failed in (False, True):
        value = dict(observation, cleanup_failed=cleanup_failed)
        with patch.object(updater, "_generation_engine", return_value="engine.json"), \
                patch.object(updater, "_remaining", return_value=30), \
                patch.object(updater, "read_status", return_value={}), \
                patch.object(updater, "_record") as record, \
                patch.object(updater, "bounded_run", return_value=json.dumps(value)) as run:
            if cleanup_failed:
                with pytest.raises(updater.CheckFailed, match="retired but cleanup failed"):
                    updater.stop_if_idle({}, {"sha": "a" * 40, "python": sys.executable}, 100)
            else:
                assert updater.stop_if_idle({}, {"sha": "a" * 40, "python": sys.executable}, 100) == value
        assert run.call_args.kwargs.get("timeout") is None
        saved = record.call_args.kwargs["service_handoff"]
        assert saved["retirement"] == receipt
        assert saved["observation"] == value
        assert saved["status"] == ("failed" if cleanup_failed else "retired")


def test_updater_preserves_unknown_retirement_and_does_not_restore():
    import updater
    value = dict(status="failed", error="OSError",
                 retirement=dict(status="retiring", operation_id="b" * 32))
    with patch.object(updater, "_generation_engine", return_value="engine.json"), \
            patch.object(updater, "_remaining", return_value=30), \
            patch.object(updater, "read_status", return_value={}), \
            patch.object(updater, "_record") as record, \
            patch.object(updater, "_restore_stopped_service") as restore, \
            patch.object(updater, "bounded_run", return_value=json.dumps(value)):
        with pytest.raises(updater.CheckFailed, match="stop result unconfirmed"):
            updater.stop_if_idle({}, {"sha": "a" * 40, "python": sys.executable}, 100)
    restore.assert_not_called()
    assert record.call_args.kwargs["service_handoff"]["observation"] == value


def test_updater_status_failure_restores_the_exact_known_retirement():
    import updater
    receipt = dict(status="retired", operation_id="c" * 32)
    value = dict(idle=True, service="absent", retirement=receipt)
    current = dict(sha="a" * 40, python=sys.executable)
    with patch.object(updater, "_generation_engine", return_value="engine.json"), \
            patch.object(updater, "_remaining", return_value=30), \
            patch.object(updater, "read_status", return_value={}), \
            patch.object(updater, "_record", side_effect=[None, PermissionError("status")]), \
            patch.object(updater, "_restore_stopped_service") as restore, \
            patch.object(updater, "bounded_run", return_value=json.dumps(value)):
        with pytest.raises(PermissionError, match="status"):
            updater.stop_if_idle({}, current, 100)
    restore.assert_called_once_with({}, current, 100, retirement=receipt, retired=current)


def test_helper_cleanup_failure_keeps_known_retirement_and_bars_install():
    import updater
    receipt = dict(status="retired", operation_id="d" * 32)
    value = dict(idle=True, service="stopped", retirement=receipt)

    def completed_with_failed_cleanup(*args, **kwargs):
        kwargs["receipt"].update(cleanup_failed=True,
                                 cleanup=[dict(stage="terminate_owned_tree", error_type="PermissionError")])
        return json.dumps(value)

    with patch.object(updater, "_generation_engine", return_value="engine.json"), \
            patch.object(updater, "_remaining", return_value=30), \
            patch.object(updater, "read_status", return_value={}), \
            patch.object(updater, "_record") as record, \
            patch.object(updater, "bounded_run", side_effect=completed_with_failed_cleanup):
        with pytest.raises(updater.CheckFailed, match="retired but cleanup failed"):
            updater.stop_if_idle({}, dict(sha="a" * 40, python=sys.executable), 100)
    saved = record.call_args.kwargs["service_handoff"]
    assert saved["retirement"] == receipt
    assert saved["observation"]["service"] == "stopped"
    assert saved["observation"]["helper_cleanup"][0]["error_type"] == "PermissionError"
