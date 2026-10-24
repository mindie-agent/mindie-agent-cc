"""Real capture queue during owner cutover; synthetic input and zero model calls."""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from support import SCRIPTS, env_for, make_config, store_file, write_json

sys.path.insert(0, str(SCRIPTS))
from install_state import OperationLock, atomic_write, binding_snapshot
from mindie_knowledge import consent_store
from mindie_knowledge.loop.activation import Admission
from mindie_knowledge.loop.cli import config_at
from mindie_knowledge.loop.handoff import _probe, prepare_schema
from mindie_knowledge.loop.lifecycle import inspect_retirement, retire_service


def capture_fixture(root, *, activate=True):
    model_marker = root / "model-must-not-run"
    canonical = make_config(
        root, sharing=True,
        summary_command=[sys.executable, "-c",
                         f"from pathlib import Path; Path({str(model_marker)!r}).touch();"
                         "raise SystemExit(1)"],
    )
    binding = json.loads(canonical.read_text(encoding="utf-8"))
    binding.update(
        adapter_config=str(root / "old" / "adapter.json"),
        claude_config_dir=str(root / "claude"),
    )
    write_json(Path(binding["adapter_config"]), binding)
    atomic_write(canonical, binding)
    consent_store.record_choice(root / "mindie-consent.json", "contribute")
    prepare_schema(binding["engine_config"])
    old_retirement = retire_service(binding["engine_config"])
    session = "658e5378-405c-4cd5-a115-55aa9cff771f"
    transcript = root / "claude" / "projects" / "synthetic" / (session + ".jsonl")
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({
        "type": "queue-operation", "operation": "enqueue",
        "sessionId": session, "timestamp": "2026-10-15T12:00:00Z",
    }) + "\n" + json.dumps({
        "type": "system", "sessionId": session, "cwd": str(root / "project"),
        "timestamp": "2026-10-15T12:00:00Z",
    }) + "\n", encoding="utf-8")
    event = {
        "hook_event_name": "Stop", "session_id": session,
        "prompt_id": "cutover-1", "transcript_path": str(transcript),
        "stop_hook_active": False,
    }
    env = env_for(canonical, {
        "MINDIE_DIAGNOSTICS_ROOT": str(root / "diagnostics"),
        "MINDIE_DIAGNOSTICS_CONFIG": str(root / "reporting-off.json"),
    })
    if activate:
        engine = json.loads(Path(binding["engine_config"]).read_text())
        with patch("mindie_knowledge.loop.activation.time.time", return_value=1792065599):
            Admission(engine["admission_path"]).activate(session, project_root=str(root / "project"))
    return canonical, binding, event, env, model_marker, old_retirement


def stop_failure_details(env, result, elapsed, error_type):
    details = {
        "elapsed_seconds": round(elapsed, 6),
        "returncode": result.returncode,
        "assertion_error_type": error_type,
        "stdout_bytes": len(result.stdout),
        "stderr": {
            "bytes": len(result.stderr),
            "sha256": hashlib.sha256(result.stderr).hexdigest(),
            "markers": [label for label in (
                "Traceback", "TimeoutError", "PermissionError", "JSONDecodeError",
                "diagnostic storage unavailable",
            ) if label.encode() in result.stderr],
        },
    }
    try:
        from mindie_diagnostics.local_snapshot import recent_failures

        snapshot = recent_failures([env["MINDIE_DIAGNOSTICS_ROOT"]])
        details["diagnostics"] = {
            key: snapshot[key]
            for key in ("status", "truncated", "scanned_files", "errors")
        }
        details["diagnostics"]["recent"] = [
            {key: row[key] for key in ("operation", "stage", "category", "error_type")
             if key in row}
            for row in snapshot["recent"]
        ]
    except Exception as exc:
        details["diagnostics"] = {"read_error_type": type(exc).__name__}
    try:
        database = store_file(Path(env["MINDIE_CC_CONFIG"]).parent)
        if not database.exists():
            details["captures"] = {"state": "database_missing"}
        else:
            with closing(sqlite3.connect(
                    database.as_uri() + "?mode=ro", uri=True, timeout=0.1)) as db:
                statuses = [row[0] for row in db.execute("SELECT status FROM captures")]
            known = ("queued", "processing", "failed", "cancelled",
                     "no-new-material", "no-shareable-material")
            details["captures"] = {
                "total": len(statuses),
                "states": {state: statuses.count(state) for state in known},
                "other": sum(state not in known for state in statuses),
            }
    except Exception as exc:
        details["captures"] = {"read_error_type": type(exc).__name__}
    return details


def raw_stop(event, env, *, failed=False):
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "mindie_launch.py"), "hook", "stop"],
        input=json.dumps(event).encode(), capture_output=True, env=env, timeout=5,
    )
    elapsed = time.monotonic() - started
    try:
        assert result.returncode == 0
        output = json.loads(result.stdout)
        if failed:
            assert set(output) == {"systemMessage"}
            assert "encountered an error" in output["systemMessage"]
            assert "decision" not in output and "continue" not in output
        else:
            assert output == {}
    except (AssertionError, ValueError) as exc:
        # Reuse the bounded diagnostic projection; never print transcript or stderr text.
        details = stop_failure_details(env, result, elapsed, type(exc).__name__)
        pytest.fail(json.dumps(details, sort_keys=True), pytrace=False)


def capture_rows(root):
    with closing(sqlite3.connect(store_file(root))) as db:
        return db.execute("SELECT id,turn,status FROM captures ORDER BY turn").fetchall()


def wait_for(check, *, child=None):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if check():
            return
        if child is not None:
            assert child.poll() is None, "synthetic direct service exited before readiness"
        time.sleep(0.02)
    raise AssertionError("synthetic service observation did not complete")


def test_stop_commits_during_cutover_and_new_owner_consumes_each_event_once(tmp_path):
    canonical, old, event, env, model_marker, retirement = capture_fixture(tmp_path)
    new_engine_path = tmp_path / "new" / "engine.json"
    engine = config_at(old["engine_config"])
    write_json(new_engine_path, engine)
    selected = dict(old, adapter_config=str(tmp_path / "new" / "adapter.json"),
                    engine_config=str(new_engine_path))
    write_json(Path(selected["adapter_config"]), selected)
    child = None
    try:
        with OperationLock(old).exclusive():
            raw_stop(event, env)
            before_ready = capture_rows(tmp_path)
            assert len(before_ready) == 1 and before_ready[0][2] == "queued"
            assert _probe(engine, 0.25, config_path=old["engine_config"]) == "absent"

            with (tmp_path / "service.log").open("wb") as log:
                child = subprocess.Popen(
                    [sys.executable, "-m", "mindie_knowledge.loop.cli",
                     "--config", str(new_engine_path), "serve"],
                    env=env, stdout=log, stderr=subprocess.STDOUT,
                )
            wait_for(lambda: _probe(engine, 0.25, config_path=new_engine_path) == "ready",
                     child=child)
            # Canonical still points at the retired helper. Its late duplicate
            # and a new event must enter the same queue without reviving it.
            raw_stop(event, env)
            event2 = dict(event, prompt_id="cutover-2")
            raw_stop(event2, env)
            atomic_write(canonical, selected)
            raw_stop(event2, env)
            wait_for(lambda: len(capture_rows(tmp_path)) == 2 and all(
                row[2] in {"no-new-material", "no-shareable-material"}
                for row in capture_rows(tmp_path)), child=child)
            rows = capture_rows(tmp_path)
            assert rows[0][0] == before_ready[0][0]
            assert [row[1] for row in rows] == ["cutover-1", "cutover-2"]
            assert inspect_retirement(old["engine_config"]) == retirement["retirement"]
            assert _probe(engine, 0.25, config_path=new_engine_path) == "ready"
            assert _probe(engine, 0.25, config_path=old["engine_config"]) != "ready"
            assert not model_marker.exists()

            Admission(engine["admission_path"]).deactivate(event["session_id"])
            raw_stop(dict(event, prompt_id="revoked"), env)
            assert capture_rows(tmp_path) == rows
            consent_store.record_choice(tmp_path / "mindie-consent.json", "disabled")
            raw_stop(dict(event, prompt_id="disabled"), env)
            assert capture_rows(tmp_path) == rows
    finally:
        if child is not None:
            try:
                retire_service(new_engine_path, control_timeout=10)
                child.wait(timeout=10)
            finally:
                if child.poll() is None:
                    child.terminate()
                    child.wait(timeout=5)


def test_stop_snapshot_never_mixes_helpers_when_canonical_changes(tmp_path):
    import install_state
    canonical, old, _, _, _, _ = capture_fixture(tmp_path)
    selected = dict(old, adapter_config=str(tmp_path / "new" / "adapter.json"),
                    engine_config=str(tmp_path / "new" / "engine.json"),
                    python=str(tmp_path / "new" / "python"),
                    runtime_scripts=str(tmp_path / "new" / "scripts"))
    write_json(Path(selected["adapter_config"]), selected)
    actual_read = install_state.read_binding
    canonical_reads = []

    def read(path=None):
        if Path(path) == canonical:
            canonical_reads.append(path)
        if Path(path) == Path(old["adapter_config"]):
            atomic_write(canonical, selected)
        return actual_read(path)

    with patch.dict(os.environ, {"MINDIE_CC_CONFIG": str(canonical)}), \
            patch.object(install_state, "read_binding", side_effect=read):
        assert binding_snapshot() == old
    assert len(canonical_reads) == 1
    assert json.loads(canonical.read_text(encoding="utf-8")) == selected


def test_stop_snapshot_rejects_incomplete_selected_helper(tmp_path):
    canonical, old, _, _, _, _ = capture_fixture(tmp_path)
    write_json(Path(old["adapter_config"]), dict(old, python=str(tmp_path / "wrong-python")))
    with patch.dict(os.environ, {"MINDIE_CC_CONFIG": str(canonical)}):
        with pytest.raises(ValueError, match="binding is incomplete"):
            binding_snapshot()


@pytest.mark.parametrize("failure", ["schema", "authority", "admission"])
def test_stop_capture_failure_is_a_native_nonblocking_warning(tmp_path, failure):
    _, binding, event, env, model_marker, _ = capture_fixture(tmp_path)
    if failure == "schema":
        with closing(sqlite3.connect(store_file(tmp_path))) as db:
            db.execute("DROP TABLE captures")
            db.execute("CREATE TABLE captures(id TEXT, turn TEXT, status TEXT)")
            db.commit()
    elif failure == "authority":
        (tmp_path / "mindie-consent.json").write_text("{PRIVATE_SENTINEL", encoding="utf-8")
    else:
        engine = config_at(binding["engine_config"])
        Path(engine["admission_path"]).write_bytes(b"PRIVATE_SENTINEL_not_sqlite")
    with OperationLock(binding).exclusive():
        raw_stop(event, env, failed=True)
    assert capture_rows(tmp_path) == []
    assert not model_marker.exists()


def test_stop_outside_scope_and_explicit_off_remain_quiet_during_cutover(tmp_path):
    _, binding, event, env, _, _ = capture_fixture(tmp_path)
    transcript = Path(event["transcript_path"])
    rows = [json.loads(raw) for raw in transcript.read_text(encoding="utf-8").splitlines()]
    rows[-1]["cwd"] = str(tmp_path / "outside-approved-scope")
    # A manual session binding owns its scope; moving that binding outside
    # contribution scope must keep capture quiet without revoking knowledge.
    engine = config_at(binding["engine_config"])
    Admission(engine["admission_path"]).activate(event["session_id"], project_root=rows[-1]["cwd"])
    transcript.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    with OperationLock(binding).exclusive():
        raw_stop(event, env)
        community = Path(binding["community_config"])
        value = json.loads(community.read_text(encoding="utf-8"))
        value["enabled"] = False
        atomic_write(community, value)
        raw_stop(event, env)
    assert capture_rows(tmp_path) == []


def test_committed_capture_warns_only_for_known_worker_or_wake_failure():
    import bridge
    cases = [
        ({"stage": "accepted-local", "wake": "failed"}, True),
        ({"stage": "accepted-local", "wake": "not-requested", "reason": "worker-dead"}, True),
        ({"stage": "accepted-local", "runtime": "retired", "reason": "service-retired"}, False),
        ({"stage": "accepted-local", "runtime": "starting"}, False),
        ({"stage": "accepted-local", "runtime": "unknown"}, False),
    ]
    with patch.object(bridge, "_record_stop"):
        for result, expected in cases:
            assert bridge._observe_stop(result) is expected


def test_raw_stop_failure_preserves_safe_diagnostics_and_capture_state(tmp_path):
    secret = "PRIVATE_SENTINEL_transcript_or_credential"
    log = tmp_path / "diagnostics" / "events" / "mindie-agent-cc" / (
        "123-" + "a" * 32 + ".jsonl")
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps({
        "event": "operation.end", "status": "error", "operation_id": "b" * 32,
        "operation": "hook", "attributes": {
            "stage": "helper_failed", "category": "child_failure",
            "error_type": "TimeoutError", "exception_message": secret,
        },
    }) + "\n", encoding="utf-8")
    database = store_file(tmp_path)
    database.parent.mkdir(parents=True)
    with closing(sqlite3.connect(database)) as db:
        db.execute("CREATE TABLE captures(status TEXT, body TEXT)")
        db.execute("INSERT INTO captures VALUES (?, ?)", ("queued", secret))
        db.commit()
    result = subprocess.CompletedProcess(
        [], 0, json.dumps({"systemMessage": secret}).encode(),
        ("TimeoutError: " + secret).encode(),
    )
    env = {
        "MINDIE_CC_CONFIG": str(tmp_path / "cc.json"),
        "MINDIE_DIAGNOSTICS_ROOT": str(tmp_path / "diagnostics"),
        "MINDIE_DIAGNOSTICS_CONFIG": str(tmp_path / "diagnostics.json"),
    }
    with patch.object(subprocess, "run", return_value=result):
        with pytest.raises(pytest.fail.Exception) as failed:
            raw_stop({"private_input": secret}, env)
    message = str(failed.value)
    assert secret not in message
    details = json.loads(message)
    assert details["returncode"] == 0
    assert details["elapsed_seconds"] >= 0
    assert details["stderr"]["markers"] == ["TimeoutError"]
    assert details["diagnostics"]["recent"] == [{
        "operation": "hook", "stage": "helper_failed",
        "category": "child_failure", "error_type": "TimeoutError",
    }]
    assert details["captures"]["total"] == details["captures"]["states"]["queued"] == 1
    assert str(tmp_path) not in message
