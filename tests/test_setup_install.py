"""First installation and refusal preserve unpublished profiles and paid facts."""
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
        stack.enter_context(patch("capture_config.prepare", return_value={
            "redactor_executable": str(tmp_path / "redactor"),
            "summary_command": [sys.executable, str(SCRIPTS / "organizer.py")],
        }))
        prepare = stack.enter_context(patch("capture_config.prepare_store"))
        compatible = stack.enter_context(patch("state_compatibility.check"))
        community = stack.enter_context(patch.object(setup, "write_community"))
        stack.enter_context(patch.object(setup, "service_launch_context",
                                        side_effect=lambda path, *args, **kwargs: {
                                            "paths": {"MINDIE_CC_CONFIG": str(path)},
                                            "environment": [],
                                        }))
        yield dict(config=config, root=root, prepare=prepare,
                   compatible=compatible, community=community)


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


def test_different_binding_refused_without_changes_or_retirement(preparing):
    f = preparing
    result = install(f)
    marker = f["root"] / "paid-unknown.json"
    marker.write_text('{"outcome":"unknown","usage":7}', encoding="utf-8")
    before = marker.read_bytes(), f["config"].read_bytes(), Path(result["config"]).read_bytes()
    f["prepare"].reset_mock()
    with patch("bootstrap.runtime_key", return_value="other-unpublished-binding"), \
            patch("service_handoff.stop") as retire:
        with pytest.raises(InstallationUnavailable, match="native_reload_required"):
            install(f)
    assert (marker.read_bytes(), f["config"].read_bytes(), Path(result["config"]).read_bytes()) == before
    assert not (f["root"] / "adapter-state/installations/other-unpublished-binding").exists()
    retire.assert_not_called()
    f["prepare"].assert_not_called()


def test_unsupported_development_profile_is_rejected_before_lock_creation(preparing):
    f = preparing
    f["config"].parent.mkdir(parents=True)
    original = '{"engine_config":"old.json","paid":"unknown"}\n'
    f["config"].write_text(original, encoding="utf-8")
    with pytest.raises(InstallationUnavailable, match="unsupported development"):
        install(f)
    assert f["config"].read_text(encoding="utf-8") == original
    assert not f["root"].exists()


def test_state_format_rejection_does_not_publish_installation(preparing):
    f = preparing
    f["root"].mkdir()
    marker = f["root"] / "paid-unknown.json"
    marker.write_text('{"outcome":"unknown","usage":7}', encoding="utf-8")
    before = marker.read_bytes()
    f["compatible"].side_effect = ValueError("state format incompatible")
    with pytest.raises(ValueError, match="incompatible"):
        install(f)
    assert marker.read_bytes() == before
    assert not f["config"].exists()
    f["prepare"].assert_not_called()


def test_selected_binding_change_refuses_an_existing_fixed_entry(preparing):
    f = preparing
    result = install(f)
    fixed = setup._read(result["config"])
    with installed_binding(fixed):
        pass
    selected = dict(fixed, engine_config=str(f["root"] / "other.engine.json"))
    f["config"].write_text(json.dumps(selected), encoding="utf-8")
    before = f["config"].read_bytes()
    with pytest.raises(InstallationUnavailable, match="native_reload_required"):
        with installed_binding(fixed):
            pytest.fail("stale entry was admitted")
    assert f["config"].read_bytes() == before
    assert setup._read(result["config"]) == fixed


def test_immutable_entry_does_not_replace_a_published_runtime(tmp_path):
    entry = tmp_path / "entry.py"
    setup.publish_entry(entry, tmp_path / "adapter.json")
    before = entry.read_bytes()
    with pytest.raises(ValueError, match="immutable"):
        setup.publish_entry(entry, tmp_path / "other.json")
    assert entry.read_bytes() == before


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
