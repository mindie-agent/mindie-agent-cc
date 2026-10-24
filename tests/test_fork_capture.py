"""Native fork metadata mapped to the existing Admission and capture queue."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest

import maintenance
import transcript
from mindie_knowledge import consent_store
from mindie_knowledge.loop.activation import Admission
from test_stop_cutover import capture_fixture, capture_rows, raw_stop
from support import store_file


def native_record(session, kind, stamp, *, cwd=None, text=None, **extra):
    value = dict(type=kind, sessionId=session, timestamp=stamp, **extra)
    if cwd is not None:
        value["cwd"] = str(cwd)
    if text is not None:
        value["message"] = dict(role=kind, content=text)
    return value


def write_records(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def completed_diagnostics(command, *args, receipt=None, **kwargs):
    if receipt is not None:
        receipt["returncode"] = 0
        return '{"status":"ok"}'
    return '{"status":"disabled"}'


def fork_fixture(tmp_path, monkeypatch):
    canonical, binding, stop, env, marker, retired = capture_fixture(tmp_path, activate=False)
    monkeypatch.setenv("MINDIE_CC_CONFIG", str(canonical))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", binding["claude_config_dir"])
    path = Path(stop["transcript_path"])
    path.unlink()
    event = dict(hook_event_name="SessionStart", source="fork",
                 session_id=stop["session_id"], transcript_path=str(path),
                 cwd=str(tmp_path / "project" / "child"))
    engine = json.loads(Path(binding["engine_config"]).read_text(encoding="utf-8"))
    return event, stop, env, marker, path, Admission(engine["admission_path"])


def test_fork_start_excludes_large_inherited_history_and_preserves_resume(tmp_path, monkeypatch):
    event, stop, env, marker, path, admission = fork_fixture(tmp_path, monkeypatch)
    session = event["session_id"]
    with patch("mindie_knowledge.loop.activation.time.time", return_value=100):
        admission.activate(session, project_root=event["cwd"])
    original = admission.check(session)
    rows = [
        native_record(session, "queue-operation", 101, operation="enqueue"),
        native_record(session, "user", 50, cwd=tmp_path / "parent-outside",
                      text="PARENT_SENTINEL"),
        *[native_record(session, "attachment", 50, cwd=tmp_path / "parent-outside")
          for _ in range(100)],
        native_record(session, "assistant", 51, cwd=tmp_path / "parent-outside",
                      text="PARENT_PRIVATE " * 100000),
        native_record(session, "user", 102, cwd=event["cwd"], text="CHILD_SENTINEL"),
    ]
    write_records(path, rows)
    assert path.stat().st_size > transcript.SOURCE_HEAD_BYTES
    raw_stop(stop, env)
    assert len(capture_rows(tmp_path)) == 1
    with closing(sqlite3.connect(store_file(tmp_path))) as db:
        boundary, scope = db.execute("SELECT boundary,scope FROM captures").fetchone()
    assert boundary == 100
    assert scope == event["cwd"]
    material = transcript.read_material(str(path), 0, session_id=session, not_before=boundary)
    assert "CHILD_SENTINEL" in material["text"]
    assert "PARENT_" not in material["text"]

    # Native resume and compaction append; neither moves the first queue or
    # the established scope/boundary as own history grows past the head.
    for number in range(2):
        rows.extend([
            native_record(session, "queue-operation", 200 + number, operation="enqueue"),
            native_record(session, "system", 200 + number, subtype="compact_boundary"),
            native_record(session, "user", 200 + number, cwd=event["cwd"],
                          text=f"RESUME_{number}"),
        ])
        write_records(path, rows)
        assert admission.knowledge_lease(session)["token"] == original["token"]
        raw_stop(dict(stop, prompt_id=f"resume-{number}"), env)
        assert admission.check(session) == original
    assert len(capture_rows(tmp_path)) == 3
    assert not marker.exists()


def test_first_queue_separates_scope_from_rewritten_inherited_session_ids(tmp_path, monkeypatch):
    event, stop, _, _, path, _ = fork_fixture(tmp_path, monkeypatch)
    session = stop["session_id"]
    rows = [
        native_record(session, "queue-operation", 100, operation="enqueue"),
        native_record(session, "user", 50, cwd=tmp_path / "parent",
                      text="PARENT_SENTINEL"),
        native_record(session, "user", 101, cwd=event["cwd"], text="CHILD_SENTINEL"),
        native_record(session, "queue-operation", 200, operation="enqueue"),
        native_record(session, "user", 201, cwd=event["cwd"], text="RESUME_SENTINEL"),
    ]
    write_records(path, rows)
    source = transcript.capture_source(str(path), session)
    assert source["created_at"] == 100
    assert source["project_root"] == event["cwd"]
    material = transcript.read_material(str(path), 0, session_id=session,
                                        not_before=source["created_at"])
    assert "PARENT_SENTINEL" not in material["text"]
    assert "CHILD_SENTINEL" in material["text"]
    assert "RESUME_SENTINEL" in material["text"]


@pytest.mark.parametrize("case", ["missing-queue", "later-queue", "foreign-queue",
                                  "foreign-path"])
def test_unprovable_source_warns_without_parent_capture(tmp_path, monkeypatch, case):
    event, stop, env, marker, path, admission = fork_fixture(tmp_path, monkeypatch)
    session = event["session_id"]
    with patch("mindie_knowledge.loop.activation.time.time", return_value=100):
        admission.activate(session, project_root=event["cwd"])
    parent = native_record(session, "user", 50, cwd=tmp_path / "project",
                           text="PARENT_SENTINEL")
    queue = native_record(session, "queue-operation", 100, operation="enqueue")
    if case == "missing-queue":
        rows = [parent]
    elif case == "later-queue":
        rows = [parent, queue]
    elif case == "foreign-queue":
        rows = [dict(queue, sessionId="ffffffff-ffff-ffff-ffff-ffffffffffff"), parent]
    else:
        rows = [queue, parent]
    write_records(path, rows)
    if case == "foreign-path":
        foreign = tmp_path / path.name
        foreign.write_bytes(path.read_bytes())
        stop["transcript_path"] = str(foreign)
    raw_stop(stop, env, failed=True)
    assert capture_rows(tmp_path) == []
    assert admission.knowledge_lease(session)
    assert not marker.exists()


@pytest.mark.parametrize("case", ["off", "out-of-scope", "revoked"])
def test_fork_start_respects_current_choice_scope_and_explicit_revocation(tmp_path, monkeypatch, case):
    event, _, _, _, _, admission = fork_fixture(tmp_path, monkeypatch)
    if case == "off":
        consent_store.record_choice(tmp_path / "mindie-consent.json", "disabled")
    elif case == "out-of-scope":
        event["cwd"] = str(tmp_path / "outside")
    else:
        admission.associate(event["session_id"], project_root=event["cwd"], not_before=10)
        admission.deactivate(event["session_id"])
    before = admission.inspect(event["session_id"])
    with patch.object(maintenance, "run", side_effect=completed_diagnostics):
        assert maintenance.session_start(event)["feed"]["result"] == "inactive"
    assert admission.inspect(event["session_id"]) == before


def test_passive_fork_does_not_run_knowledge_maintenance(tmp_path, monkeypatch):
    event, _, _, _, _, admission = fork_fixture(tmp_path, monkeypatch)
    with patch.object(maintenance, "sync_public_feed") as sync, \
            patch.object(maintenance, "run", side_effect=completed_diagnostics) as run:
        result = maintenance.session_start(event)
        assert result["status"] == "ready"
        assert result["feed"]["result"] == "inactive"
        sync.assert_not_called()
        assert run.call_count == 2
        assert run.call_args_list[0].args[0][1].endswith("diagnostic_support.py")
        assert "mindie_diagnostics.cli" in run.call_args_list[1].args[0]
    assert admission.knowledge_lease(event["session_id"]) is None


def test_active_fork_binding_still_requires_its_native_queue_and_profile(tmp_path, monkeypatch):
    event, stop, env, _, path, admission = fork_fixture(tmp_path, monkeypatch)
    admission.activate(event["session_id"], project_root=event["cwd"])
    original = admission.check(event["session_id"])
    write_records(path, [
        native_record("ffffffff-ffff-ffff-ffff-ffffffffffff",
                      "queue-operation", 100, operation="enqueue"),
    ])
    raw_stop(stop, env, failed=True)
    write_records(path, [
        native_record(event["session_id"], "queue-operation", 100, operation="enqueue"),
    ])
    foreign = tmp_path / path.name
    foreign.write_bytes(path.read_bytes())
    raw_stop(dict(stop, transcript_path=str(foreign)), env, failed=True)
    assert admission.check(event["session_id"]) == original
    assert capture_rows(tmp_path) == []


def test_later_maintenance_failure_keeps_successful_fork_binding(tmp_path, monkeypatch):
    event, _, _, _, _, admission = fork_fixture(tmp_path, monkeypatch)
    admission.activate(event["session_id"], project_root=event["cwd"])
    with patch.object(maintenance, "run", side_effect=completed_diagnostics), \
            patch.object(maintenance, "sync_public_feed",
                      side_effect=RuntimeError("synthetic feed failure")):
        with pytest.raises(RuntimeError, match="synthetic feed failure"):
            maintenance.session_start(event)
    original = admission.check(event["session_id"])
    with patch("diagnostic_support.reporting_config_path", return_value=tmp_path / "off.json"), \
            patch.object(maintenance, "sync_public_feed",
                         return_value={"result": "sync_failed", "error": "fixture"}), \
            patch.object(maintenance, "run", return_value='{"status":"ok"}'):
        result = maintenance.session_start(dict(event, source="resume"))
    assert result["status"] == "incomplete"
    assert admission.check(event["session_id"]) == original
