"""Native installation facts are distinct from Release eligibility and activation."""

import copy
import json
import os
from pathlib import Path
import sys
import threading
from unittest.mock import patch

import pytest

from support import ROOT, SCRIPTS, make_config

sys.path.insert(0, str(SCRIPTS))
import harness
import release_update as update
from mindie_knowledge.releases import supports_harness


@pytest.fixture(autouse=True)
def no_independent_component_change(monkeypatch):
    # These tests own native plugin transactions. Component selection and its
    # effective runtime are exercised by test_component_update separately.
    import component_update
    from unittest.mock import Mock
    check = Mock(return_value={"status": "up_to_date"})
    monkeypatch.setattr(component_update, "check", check)
    return check


def release(version="0.0.2", harness_version=None):
    declaration = harness.compatibility(ROOT)
    if harness_version is not None:
        intervals = [{
            "minimum": harness_version, "maximum": harness_version,
            "maximum_inclusive": True, "include_prerelease": False,
        }]
        declaration["supported"] = intervals
        for interface in declaration["interface_registry"].values():
            interface["supported"] = copy.deepcopy(intervals)
    return {
        "id": int(version.rsplit(".", 1)[-1]), "tag_name": "v" + version,
        "draft": False, "prerelease": False,
        "metadata": {
            "schema": "mindie-plugin-release/1", "version": version,
            "source": {"repository": harness.REPOSITORY, "sha": "a" * 40},
            "compatibility": declaration,
        },
    }


def binding(tmp_path):
    path = make_config(tmp_path)
    value = json.loads(path.read_text())
    value["product_validation"] = {"native_version": "0.0.1"}
    value["claude_config_dir"] = str(tmp_path / "claude")
    path.write_text(json.dumps(value), encoding="utf-8")
    return value


def activation_result(status):
    def run(*args, receipt, **kwargs):
        receipt.update(started=True, completed=True, cleanup_failed=False, returncode=0)
        return json.dumps({"status": status})
    return run


def test_current_interface_registry_is_complete_and_bounded():
    declaration = harness.check_registry(ROOT, ROOT / "tests")
    for version in ("2.1.269", "2.1.299"):
        assert supports_harness(declaration, harness_name="claude-code", harness_version=version)
    for version in ("2.1.268", "2.1.300"):
        assert not supports_harness(declaration, harness_name="claude-code", harness_version=version)
    for entry in declaration["interface_registry"].values():
        for code in entry["code_paths"]:
            assert (ROOT / code.split(":")[0]).is_file()


def test_native_checkout_must_match_selected_sha_before_install(tmp_path):
    selected = release()
    saved = binding(tmp_path)
    state = {"candidate": selected}
    with patch.object(update, "_declare_ref"), \
            patch.object(update, "_mutation") as mutation, \
            patch.object(update, "installed_plugin", return_value={"enabled": True, "version": "0.0.1", "installPath": str(ROOT)}), \
            patch.object(update, "_marketplace", return_value={
                "source": "git", "url": f"https://github.com/{harness.REPOSITORY}.git",
                "ref": "v0.0.2", "installLocation": str(tmp_path / "catalog"),
            }), patch.object(update, "run", return_value="b" * 40):
        with pytest.raises(ValueError, match="immutable SHA"):
            update.install_selected(saved, "claude", selected, state)
    assert mutation.call_count == 1
    assert mutation.call_args.args[-1] == "catalog"


@pytest.mark.parametrize("process", [
    {"started": True, "cleanup_failed": True, "completed": True},
    {"started": True, "cleanup_failed": False},
    {},
])
def test_unknown_native_effect_is_not_replayed_even_if_target_matches(tmp_path, process):
    selected = release()
    state = {"candidate": selected, "native_effect": "unknown", "stage": "plugin",
             "process": process}
    with patch.object(update, "installed_plugin", return_value={"enabled": True, "version": "0.0.2", "installPath": str(ROOT)}) as read, \
            patch.object(update, "_mutation") as mutation:
        with pytest.raises(RuntimeError, match="not confirmed settled"):
            update.install_selected(binding(tmp_path), "claude", selected, state)
    read.assert_not_called()
    mutation.assert_not_called()
    assert state["native_effect"] == "unknown"


def test_settled_writer_with_old_native_target_is_not_replayed(tmp_path):
    selected = release()
    state = {"candidate": selected, "native_effect": "unknown", "stage": "plugin",
             "process": {"completed": True, "cleanup_failed": False}}
    with patch.object(update, "installed_plugin", return_value={"enabled": True, "version": "0.0.1", "installPath": str(ROOT)}), \
            patch.object(update, "_verify_catalog", return_value=tmp_path), \
            patch.object(update, "_mutation") as mutation:
        with pytest.raises(RuntimeError, match="unconfirmed"):
            update.install_selected(binding(tmp_path), "claude", selected, state)
    mutation.assert_not_called()


def test_harness_changed_after_interruption_does_not_activate_or_install(tmp_path):
    selected = release()
    saved = binding(tmp_path)
    state = {"candidate": selected, "native_effect": "unknown", "stage": "plugin",
             "process": {"completed": True, "cleanup_failed": False}}
    with patch.object(update, "read_receipt", return_value=state), \
            patch.object(update, "native_binary", return_value="claude"), \
            patch.object(update, "native_version", return_value="9.0.0"), \
            patch.object(update, "installed_plugin", return_value={"enabled": True, "version": "0.0.1", "installPath": str(ROOT)}), \
            patch("mindie_knowledge.releases_io.verify_release_source"), \
            patch.object(update, "install_selected") as install:
        result = update.check(saved)
    assert result["status"] == "incompatible_harness"
    assert result["native_effect"] == "unknown"
    install.assert_not_called()


def test_busy_activation_does_not_wait_an_hour_or_refetch_releases(tmp_path):
    selected = release()
    saved = binding(tmp_path)
    state = {"candidate": selected, "native_effect": "verified", "status": "deferred",
             "last_activation_attempt": 100, "stage": "activation"}
    with patch.object(update, "read_receipt", return_value=state):
        assert not update.due(saved, now=104)
        assert update.due(saved, now=105)

    def prepare_or_activate(argv, *, receipt, **kwargs):
        receipt.update(started=True, completed=True, cleanup_failed=False, returncode=0)
        if "--activate" in argv:
            return '{"status":"activated"}'
        return json.dumps({"config": str(tmp_path / "new.adapter.json")})

    with patch.object(update, "read_receipt", return_value=state), \
            patch.object(update, "native_binary", return_value="claude"), \
            patch.object(update, "native_version", return_value="2.1.269"), \
            patch.object(update, "installed_plugin", return_value={"enabled": True, "version": "0.0.1", "installPath": str(ROOT)}), \
            patch.object(update, "install_selected", return_value=tmp_path), \
            patch("bootstrap.ensure_runtime", return_value=(sys.executable, SCRIPTS)), \
            patch("mindie_knowledge.releases_io.fetch_releases") as fetch, \
            patch.object(update, "read_binding", return_value=saved), \
            patch.object(update, "_record"), \
            patch.object(update, "run", side_effect=prepare_or_activate):
        result = update.check(saved)
    assert result["status"] == "activated"
    fetch.assert_not_called()


def test_successful_check_refreshes_after_five_minutes(tmp_path):
    saved = binding(tmp_path)
    state = {"status": "up_to_date", "last_successful_check": 100}
    with patch.object(update, "read_receipt", return_value=state):
        assert not update.due(saved, now=100 + 299)
        assert update.due(saved, now=100 + 300)
        state["last_failed_check"] = 100 + 300
        assert not update.due(saved, now=100 + 359)
        assert update.due(saved, now=100 + 360)
    assert state["last_successful_check"] == 100


def test_successful_no_candidate_check_advances_success_clock(tmp_path):
    saved = binding(tmp_path)
    state = {"last_successful_check": 10, "last_failed_check": 20}
    with patch.object(update, "native_binary", return_value="claude"), \
            patch.object(update, "native_version", return_value="2.1.269"), \
            patch.object(update, "installed_plugin", return_value={"enabled": True, "version": "0.0.1", "installPath": str(ROOT)}), \
            patch("mindie_knowledge.releases_io.fetch_releases", return_value=[]), \
            patch.object(update.time, "time", return_value=100):
        result = update.check(saved, state)
    assert result["last_successful_check"] == 100
    assert "last_failed_check" not in result
    assert result["candidate"] is None


def test_failed_fetch_preserves_success_clock(tmp_path):
    saved = binding(tmp_path)
    state = {"last_successful_check": 10}
    with patch.object(update, "native_binary", return_value="claude"), \
            patch.object(update, "native_version", return_value="2.1.269"), \
            patch.object(update, "installed_plugin", return_value={"enabled": True, "version": "0.0.1", "installPath": str(ROOT)}), \
            patch("mindie_knowledge.releases_io.fetch_releases",
                  side_effect=OSError("offline")):
        with pytest.raises(OSError, match="offline"):
            update.check(saved, state)
    assert state == {"last_successful_check": 10}


def test_prior_completed_install_is_not_pending_after_later_fetch_failure(tmp_path):
    state = {
        "candidate": release(), "native_effect": "verified", "stage": "activation",
        "status": "failed", "installation_complete": True,
        "last_successful_check": 100, "last_failed_check": 100 + 300,
        "prepared": {"config": "old-completed-config"},
    }
    assert not update._pending_install(state)
    assert update.next_check_at(state) == 100 + 300 + 60
    with patch.object(update, "native_binary", return_value="claude"), \
            patch.object(update, "native_version", return_value="2.1.299"), \
            patch.object(update, "installed_plugin", return_value={"enabled": True, "version": "0.0.2", "installPath": str(ROOT)}), \
            patch("mindie_knowledge.releases_io.fetch_releases", return_value=[]) as fetch, \
            patch.object(update, "install_selected") as install:
        result = update.check(binding(tmp_path), state)
    assert result["candidate"] is None
    fetch.assert_called_once()
    install.assert_not_called()


def test_disabled_native_plugin_does_not_fetch_install_or_activate(tmp_path):
    saved = binding(tmp_path)
    row = dict(id=update.PLUGIN_QUALIFIED, version="0.0.1", scope="user",
               enabled=False, installPath=str(tmp_path / "native"))
    with patch.object(update, "native_binary", return_value="claude"), \
            patch.object(update, "native_version", return_value="2.1.269"), \
            patch.object(update, "_native_json", return_value=[row]), \
            patch("mindie_knowledge.releases_io.fetch_releases") as fetch, \
            patch.object(update, "_mutation") as mutation:
        with pytest.raises(update.NativePluginDisabled):
            update.check(saved, {})
    fetch.assert_not_called()
    mutation.assert_not_called()


def test_registry_rejects_missing_symbols_and_escaping_test_paths(tmp_path):
    value = harness.compatibility(ROOT)
    for field, reference, message in (
        ("code_paths", "scripts/setup.py:missing_symbol", "code boundary"),
        ("test_ids", "tests/test_release_update.py::missing_test", "contract test"),
        ("test_ids", "tests/../scripts/setup.py::prepare_binding", "contract test"),
    ):
        corrupt = copy.deepcopy(value)
        corrupt["interface_registry"]["plugin.marketplace"][field] = [reference]
        with patch.object(harness, "compatibility", return_value=corrupt):
            with pytest.raises(ValueError, match=message):
                harness.check_registry(ROOT, ROOT / "tests")


def test_release_asset_binds_exact_source_and_rejects_floating_revision():
    value = harness.metadata(ROOT, "a" * 40)
    assert value["source"] == {"repository": harness.REPOSITORY, "sha": "a" * 40}
    assert value["version"] == json.loads(
        (ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8")
    )["version"]
    with pytest.raises(ValueError):
        harness.metadata(ROOT, "main")

@pytest.mark.parametrize("child_failure", [False, True])
def test_background_no_receipt_or_failed_child_is_backed_off(tmp_path, child_failure):
    saved = binding(tmp_path)
    with patch.object(update, "read_receipt", return_value={}), \
            patch.object(update, "run", side_effect=OSError("failed") if child_failure else None) as run, \
            patch.object(update.BackgroundCheck, "_record_error"), \
            patch.object(update.time, "time", return_value=100):
        checker = update.BackgroundCheck(lambda: saved)
        checker.start_if_due()
        checker.thread.join(timeout=2)
        assert not checker.thread.is_alive()
        for _ in range(5):
            checker.start_if_due()
        checker.close()
    assert run.call_count == 1
    assert checker.next_check_at == 160


def test_concurrent_due_triggers_start_only_one_worker(tmp_path):
    saved = binding(tmp_path)
    started = threading.Event()
    release = threading.Event()

    def child(*args, **kwargs):
        started.set()
        assert release.wait(timeout=3)

    with patch.object(update, "read_receipt", return_value={}), \
            patch.object(update, "run", side_effect=child) as run:
        checker = update.BackgroundCheck(lambda: saved)
        threads = [threading.Thread(target=checker.start_if_due) for _ in range(20)]
        for thread in threads:
            thread.start()
        assert started.wait(timeout=2)
        for thread in threads:
            thread.join(timeout=2)
        release.set()
        checker.thread.join(timeout=2)
        checker.close()
    assert run.call_count == 1


def test_prepared_activation_only_rechecks_native_fact_and_runs_activation(tmp_path):
    saved = binding(tmp_path)
    candidate_file = tmp_path / "prepared.adapter.json"
    selected = dict(saved, adapter_config=str(candidate_file))
    candidate_file.write_text(json.dumps(selected), encoding="utf-8")
    state = {
        "candidate": release(), "native_effect": "verified", "status": "deferred",
        "activation_effect": "deferred",
        "last_successful_check": 100, "last_activation_attempt": 101,
        "prepared": {"config": str(candidate_file), "python": saved["python"],
                     "runtime_scripts": saved["runtime_scripts"]},
        "installed": {"version": "0.0.2", "installPath": str(tmp_path / "native")},
    }
    with patch.object(update, "native_binary", return_value="claude"), \
            patch.object(update, "native_version", return_value="2.1.269"), \
            patch.object(update, "installed_plugin", return_value=state["installed"]), \
            patch.object(update, "install_selected") as install, \
            patch("bootstrap.ensure_runtime") as runtime, \
            patch("mindie_knowledge.releases_io.fetch_releases") as fetch, \
            patch("mindie_knowledge.releases_io.verify_release_source") as verify, \
            patch.object(update, "run", side_effect=activation_result("deferred")) as run:
        result = update.check(saved, state)
    assert result["status"] == "deferred"
    assert result["last_successful_check"] == 100
    assert run.call_count == 1
    assert "--activate" in run.call_args.args[0]
    for call in (install, runtime, fetch, verify):
        call.assert_not_called()


def test_new_native_intent_does_not_inherit_previous_settled_process(tmp_path):
    saved = binding(tmp_path)
    state = {"process": {"completed": True, "cleanup_failed": False},
             "native_effect": "completed"}
    recorded = []

    def capture(_binding, value):
        recorded.append(copy.deepcopy(value))

    with patch.object(update, "_record", side_effect=capture), \
            patch.object(update, "run", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            update._mutation(saved, state, "claude", ["update"], "plugin")
    assert recorded[0]["native_effect"] == "unknown"
    assert recorded[0]["process"] == {}
    assert state["native_effect"] == "unknown"


def test_native_preintent_record_failure_cannot_start_or_claim_unknown_mutation(tmp_path):
    state = {}
    with patch.object(update, "_record", side_effect=PermissionError), \
            patch.object(update, "run") as run:
        with pytest.raises(PermissionError):
            update._mutation(binding(tmp_path), state, "claude", ["update"], "plugin")
    run.assert_not_called()
    assert state["native_effect"] == "not_started"
    assert state["process"]["started"] is False


def test_record_failure_does_not_replace_unknown_native_error_or_facts(tmp_path):
    state = {}
    original = RuntimeError("original native interruption")

    def child(*args, receipt, **kwargs):
        receipt.update(started=True, completed=False, cleanup_failed=True)
        raise original

    with patch.object(update, "_record", side_effect=[None, PermissionError]), \
            patch.object(update, "run", side_effect=child):
        with pytest.raises(RuntimeError) as caught:
            update._mutation(binding(tmp_path), state, "claude", ["update"], "plugin")
    assert caught.value is original
    assert state["native_effect"] == "unknown"
    assert state["process"]["cleanup_failed"] is True
    assert state["recording_error"] == "PermissionError"


def test_native_first_install_policy_preserves_exact_source_and_other_choices(tmp_path):
    saved = binding(tmp_path)
    profile = tmp_path / "claude"
    profile.mkdir()
    saved["claude_config_dir"] = str(profile)
    settings = {
        "enabledPlugins": {update.PLUGIN_QUALIFIED: False},
        "extraKnownMarketplaces": {
            update.MARKETPLACE: {"source": {"source": "git",
                "url": f"https://github.com/{harness.REPOSITORY}.git", "ref": "chosen"},
                "autoUpdate": True},
            "other": {"autoUpdate": True},
        },
    }
    path = profile / "settings.json"
    path.write_text(json.dumps(settings), encoding="utf-8")
    update.disable_unfiltered_updates(saved)
    actual = json.loads(path.read_text(encoding="utf-8"))
    settings["extraKnownMarketplaces"][update.MARKETPLACE]["autoUpdate"] = False
    assert actual == settings
    before = path.read_bytes(), path.stat().st_mtime_ns
    update.disable_unfiltered_updates(saved)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    settings["extraKnownMarketplaces"][update.MARKETPLACE]["source"]["url"] = "https://other.invalid"
    path.write_text(json.dumps(settings), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="official Git"):
        update.disable_unfiltered_updates(saved)
    assert path.read_bytes() == before

def test_native_source_declaration_preserves_other_settings_and_explicit_off(tmp_path):
    saved = binding(tmp_path)
    profile = tmp_path / "claude"
    profile.mkdir()
    settings = {"env": {"PRIVATE_TEST": "kept"}, "enabledPlugins": {update.PLUGIN_QUALIFIED: False},
                "extraKnownMarketplaces": {
                    update.MARKETPLACE: {"autoUpdate": True, "source": {
                        "source": "git", "url": f"https://github.com/{harness.REPOSITORY}.git"}},
                    "other": {"autoUpdate": True, "source": {"source": "directory", "path": "/other"}}}}
    path = profile / "settings.json"
    path.write_text(json.dumps(settings))
    before = copy.deepcopy(settings)
    saved["claude_config_dir"] = str(profile)
    update._declare_ref(saved, "v0.0.2")
    actual = json.loads(path.read_text())
    ours = actual["extraKnownMarketplaces"].pop(update.MARKETPLACE)
    assert ours["autoUpdate"] is False
    assert ours["source"]["ref"] == "v0.0.2"
    before["extraKnownMarketplaces"].pop(update.MARKETPLACE)
    assert actual == before


@pytest.mark.parametrize("effect", ["not_started", "deferred"])
def test_incompatible_prepared_candidate_does_not_block_new_eligible_release(tmp_path, effect):
    saved = binding(tmp_path)
    old = release(harness_version="2.1.269")
    newer = release("0.0.3", harness_version="2.1.299")
    state = {
        "status": "failed", "candidate": old, "native_effect": "verified",
        "activation_effect": effect, "prepared": {"config": "old-prepared"},
    }
    selected = []

    def install(_binding, _native, candidate, _state):
        selected.append(candidate)
        raise RuntimeError("stop after eligibility; no native mutation")

    with patch.object(update, "native_binary", return_value="claude"), \
            patch.object(update, "native_version", return_value="2.1.299"), \
            patch.object(update, "installed_plugin", return_value={"enabled": True, "version": "0.0.2", "installPath": str(ROOT)}), \
            patch("mindie_knowledge.releases_io.fetch_releases", return_value=[old, newer]), \
            patch("mindie_knowledge.releases_io.verify_release_source"), \
            patch.object(update, "install_selected", side_effect=install):
        with pytest.raises(RuntimeError, match="stop after eligibility"):
            update.check(saved, state)
    assert selected == [newer]
    assert state["candidate"] == newer
    assert "prepared" not in state
    assert state["activation_effect"] == "not_started"


def test_native_installed_version_prevents_downgrade_while_active_binding_is_older(tmp_path):
    saved = binding(tmp_path)
    state = {
        "status": "deferred", "candidate": release("0.0.3", harness_version="2.1.269"),
        "native_effect": "verified", "activation_effect": "deferred",
        "prepared": {"config": "old-prepared"},
    }
    eligible = release("0.0.2", harness_version="2.1.299")
    with patch.object(update, "native_binary", return_value="claude"), \
            patch.object(update, "native_version", return_value="2.1.299"), \
            patch.object(update, "installed_plugin", return_value={"enabled": True, "version": "0.0.3", "installPath": str(ROOT)}), \
            patch("mindie_knowledge.releases_io.fetch_releases", return_value=[eligible]), \
            patch.object(update, "install_selected") as install:
        result = update.check(saved, state)
    assert result["status"] == "up_to_date"
    assert not update._pending_install(result)
    install.assert_not_called()


@pytest.mark.parametrize("effect,process", [
    ("unknown", {"started": True, "cleanup_failed": True}),
    ("unknown", {"completed": True, "cleanup_failed": False}),
    ("incomplete", {"completed": True, "cleanup_failed": False}),
])
def test_incompatible_activation_with_possible_effect_cannot_be_superseded(tmp_path, effect, process):
    saved = binding(tmp_path)
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps(dict(saved, adapter_config=str(candidate),
                                         engine_config=str(tmp_path / "candidate.engine.json"))))
    state = {
        "status": "failed", "candidate": release(harness_version="2.1.269"),
        "native_effect": "verified", "activation_effect": effect,
        "activation_process": process, "prepared": {"config": str(candidate)},
    }
    with patch.object(update, "native_binary", return_value="claude"), \
            patch.object(update, "native_version", return_value="2.1.299"), \
            patch.object(update, "installed_plugin", return_value={"enabled": True, "version": "0.0.2", "installPath": str(ROOT)}), \
            patch("mindie_knowledge.loop.lifecycle.inspect_retirement",
                  return_value={"status": "retiring"}), \
            patch("mindie_knowledge.releases_io.fetch_releases") as fetch, \
            patch.object(update, "run") as run:
        result = update.check(saved, state)
    assert result["status"] == "incompatible_harness"
    assert result["activation_effect"] == effect
    assert update._pending_install(result)
    fetch.assert_not_called()
    run.assert_not_called()


def test_activation_unknown_without_retirement_or_candidate_owner_is_read_only_not_started(tmp_path):
    saved = binding(tmp_path)
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps(dict(saved, adapter_config=str(candidate))))
    state = {
        "activation_effect": "unknown",
        "activation_process": {"completed": True, "cleanup_failed": False},
        "prepared": {"config": str(candidate)},
    }
    with patch("mindie_knowledge.loop.lifecycle.inspect_retirement", return_value=None), \
            patch("mindie_knowledge.loop.handoff._probe", return_value="absent") as probe, \
            patch.object(update, "run") as run:
        update._observe_activation(saved, state)
    assert state["activation_effect"] == "not_started"
    probe.assert_called_once()
    run.assert_not_called()


def test_exact_old_ready_owner_resolves_candidate_probe_mismatch(tmp_path):
    saved = binding(tmp_path)
    candidate = tmp_path / "candidate.json"
    selected = dict(saved, adapter_config=str(candidate),
                    engine_config=str(tmp_path / "candidate.engine.json"))
    candidate.write_text(json.dumps(selected))
    Path(selected["engine_config"]).write_bytes(Path(saved["engine_config"]).read_bytes())
    state = {
        "activation_effect": "unknown",
        "activation_process": {"completed": True, "cleanup_failed": False},
        "prepared": {"config": str(candidate)},
    }
    with patch("mindie_knowledge.loop.lifecycle.inspect_retirement", return_value=None), \
            patch("mindie_knowledge.loop.handoff._probe", side_effect=["unknown", "ready"]) as probe:
        update._observe_activation(saved, state)
    assert state["activation_effect"] == "not_started"
    assert probe.call_args_list[1].kwargs["config_path"] == saved["engine_config"]


def test_canonical_publication_resolves_activation_without_repeating_control(tmp_path):
    saved = binding(tmp_path)
    candidate = tmp_path / "candidate.json"
    selected = dict(saved, adapter_config=str(candidate))
    candidate.write_text(json.dumps(selected))
    Path(saved["base_config"]).write_text(json.dumps(selected))
    state = {
        "candidate": release(), "native_effect": "verified", "status": "failed",
        "last_successful_check": 100, "last_failed_check": 101,
        "activation_effect": "unknown",
        "activation_process": {"completed": True, "cleanup_failed": False},
        "prepared": {"config": str(candidate)},
    }
    with patch.object(update, "run") as run, \
            patch("mindie_knowledge.loop.lifecycle.inspect_retirement") as retire:
        update._observe_activation(saved, state)
    assert state["installation_complete"] is True
    assert state["activation_effect"] == "activated"
    run.assert_not_called()
    retire.assert_not_called()
    state["installation_complete"] = False
    state["activation_effect"] = "unknown"
    state["status"] = "failed"
    with patch.object(update, "native_binary", return_value="claude"), \
            patch.object(update, "native_version", return_value="2.1.269"), \
            patch.object(update, "installed_plugin", return_value={"enabled": True, "version": "0.0.2", "installPath": str(ROOT)}), \
            patch("mindie_knowledge.releases_io.fetch_releases") as fetch:
        result = update.check(saved, state)
    assert result["installation_complete"] is True
    assert result["last_successful_check"] == 100
    assert "last_failed_check" not in result
    fetch.assert_not_called()


def test_activation_intent_precedes_call_and_interrupt_preserves_process_facts(tmp_path):
    saved = binding(tmp_path)
    prepared = {"python": sys.executable, "runtime_scripts": str(SCRIPTS), "config": "candidate"}
    state = {"activation_effect": "deferred",
             "activation_process": {"completed": True, "cleanup_failed": False}}
    recorded = []
    original = KeyboardInterrupt()

    def child(*args, receipt, **kwargs):
        assert recorded[0]["activation_effect"] == "unknown"
        assert recorded[0]["activation_process"] == {}
        receipt.update(started=True, completed=False, cleanup_failed=True)
        raise original

    def record(_binding, value):
        recorded.append(copy.deepcopy(value))
        if len(recorded) > 1:
            raise PermissionError("record unavailable")

    with patch.object(update, "_record", side_effect=record), \
            patch.object(update, "run", side_effect=child):
        with pytest.raises(KeyboardInterrupt) as error:
            update._activate(saved, prepared, {}, state)
    assert error.value is original
    assert state["activation_effect"] == "unknown"
    assert state["activation_process"]["cleanup_failed"] is True
    assert state["recording_error"] == "PermissionError"


def test_activation_preintent_failure_has_no_side_effect(tmp_path):
    state = {}
    with patch.object(update, "_record", side_effect=PermissionError), \
            patch.object(update, "run") as run:
        with pytest.raises(PermissionError):
            update._activate(binding(tmp_path), {}, {}, state)
    assert state["activation_effect"] == "not_started"
    assert state["activation_process"]["started"] is False
    run.assert_not_called()


def test_each_runtime_process_persists_fresh_intent_before_spawn(tmp_path):
    import bootstrap

    saved = binding(tmp_path)
    state = {}
    records = []
    observer = update._preparation_observer(saved, state)
    calls = 0

    def child(*args, receipt, **kwargs):
        nonlocal calls
        calls += 1
        assert records[-1]["preparation_effect"] == "unknown"
        assert records[-1]["preparation_process"] == {}
        receipt.update(started=True, cleanup_failed=False)
        if calls == 1:
            receipt.update(completed=True, returncode=0)
            return "first process completed"
        receipt["cleanup_failed"] = True
        raise KeyboardInterrupt()

    with patch.object(update, "_record",
                      side_effect=lambda _, value: records.append(copy.deepcopy(value))), \
            patch.object(bootstrap.bounded, "run", side_effect=child):
        bootstrap._run("locked-runtime-install", ["fixture"], on_process=observer)
        with pytest.raises(KeyboardInterrupt):
            bootstrap._run("runtime-readback", ["fixture"], on_process=observer)
    assert [row["preparation_effect"] for row in records] == [
        "unknown", "completed", "unknown", "unknown",
    ]
    assert state["preparation_stage"] == "runtime-readback"
    assert state["preparation_process"]["cleanup_failed"] is True


def test_interrupted_local_preparation_without_cleanup_failure_retries_while_cleanup_failure_blocks(tmp_path):
    saved = binding(tmp_path)
    candidate_file = tmp_path / "prepared.adapter.json"
    selected = dict(saved, adapter_config=str(candidate_file))
    candidate_file.write_text(json.dumps(selected), encoding="utf-8")
    installed_path = tmp_path / "native"
    installed_path.mkdir()
    row = {"version": "0.0.2", "installPath": str(installed_path)}

    for process in ({}, {"started": True, "completed": False, "cleanup_failed": False},
                    {"started": True, "completed": True, "returncode": 1, "cleanup_failed": False}):
        state = {
            "candidate": release(), "native_effect": "verified", "status": "failed",
            "installed": row, "preparation_effect": "unknown",
            "preparation_stage": "locked-runtime-install", "preparation_process": process,
        }

        def fake_run(argv, **kwargs):
            receipt = kwargs.get("receipt")
            if receipt is not None:
                receipt.update(started=True, completed=True, returncode=0, cleanup_failed=False)
            if "--source" in argv:
                return json.dumps({"config": str(candidate_file)})
            if "--activate" in argv:
                return json.dumps({"status": "activated"})
            raise AssertionError(argv)

        with patch.object(update, "native_binary", return_value="claude"), \
                patch.object(update, "native_version", return_value="2.1.269"), \
                patch.object(update, "installed_plugin", return_value=row), \
                patch.object(update, "install_selected", return_value=installed_path), \
                patch("bootstrap.ensure_runtime",
                      side_effect=lambda _, on_process=None: (
                          on_process("locked-runtime-install",
                                     {"started": True, "completed": True, "returncode": 0, "cleanup_failed": False}),
                          (Path(saved["python"]), Path(saved["runtime_scripts"])),
                      )[1]), \
                patch.object(update, "run", side_effect=fake_run):
            result = update.check(saved, state)
        assert result["status"] == "activated"
        assert result["installation_complete"] is True

    blocked = {
        "candidate": release(), "native_effect": "verified", "status": "failed",
        "installed": row, "preparation_effect": "unknown",
        "preparation_stage": "locked-runtime-install",
        "preparation_process": {"started": True, "completed": False, "cleanup_failed": True},
    }
    with patch.object(update, "native_binary", return_value="claude"), \
            patch.object(update, "native_version", return_value="2.1.269"), \
            patch.object(update, "installed_plugin", return_value=row):
        with pytest.raises(RuntimeError, match="previous preparation writer is not confirmed settled"):
            update.check(saved, blocked)

