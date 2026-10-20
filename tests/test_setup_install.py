"""One installation profile selects complete helpers without changing user data."""
import json
import os
from pathlib import Path
import sys
import subprocess
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from support import SCRIPTS
sys.path.insert(0, str(SCRIPTS))
import setup
from install_state import InstallationUnavailable, OperationLock, installed_binding


@pytest.fixture
def preparing(tmp_path):
    config = tmp_path / "profile" / "cc.json"
    root = tmp_path / "data"
    validation = {
        "schema": "mindie-candidate-check/1",
        "publication": {
            "repository": "mindie-agent/knowledge-vllm-ascend",
            "ref": "main",
            "domain": "fixture",
            "verified_commit": "a" * 40,
        },
    }
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
                                                      "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"),
                                                      "MINDIE_DIAGNOSTICS_CONFIG": str(config.parent / "diagnostics.json"),
                                                      "MINDIE_DIAGNOSTICS_ROOT": str(tmp_path / "diagnostics")},
                                            "environment": [],
                                        }))
        yield dict(config=config, root=root, prepare=prepare, community=community,
                   validation=validation)


def install(f):
    return setup.prepare_binding(SCRIPTS.parent, f["config"], root=f["root"],
                                 domain="fixture", no_public_feed=True)


def install_graded(f, *, runtime=None, domain="vllm-ascend", no_public_feed=False):
    with patch("bootstrap.runtime_key", return_value=runtime or "release-a"):
        return setup.prepare_binding(
            SCRIPTS.parent, f["config"], root=f["root"], domain=domain,
            no_public_feed=no_public_feed)


OFFICIAL_ROWS = [
    dict(repository="mindie-agent/knowledge-vllm-ascend", ref="main",
         domain="vllm-ascend", interval_seconds=300),
    dict(repository="mindie-agent/knowledge-operators", ref="main",
         domain="operators", interval_seconds=300),
]


def install_official(f, *, runtime="release-a", domain="vllm-ascend",
                     no_public_feed=False):
    with patch("bootstrap.runtime_key", return_value=runtime):
        return setup.prepare_binding(
            SCRIPTS.parent, f["config"], root=f["root"], domain=domain,
            no_public_feed=no_public_feed)


def use_publication(f, publication):
    f["validation"].clear()
    f["validation"].update(schema="mindie-candidate-check/1",
                           publication=publication)


def test_fresh_install_writes_two_official_main_feeds(preparing):
    from mindie_knowledge import consent_store

    f = preparing
    consent = f["config"].with_name("mindie-consent.json")
    consent_store.record_choice(consent, "disabled")
    before = consent.read_bytes()
    use_publication(f, dict(repository="mindie-agent/knowledge-vllm-ascend",
                            ref="main", domain="vllm-ascend",
                            verified_commit="1" * 40))
    result = install_official(f)
    binding = setup._read(result["config"])
    engine = setup._read(binding["engine_config"])
    assert engine["feeds"] == [OFFICIAL_ROWS[0]]
    assert "contract_sha256" not in engine["feeds"][0]
    operators = setup._read(binding["domain_configs"]["operators"])
    assert operators["feeds"] == [OFFICIAL_ROWS[1]]
    settings = setup._read(operators["community_config"])
    assert settings["branch"] == "main"
    assert settings["repository"] == "mindie-agent/knowledge-operators"
    assert settings["enabled"] is False
    assert settings["consent_config"] == str(consent)
    assert consent.read_bytes() == before


def test_custom_root_ref_and_route_survive_update(preparing):
    from mindie_knowledge import consent_store

    f = preparing
    use_publication(f, dict(repository="mindie-agent/knowledge-vllm-ascend",
                            ref="main", domain="vllm-ascend",
                            verified_commit="1" * 40))
    installed = install_official(f, runtime="release-a")
    current = setup._read(installed["config"])
    engine_path = Path(current["engine_config"])
    operators_path = Path(current["domain_configs"]["operators"])
    engine = setup._read(engine_path)
    operators = setup._read(operators_path)
    custom_root = dict(repository="owner/custom-vllm", ref="topic",
                       domain="vllm-ascend", interval_seconds=300)
    custom_operators = dict(repository="owner/custom-operators", ref="review",
                            domain="operators", interval_seconds=300)
    engine["feeds"] = [custom_root]
    operators["feeds"] = [custom_operators]
    engine_path.write_text(json.dumps(engine), encoding="utf-8")
    operators_path.write_text(json.dumps(operators), encoding="utf-8")
    settings_path = Path(operators["community_config"])
    settings = setup._read(settings_path)
    settings.update(repository=custom_operators["repository"],
                    branch=custom_operators["ref"])
    settings_path.write_text(json.dumps(settings), encoding="utf-8")
    consent = f["config"].with_name("mindie-consent.json")
    consent_store.record_choice(consent, "contribute")
    before = consent.read_bytes()
    settings_before = settings_path.read_bytes()

    upgraded = install_official(f, runtime="release-b")
    upgraded_binding = setup._read(upgraded["config"])
    upgraded_engine = setup._read(upgraded_binding["engine_config"])
    assert upgraded_engine["feeds"] == [custom_root]
    upgraded_operators = setup._read(
        upgraded_binding["domain_configs"]["operators"])
    assert upgraded_operators["feeds"] == [custom_operators]
    assert settings_path.read_bytes() == settings_before
    assert consent.read_bytes() == before


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


def test_ready_mcp_preparation_does_not_wait_for_session_start_maintenance(preparing):
    result = install(preparing)
    binding = setup._read(result["config"])
    exclusive = OperationLock.exclusive
    # SessionStart legitimately keeps a shared binding during maintenance.
    # Use an immediate writer budget so a regressed lock upgrade fails quickly.
    with installed_binding(binding), patch.object(
        OperationLock, "exclusive",
        lambda lock, timeout=30: exclusive(lock, timeout=0),
    ):
        assert install(preparing) == result


def test_parallel_cold_native_entries_share_one_complete_store(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("cold")
    command = [sys.executable, str(Path(__file__).parent / "fixtures/concurrent_setup.py"),
               str(SCRIPTS), str(tmp_path)]
    processes = []
    try:
        first = subprocess.Popen(command + ["first"], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        processes.append(first)
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert pool.submit(first.stdout.readline).result(10) == "started\n"
            assert pool.submit(first.stdout.readline).result(10) == "state-open-before-layout\n"
            second = subprocess.Popen(command + ["second"], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            processes.append(second)
            assert pool.submit(second.stdout.readline).result(10) == "started\n"
            waiting = pool.submit(second.communicate, timeout=10)
            try:
                # A contender must wait for layout publication rather than
                # diagnose the first process's in-progress DB as broken state.
                with pytest.raises(FutureTimeout):
                    waiting.result(0.3)
            finally:
                output, errors = first.communicate("continue\n", timeout=10)
            other_output, other_errors = waiting.result(10)
        assert first.returncode == 0, errors
        assert second.returncode == 0, other_errors
        assert json.loads(output) == json.loads(other_output)
        from mindie_knowledge.loop.store import Store
        store = Store(tmp_path / "data", "fixture")
        store.close()
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


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


def test_domain_registration_after_prepare_defers_and_can_be_reprepared(preparing):
    f = preparing
    original, candidate = new_candidate(f)
    previous = setup._read(f['config'])
    custom = f['root'] / 'custom.engine.json'
    engine = setup._read(previous['engine_config'])
    custom.write_text(json.dumps({**engine, 'domain': 'custom'}))
    changed = {**previous, 'domain_configs': {**previous.get('domain_configs', {}), 'custom': str(custom)}}
    setup.atomic_write(f['config'], changed)
    result = setup.activate_binding(candidate['config'])
    assert result['status'] == 'deferred' and result['stage'] == 'domain_routes_changed'
    assert setup._read(f['config']) == changed
    with patch('bootstrap.runtime_key', return_value='release-b'):
        refreshed = install(f)
    value = setup._read(refreshed['config'])
    assert refreshed['config'] != candidate['config']
    assert value['domain_configs']['custom'] == str(custom)
    assert value['domain_source_configs'] == changed['domain_configs']


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
