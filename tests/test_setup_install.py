"""One installation profile selects complete helpers without changing user data."""
import json
import os
from pathlib import Path
import sys
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from support import SCRIPTS
sys.path.insert(0, str(SCRIPTS))
import setup
from install_state import InstallationUnavailable, installed_binding


@pytest.fixture
def preparing(tmp_path):
    config = tmp_path / "profile" / "cc.json"
    root = tmp_path / "data"
    validation = {"schema": "mindie-candidate-check/1", "publication": {"domain": "fixture"}}
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"MINDIE_CC_CONFIG": str(config)}))
        stack.enter_context(patch("bootstrap.runtime_key", return_value="release-a"))
        stack.enter_context(patch.object(setup, "validate_candidate", return_value=validation))
        stack.enter_context(patch("candidate_validate.validate", return_value=validation))
        stack.enter_context(patch.object(setup, "identity", return_value={}))
        stack.enter_context(patch("harness.require_engine_support", return_value="2.1.269"))
        stack.enter_context(patch("release_update.disable_unfiltered_updates"))
        stack.enter_context(patch("capture_config.prepare", return_value={
            "redactor_executable": str(tmp_path / "redactor"),
            "summary_command": [sys.executable, str(SCRIPTS / "organizer.py")],
        }))
        prepare = stack.enter_context(patch("capture_config.prepare_store"))
        stack.enter_context(patch("mindie_knowledge.loop.lifecycle.inspect_retirement",
                                  return_value=None))
        community = stack.enter_context(patch.object(setup, "write_community"))
        stack.enter_context(patch.object(setup, "service_launch_context",
                                        side_effect=lambda path, *args, **kwargs: {
                                            "paths": {"MINDIE_CC_CONFIG": str(path),
                                                      "CLAUDE_CONFIG_DIR": str(tmp_path / "claude")},
                                            "environment": [],
                                        }))
        yield dict(config=config, root=root, prepare=prepare, community=community)


def install(f):
    return setup.prepare_binding(SCRIPTS.parent, f["config"], root=f["root"],
                                 domain="fixture", no_public_feed=True)


def test_cold_setup_publishes_fixed_binding_without_an_update_selector(preparing):
    f = preparing
    result = install(f)
    base = setup._read(f["config"])
    bound = setup._read(result["config"])
    assert base == bound
    assert base["python"] == sys.executable
    assert Path(bound["engine_config"]).parent.name == "release-a"
    assert "client_contract" not in setup._read(bound["engine_config"])
    assert not (Path(base["state_dir"]) / "update" / "current.json").exists()
    assert result["status"] == "ready"


def test_same_binding_recheck_does_not_rewrite_or_repeat_store_preparation(preparing):
    f = preparing
    result = install(f)
    paths = [f["config"], Path(result["config"]), Path(setup._read(result["config"])["engine_config"])]
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
    assert install(f) == result
    assert [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths] == before
    f["prepare"].assert_called_once()
    f["community"].assert_called_once()


def test_different_binding_is_prepared_without_changing_current_or_retiring(preparing):
    f = preparing
    result = install(f)
    marker = f["root"] / "paid-unknown.json"
    marker.write_text('{"outcome":"unknown","usage":7}', encoding="utf-8")
    before = marker.read_bytes(), f["config"].read_bytes(), Path(result["config"]).read_bytes()
    f["prepare"].reset_mock()
    with patch("bootstrap.runtime_key", return_value="other-unpublished-binding"), \
            patch("service_handoff.stop") as retire:
        candidate = install(f)
    assert candidate["status"] == "prepared"
    assert (marker.read_bytes(), f["config"].read_bytes(), Path(result["config"]).read_bytes()) == before
    assert (f["root"] / "adapter-state/installations/other-unpublished-binding").exists()
    retire.assert_not_called()
    f["prepare"].assert_not_called()


def test_unsupported_development_profile_is_rejected_before_lock_creation(preparing):
    f = preparing
    f["config"].parent.mkdir(parents=True)
    original = '{"engine_config":"old.json","paid":"unknown"}\n'
    f["config"].write_text(original, encoding="utf-8")
    with pytest.raises(InstallationUnavailable, match="unsupported installation"):
        install(f)
    assert f["config"].read_text(encoding="utf-8") == original
    assert not f["root"].exists()


def test_selected_binding_change_redirects_an_existing_fixed_facade(preparing):
    f = preparing
    result = install(f)
    fixed = setup._read(result["config"])
    with installed_binding(fixed):
        pass
    new_file = f["root"] / "other.adapter.json"
    selected = dict(fixed, engine_config=str(f["root"] / "other.engine.json"),
                    adapter_config=str(new_file))
    new_file.write_text(json.dumps(selected), encoding="utf-8")
    f["config"].write_text(json.dumps(selected), encoding="utf-8")
    before = f["config"].read_bytes()
    with installed_binding(fixed) as observed:
        assert observed == selected
    assert f["config"].read_bytes() == before
    assert setup._read(result["config"]) == fixed


def new_candidate(f):
    original = install(f)
    with patch("bootstrap.runtime_key", return_value="release-b"):
        candidate = install(f)
    return original, candidate


def test_busy_owner_keeps_selected_binding_and_choices(preparing):
    f = preparing
    original, candidate = new_candidate(f)
    before = f["config"].read_bytes()
    with patch("mindie_knowledge.loop.lifecycle.retire_service",
               return_value={"idle": False, "service": "busy"}) as retirement, \
            patch("mindie_knowledge.loop.cli.prepare_service") as start:
        result = setup.activate_binding(candidate["config"])
    assert result["status"] == "deferred"
    assert f["config"].read_bytes() == before
    retirement.assert_called_once()
    start.assert_not_called()


def test_ready_candidate_is_selected_only_after_exact_idle_retirement(preparing):
    f = preparing
    original, candidate = new_candidate(f)
    with patch("mindie_knowledge.loop.lifecycle.retire_service",
               return_value={"idle": True, "service": "stopped"}), \
            patch("mindie_knowledge.loop.cli.prepare_service") as start, \
            patch("mindie_knowledge.loop.handoff._probe", side_effect=["unknown", "absent", "ready"]), \
            patch("mindie_knowledge.loop.cli.connect"):
        result = setup.activate_binding(candidate["config"])
    assert result["status"] == "activated"
    assert setup._read(f["config"]) == setup._read(candidate["config"])
    start.assert_called_once()
    assert setup._read(original["config"]) != setup._read(f["config"])


def test_ready_after_write_failure_is_adopted_without_second_start(preparing):
    f = preparing
    original, candidate = new_candidate(f)
    before = f["config"].read_bytes()
    with patch("mindie_knowledge.loop.lifecycle.retire_service",
               return_value={"idle": True, "service": "absent"}) as retirement, \
            patch("mindie_knowledge.loop.cli.prepare_service") as start, \
            patch("mindie_knowledge.loop.lifecycle.inspect_retirement",
                  return_value={"status": "retired"}), \
            patch("mindie_knowledge.loop.handoff._probe", return_value="ready"), \
            patch("mindie_knowledge.loop.cli.connect"):
        with patch.object(setup, "atomic_write", side_effect=OSError("disk")):
            failure = setup.activate_binding(candidate["config"])
        assert failure["stage"] == "canonical_publish"
        assert failure["service"] == "ready"
        assert f["config"].read_bytes() == before
        result = setup.activate_binding(candidate["config"])
    assert result["status"] == "activated"
    start.assert_not_called()
    retirement.assert_not_called()


def test_readiness_window_never_kills_or_restarts_an_unconfirmed_owner(preparing):
    f = preparing
    original, candidate = new_candidate(f)
    before = f["config"].read_bytes()
    with patch("mindie_knowledge.loop.lifecycle.retire_service",
               return_value={"idle": True, "service": "stopped"}), \
            patch("mindie_knowledge.loop.cli.prepare_service") as start, \
            patch("mindie_knowledge.loop.handoff._probe", return_value="starting"):
        result = setup.activate_binding(candidate["config"], observation_seconds=0)
    assert result["status"] == "incomplete"
    assert result["stage"] == "new_readiness"
    assert f["config"].read_bytes() == before
    start.assert_not_called()


def test_retired_marker_does_not_bypass_incomplete_old_cleanup(preparing):
    f = preparing
    _, candidate = new_candidate(f)
    before = f["config"].read_bytes()
    with patch("mindie_knowledge.loop.lifecycle.inspect_retirement",
               return_value={"status": "retired"}), \
            patch("mindie_knowledge.loop.handoff._probe", return_value="absent"), \
            patch("mindie_knowledge.loop.lifecycle.retire_service",
                  return_value={"idle": True, "cleanup_failed": True}) as retire, \
            patch("mindie_knowledge.loop.cli.prepare_service") as start:
        result = setup.activate_binding(candidate["config"])
    assert result["stage"] == "old_retirement"
    assert result["retirement"]["cleanup_failed"] is True
    assert f["config"].read_bytes() == before
    retire.assert_called_once()
    start.assert_not_called()


def test_all_candidate_probes_receive_positive_remaining_observation_budget(preparing):
    f = preparing
    _, candidate = new_candidate(f)
    with patch("mindie_knowledge.loop.lifecycle.retire_service",
               return_value={"idle": True, "service": "stopped"}), \
            patch("mindie_knowledge.loop.cli.prepare_service") as start, \
            patch("mindie_knowledge.loop.handoff._probe",
                  side_effect=["unknown", "absent", "ready"]) as probe, \
            patch("mindie_knowledge.loop.cli.connect"):
        result = setup.activate_binding(candidate["config"], observation_seconds=3)
    assert result["status"] == "activated"
    for call in probe.call_args_list:
        assert 0 < call.args[1] <= 3
    assert 0 < start.call_args.kwargs["probe_timeout"] <= 3


def test_active_shared_operation_defers_activation_without_retirement(preparing):
    f = preparing
    original, candidate = new_candidate(f)
    with installed_binding(setup._read(original["config"])), \
            patch("mindie_knowledge.loop.lifecycle.retire_service") as retirement:
        result = setup.activate_binding(candidate["config"])
    assert result == {"status": "deferred", "stage": "active_operation", "switched": False}
    retirement.assert_not_called()


def test_default_coinstallation_preserves_shared_authority_bytes(tmp_path):
    from mindie_knowledge import consent_store
    from mindie_knowledge.loop.settings import CommunityWriteContext

    consent = tmp_path / "mindie-consent.json"
    community = tmp_path / "mindie-community.json"
    consent_store.record_choice(consent, "contribute")
    consent_store.record_reporting(consent, "disabled")
    with CommunityWriteContext(community) as context:
        context.configure(community, {
            "enabled": True,
            "repository": "owner/repo",
            "project_roots": [str(tmp_path)],
            "consent_config": str(consent),
        })
    before = community.read_bytes(), consent.read_bytes()
    assert setup.write_community(community, None, python=sys.executable) == "enabled"
    assert (community.read_bytes(), consent.read_bytes()) == before
