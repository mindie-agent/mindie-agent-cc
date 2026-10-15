"""Fixed release bindings retain shared state and hand off only authenticated owners."""
import json
import hashlib
import os
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

from support import SCRIPTS
sys.path.insert(0, str(SCRIPTS))
import setup


@pytest.fixture
def preparing(tmp_path):
    from contextlib import ExitStack
    config = tmp_path / "profile" / "cc.json"
    root = tmp_path / "data"
    validation = {"schema": "mindie-candidate-check/1", "publication": {"domain": "fixture"}}
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"MINDIE_CC_CONFIG": str(config)}))
        stack.enter_context(patch("bootstrap.runtime_key", return_value="release-a"))
        stack.enter_context(patch.object(setup, "validate_candidate", return_value=validation))
        stack.enter_context(patch("candidate_validate.validate", return_value=validation))
        stack.enter_context(patch.object(setup, "identity", return_value={}))
        stack.enter_context(patch("capture_config.prepare", return_value={
            "redactor_executable": str(tmp_path / "redactor"),
            "summary_command": [sys.executable, str(SCRIPTS / "organizer.py")],
        }))
        prepare = stack.enter_context(patch("capture_config.prepare_store"))
        compatible = stack.enter_context(patch("state_compatibility.check"))
        stack.enter_context(patch("legacy_update.migrate", return_value={"status": "not-needed"}))
        stack.enter_context(patch.object(setup, "write_community"))
        stack.enter_context(patch("consent.migrate_consent", return_value={"state": "missing"}))
        stack.enter_context(patch.object(setup, "service_launch_context",
                                        side_effect=lambda path, *args, **kwargs: {
                                            "paths": {"MINDIE_CC_CONFIG": str(path)},
                                            "environment": [],
                                        }))
        stack.enter_context(patch("mindie_knowledge.loop.cli.config_at", side_effect=setup._read))
        live = stack.enter_context(patch("mindie_knowledge.loop.client_contract.connect_ready", return_value=None))
        resolve = stack.enter_context(patch("mindie_knowledge.loop.client_contract.resolve_config",
                                            side_effect=lambda path: (Path(path), setup._read(path))))
        retire = stack.enter_context(patch("service_handoff.stop", return_value={"idle": True}))
        yield dict(config=config, root=root, live=live, resolve=resolve, retire=retire,
                   prepare=prepare, compatible=compatible, validation=validation)


def install(f, version="0.0.1", **kwargs):
    f["validation"].update(native_version=version,
                           source_sha256=hashlib.sha256(version.encode()).hexdigest())
    return setup.prepare_binding(SCRIPTS.parent, f["config"], root=f["root"],
                                 domain="fixture", no_public_feed=True, **kwargs)


def test_cold_setup_publishes_fixed_binding_and_no_code_selector(preparing):
    f = preparing
    result = install(f)
    base = setup._read(f["config"])
    bound = setup._read(result["config"])
    assert base == bound
    assert base["python"] == sys.executable
    assert Path(bound["engine_config"]).parent.name == "release-a"
    assert not (Path(base["state_dir"]) / "update" / "current.json").exists()
    assert result["status"] == "ready"
    f["retire"].assert_not_called()


def test_native_new_release_uses_authenticated_owner_without_rewriting_anchor(preparing):
    f = preparing
    first = install(f)
    old = setup._read(first["config"])["engine_config"]
    original = f["config"].read_bytes()
    f["live"].return_value = {"config_path": old}
    with patch("bootstrap.runtime_key", return_value="release-b"):
        new = install(f, "0.0.2")
    f["retire"].assert_called_once_with(old, successor_config_path=setup._read(new["config"])["engine_config"])
    assert f["config"].read_bytes() == original
    f["compatible"].assert_called_once()


def test_cold_handoff_follows_only_recorded_anchor_successor(preparing):
    f = preparing
    first = install(f)
    old = Path(setup._read(first["config"])["engine_config"])
    successor = old.parent.parent / "recorded" / "cc.engine.json"
    successor.parent.mkdir()
    successor.write_bytes(old.read_bytes())
    f["resolve"].side_effect = lambda path: (successor if Path(path) == old else Path(path), {})
    before = f["config"].read_bytes()
    with patch("bootstrap.runtime_key", return_value="release-b"):
        new = install(f, "0.0.2")
    f["retire"].assert_called_once_with(str(successor), successor_config_path=setup._read(new["config"])["engine_config"])
    assert f["config"].read_bytes() == before


def test_busy_release_can_publish_entry_but_clients_keep_the_anchor(preparing):
    from knowledge_service import client_engine_path
    f = preparing
    first = install(f)
    old = setup._read(first["config"])["engine_config"]
    f["live"].return_value = {"config_path": old}
    before = f["config"].read_bytes()
    f["retire"].return_value = {"idle": False, "service": "busy"}
    with patch("bootstrap.runtime_key", return_value="release-b"):
        result = install(f, "0.0.2")
    assert f["config"].read_bytes() == before
    assert result["status"] == "pending"
    assert client_engine_path(setup._read(result["config"])) == Path(old)
    entry = f["root"] / "plugin-data/releases/0.0.2/entry.py"
    setup.publish_entry(entry, result["config"])
    assert entry.is_file()
    f["retire"].assert_called_once()


def test_old_binding_with_successor_cannot_retire_new_owner_backwards(preparing):
    f = preparing
    first = install(f)
    old = Path(setup._read(first["config"])["engine_config"])
    successor = old.parent.parent / "new" / "cc.engine.json"
    successor.parent.mkdir()
    saved = setup._read(old)
    saved["product_validation"]["native_version"] = "0.0.2"
    successor.write_text(json.dumps(saved), encoding="utf-8")
    f["resolve"].side_effect = lambda path: (successor, {})
    f["live"].side_effect = AssertionError("retired binding must not select a live owner")
    result = install(f)
    assert result["config"] == first["config"]
    f["retire"].assert_not_called()


def test_compatibility_rejection_precedes_retirement_and_keeps_paid_state(preparing):
    f = preparing
    install(f)
    marker = f["root"] / "paid-unknown.json"
    marker.write_text('{"outcome":"unknown","usage":7}')
    before = marker.read_bytes(), f["config"].read_bytes()
    f["compatible"].side_effect = ValueError("state format incompatible")
    with patch("bootstrap.runtime_key", return_value="release-b"):
        with pytest.raises(ValueError, match="incompatible"):
            install(f)
    assert (marker.read_bytes(), f["config"].read_bytes()) == before
    f["retire"].assert_not_called()


def test_legacy_profile_migration_preserves_original_and_requires_session_reload(preparing):
    f = preparing
    first = install(f)
    oldfile = Path(setup._read(first["config"])["engine_config"])
    old = setup._read(oldfile)
    del old["client_contract"]
    oldfile.write_text(json.dumps(old))
    before = f["config"].read_bytes(), oldfile.read_bytes()
    with patch("bootstrap.runtime_key", return_value="release-b"):
        result = install(f, "0.0.2")
    assert result["legacy_session_reload_required"] is True
    f["retire"].assert_called_once_with(str(oldfile))
    preserved = list(oldfile.parent.parent.glob("legacy-profile-*.json"))
    assert len(preserved) == 1 and preserved[0].read_bytes() == before[0]
    assert oldfile.read_bytes() == before[1]
    assert setup._read(f["config"])["engine_config"] != str(oldfile)


def test_pending_v2_cannot_retire_v3_or_cold_start_itself_after_v3_handoff(preparing):
    from knowledge_service import client_engine_path
    f = preparing
    first = install(f)
    original = f["config"].read_bytes()
    old = Path(setup._read(first["config"])["engine_config"])
    f["live"].return_value = {"config_path": str(old)}
    f["retire"].return_value = {"idle": False, "service": "busy"}
    with patch("bootstrap.runtime_key", return_value="release-b"):
        second = install(f, "0.0.2")
    f["retire"].return_value = {"idle": True}
    with patch("bootstrap.runtime_key", return_value="release-c"):
        third = install(f, "0.0.3")
    current = Path(setup._read(third["config"])["engine_config"])
    f["resolve"].side_effect = lambda path: (current if Path(path) == old else Path(path), {})
    f["retire"].reset_mock()
    f["live"].return_value = None
    late = setup.advance_handoff(second["config"])
    assert late == {"status": "superseded", "config": str(current)}
    assert client_engine_path(setup._read(second["config"])) == current
    assert client_engine_path(setup._read(first["config"])) == current
    assert f["config"].read_bytes() == original
    f["retire"].assert_not_called()


def test_uncertain_handoff_blocks_entry_without_replaying_retirement(preparing):
    f = preparing
    install(f)
    before = f["config"].read_bytes()
    f["retire"].return_value = {"idle": True, "cleanup_failed": True}
    with patch("bootstrap.runtime_key", return_value="release-b"):
        with pytest.raises(RuntimeError, match="uncertain"):
            install(f, "0.0.2")
    assert f["config"].read_bytes() == before
    f["retire"].assert_called_once()


def test_same_native_version_cannot_handoff_to_different_source_bytes(preparing):
    f = preparing
    first = install(f)
    old = Path(setup._read(first["config"])["engine_config"])
    engine = setup._read(old)
    engine["product_validation"]["source_sha256"] = "other-source"
    old.write_text(json.dumps(engine), encoding="utf-8")
    with patch("bootstrap.runtime_key", return_value="release-b"):
        with pytest.raises(ValueError, match="different source bytes"):
            install(f)
    f["retire"].assert_not_called()


def test_cold_mcp_prepares_runtime_binding_without_advancing_owner(preparing):
    from knowledge_service import client_engine_path
    f = preparing
    first = install(f)
    old = Path(setup._read(first["config"])["engine_config"])
    with patch("bootstrap.runtime_key", return_value="release-b"):
        second = install(f, "0.0.2", advance=False)
    assert second["status"] == "pending"
    assert client_engine_path(setup._read(second["config"])) == old
    f["retire"].assert_not_called()
    assert setup.advance_handoff(second["config"])["status"] == "ready"
    f["retire"].assert_called_once()


def test_legacy_cold_mcp_refuses_before_any_migration_or_owner_mutation(preparing):
    f = preparing
    first = install(f)
    engine_file = Path(setup._read(first["config"])["engine_config"])
    engine = setup._read(engine_file)
    del engine["client_contract"]
    engine_file.write_text(json.dumps(engine), encoding="utf-8")
    before = f["config"].read_bytes(), engine_file.read_bytes()
    with patch("bootstrap.runtime_key", return_value="release-b"), \
            patch("legacy_update.migrate") as migrate:
        with pytest.raises(RuntimeError, match="requires native SessionStart"):
            install(f, "0.0.2", advance=False)
    migrate.assert_not_called()
    f["retire"].assert_not_called()
    assert (f["config"].read_bytes(), engine_file.read_bytes()) == before
    assert not (engine_file.parent.parent / "release-b").exists()
