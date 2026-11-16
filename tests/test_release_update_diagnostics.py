"""native_update classifies environment misses; real crashes stay internal."""

import builtins
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from mindie_diagnostics.integration import configure_reporting
from mindie_knowledge.releases_io import ReleaseFetchError

from support import SCRIPTS, make_config

import sys
sys.path.insert(0, str(SCRIPTS))
import release_update as update


def binding(tmp_path):
    path = make_config(tmp_path)
    value = json.loads(path.read_text())
    value["product_validation"] = {"native_version": "0.0.1"}
    value["claude_config_dir"] = str(tmp_path / "claude")
    path.write_text(json.dumps(value), encoding="utf-8")
    return value


def _last_event(logs):
    files = list(Path(logs).glob("events/*/*.jsonl"))
    assert files, "expected a local diagnostic event"
    return json.loads(files[-1].read_text(encoding="utf-8").splitlines()[-1])


def _record_check_failure(tmp_path, error):
    saved = binding(tmp_path)
    policy = Path(os.environ["MINDIE_DIAGNOSTICS_CONFIG"])
    logs = Path(os.environ["MINDIE_DIAGNOSTICS_ROOT"])
    configure_reporting(True, config=policy, roots=[str(logs)])
    with patch.object(update, "read_binding", return_value=saved), \
            patch.object(update, "check", side_effect=error):
        update.main()
    return update.read_receipt(saved), _last_event(logs)


def _network_error():
    error = RuntimeError("native installation did not complete cleanly; inspect its receipt")
    error.category = "temporary_network"
    error.returncode = 1
    return error


def test_http_catalog_miss_is_connectivity_not_internal(tmp_path):
    receipt, event = _record_check_failure(
        tmp_path, ReleaseFetchError("release_http_error", status=403))
    assert receipt["status"] == "failed"
    assert receipt["error_type"] == "ReleaseFetchError"
    assert event["attributes"]["category"] == "connectivity"
    assert event["attributes"]["error_code"] == "release_http_error"
    assert event["attributes"]["error_type"] == "ReleaseFetchError"
    assert event["reportable"] is False


@pytest.mark.parametrize("code", (
    "release_source_blob_mismatch",
    "release_source_declaration_mismatch",
    "invalid_release_json",
))
def test_source_integrity_failure_stays_reportable(tmp_path, code):
    receipt, event = _record_check_failure(tmp_path, ReleaseFetchError(code))
    assert receipt["status"] == "failed"
    assert receipt["error_type"] == "ReleaseFetchError"
    assert event["attributes"]["category"] == "internal"
    assert event["attributes"]["error_code"] == "update_internal_failure"
    assert event["attributes"]["error_type"] == "ReleaseFetchError"
    assert event["reportable"] is True


def test_rate_limited_catalog_is_connectivity(tmp_path):
    receipt, event = _record_check_failure(
        tmp_path, ReleaseFetchError("release_response_too_large", status=429))
    assert receipt["status"] == "failed"
    assert event["attributes"]["category"] == "connectivity"
    assert event["attributes"]["error_code"] == "update_transport_unavailable"
    assert event["reportable"] is False


def test_release_import_failure_still_records_the_original_error(tmp_path, monkeypatch):
    original_import = builtins.__import__

    def fail_release_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "mindie_knowledge.releases_io":
            raise ImportError("semantic_version is not installed")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fail_release_import)
    original = RuntimeError("native helper dumped core")
    receipt, event = _record_check_failure(tmp_path, original)
    assert receipt["status"] == "failed"
    assert receipt["error_type"] == "RuntimeError"
    assert event["attributes"]["error_type"] == "RuntimeError"
    assert event["attributes"]["category"] == "internal"
    assert event["reportable"] is True


def test_missing_native_binary_is_a_local_prerequisite(tmp_path):
    receipt, event = _record_check_failure(
        tmp_path, FileNotFoundError("native Claude executable is not bound to the installation"))
    assert receipt["status"] == "failed"
    assert event["attributes"]["category"] == "prerequisite"
    assert event["attributes"]["error_code"] == "update_prerequisite_unavailable"
    assert event["reportable"] is False


def test_native_cli_transport_failure_is_connectivity_with_exit_code(tmp_path):
    receipt, event = _record_check_failure(tmp_path, _network_error())
    assert receipt["status"] == "failed"
    assert receipt["error_type"] == "RuntimeError"
    assert event["attributes"]["category"] == "connectivity"
    assert event["attributes"]["error_code"] == "update_transport_unavailable"
    assert event["attributes"]["error_type"] == "RuntimeError"
    assert event["attributes"]["exit_code"] == 1
    assert event["reportable"] is False


def test_unclassified_native_runtimeerror_remains_internal(tmp_path):
    receipt, event = _record_check_failure(tmp_path, RuntimeError("native helper dumped core"))
    assert receipt["status"] == "failed"
    assert event["attributes"]["category"] == "internal"
    assert event["attributes"]["error_code"] == "update_internal_failure"
    assert event["attributes"]["error_type"] == "RuntimeError"
    assert "exit_code" not in event["attributes"]
    assert event["reportable"] is True


def test_unbound_native_binary_is_a_prerequisite(tmp_path):
    saved = binding(tmp_path)
    with patch("mindie_knowledge.loop.cli.config_at",
               return_value={"service_launch_context": {"paths": {}}}):
        with pytest.raises(FileNotFoundError, match="not bound"):
            update.native_binary(saved)


def test_missing_user_plugin_is_a_prerequisite():
    with patch.object(update, "_native_json", return_value=[]):
        with pytest.raises(FileNotFoundError, match="user-scoped"):
            update.installed_plugin("claude")


def test_native_json_classifies_transport():
    with patch.object(update, "run", return_value="[]") as run:
        update._native_json("claude", ["list", "--json"])
    assert run.call_args.kwargs["transport"] is True


def test_native_writer_network_failure_is_typed_and_omits_streams(tmp_path):
    import copy

    saved = binding(tmp_path)
    state = {}
    recorded = []

    def child(*args, receipt, **kwargs):
        receipt.update(
            started=True, completed=True, cleanup_failed=False, returncode=1,
            stdout="ok", stderr="NewConnectionError: connection refused",
        )
        return ""

    with patch.object(update, "_record", side_effect=lambda _, value: recorded.append(copy.deepcopy(value))), \
            patch.object(update, "run", side_effect=child):
        with pytest.raises(RuntimeError) as caught:
            update._mutation(saved, state, "claude", ["update"], "plugin")
    assert caught.value.category == "temporary_network"
    assert caught.value.returncode == 1
    assert "stdout" not in state["process"]
    assert "stderr" not in state["process"]
    assert all("stdout" not in row.get("process", {}) for row in recorded)
    assert all("stderr" not in row.get("process", {}) for row in recorded)


def test_native_writer_unclassified_failure_stays_untyped(tmp_path):
    saved = binding(tmp_path)
    state = {}

    def child(*args, receipt, **kwargs):
        receipt.update(
            started=True, completed=True, cleanup_failed=False, returncode=1,
            stderr="native helper dumped core",
        )
        return ""

    with patch.object(update, "_record"), patch.object(update, "run", side_effect=child):
        with pytest.raises(RuntimeError) as caught:
            update._mutation(saved, state, "claude", ["update"], "plugin")
    assert getattr(caught.value, "category", None) is None
    assert caught.value.returncode == 1


def test_post_install_stage_and_transport_classification_prevent_false_internal_report(tmp_path):
    error = RuntimeError("command failed (1)")
    error.returncode = 1
    error.category = "temporary_network"
    error.mindie_stage = "locked-runtime-install"
    receipt, event = _record_check_failure(tmp_path, error)
    assert receipt["status"] == "failed"
    assert event["attributes"]["stage"] == "locked_runtime_install"
    assert event["attributes"]["category"] == "connectivity"
    assert event["attributes"]["error_code"] == "update_transport_unavailable"
    assert event["attributes"]["exit_code"] == 1
    assert event["reportable"] is False


def test_binding_prepare_and_activation_enable_transport_and_tag_stage(tmp_path):
    saved = binding(tmp_path)
    state = dict(
        prepared=dict(
            python=sys.executable,
            runtime_scripts=str(SCRIPTS),
            config=str(tmp_path / "prepared.json"),
        ),
        activation_effect="not_started",
    )
    with patch.object(update, "_record"), \
            patch.object(update, "run", side_effect=RuntimeError("boom")) as run_mock:
        with pytest.raises(RuntimeError) as caught:
            update._activate(saved, state["prepared"], {}, state)
    assert run_mock.call_args.kwargs["transport"] is True
    assert caught.value.mindie_stage == "runtime_activation"


# --------------------------------------------------------------------------- #
# Issue #360: owned `setup.py` children must not collapse into internal faults.
# --------------------------------------------------------------------------- #

def _envelope(error):
    import setup_diagnostics

    return json.dumps(setup_diagnostics.failure_envelope(error, source_root=SCRIPTS))


def _publication_error(monkeypatch, stderr):
    import bounded
    import candidate_validate

    def run(args, data, **kwargs):
        kwargs["receipt"].update(started=True, completed=True, returncode=1,
                                 cleanup_failed=False, stdout="", stderr=stderr)
        return ""

    monkeypatch.setattr(bounded, "run", run)
    with pytest.raises(candidate_validate.CheckFailure) as caught:
        candidate_validate.publication_git(
            ["git", "fetch", "origin"], "fetch_failed", {}, timeout=1.0)
    return caught.value


def _component_prepare(*args, **kwargs):
    from support import ROOT

    return {"python": sys.executable, "scripts": str(SCRIPTS),
            "source": str(ROOT), "components": {}}


def _record_component_failure(tmp_path, monkeypatch, envelope):
    import bounded
    import component_update

    monkeypatch.setattr(component_update, "prepare", _component_prepare)

    def child(argv, *args, **kwargs):
        assert kwargs["env"]["MINDIE_SETUP_ERROR_ENVELOPE"] == "1"
        error = RuntimeError("command failed (1)")
        error.mindie_output = envelope
        error.returncode = 1
        raise error

    monkeypatch.setattr(bounded, "run", child)
    saved = binding(tmp_path)
    policy = Path(os.environ["MINDIE_DIAGNOSTICS_CONFIG"])
    logs = Path(os.environ["MINDIE_DIAGNOSTICS_ROOT"])
    configure_reporting(True, config=policy, roots=[str(logs)])

    def check(value, _state=None):
        component_update.check(
            dict(value, product_validation={"source_sha256": "a" * 64}), SCRIPTS.parent)

    with patch.object(update, "read_binding", return_value=saved), \
            patch.object(update, "check", side_effect=check):
        update.main()
    return update.read_receipt(saved), _last_event(logs)


@pytest.mark.parametrize("stderr, child_category, category", (
    ("fatal: unable to access 'https://github.com/': Could not resolve host: github.com",
     "resolver", "connectivity"),
    ("fatal: unable to access 'https://github.com/': SSL certificate problem: "
     "unable to get local issuer certificate", "certificate", "credentials"),
    ("fatal: unable to access 'https://github.com/': connection reset by peer",
     "temporary_network", "connectivity"),
))
def test_child_publication_transport_failure_stays_non_reportable(
        tmp_path, monkeypatch, stderr, child_category, category):
    error = _publication_error(monkeypatch, stderr)
    assert error.category == child_category
    assert error.substage == "publication_fetch"
    assert error.reason_code == "fetch_failed"
    assert "category" not in error.failure and "reason_code" not in error.failure
    assert set(error.failure) == {"stage", "code", "process"}
    receipt, event = _record_component_failure(tmp_path, monkeypatch, _envelope(error))

    attributes = event["attributes"]
    assert receipt["status"] == "failed"
    assert attributes["category"] == category
    assert event["reportable"] is False
    assert attributes["stage"] == "publication_fetch"
    assert attributes["substage"] == "publication_fetch"
    assert attributes["reason_code"] == "fetch_failed"


def test_child_lock_timeout_defers_without_an_error_event(tmp_path, monkeypatch):
    import bounded
    import component_update

    calls = []
    monkeypatch.setattr(component_update, "prepare", _component_prepare)

    def child(argv, *args, **kwargs):
        calls.append((list(argv), dict(kwargs)))
        return json.dumps({"status": "deferred", "stage": "lock_busy", "switched": False})

    monkeypatch.setattr(bounded, "run", child)
    result = component_update.check(
        dict(binding(tmp_path), product_validation={"source_sha256": "a" * 64}),
        SCRIPTS.parent)

    assert result == {"status": "deferred", "stage": "lock_busy"}
    assert len(calls) == 1 and "--source" in calls[0][0]
    assert "--activate" not in calls[0][0]
    assert calls[0][1]["env"]["MINDIE_SETUP_ERROR_ENVELOPE"] == "1"

    saved = binding(tmp_path)
    logs = Path(os.environ["MINDIE_DIAGNOSTICS_ROOT"])
    configure_reporting(True, config=Path(os.environ["MINDIE_DIAGNOSTICS_CONFIG"]),
                        roots=[str(logs)])
    with patch.object(update, "read_binding", return_value=saved), \
            patch.object(update, "check",
                         return_value={"status": "deferred", "stage": "lock_busy"}):
        update.main()
    receipt = update.read_receipt(saved)
    assert receipt["status"] == "deferred"
    assert "last_failed_check" not in receipt
    assert not list(Path(logs).glob("events/*/*.jsonl"))


def test_deferred_component_check_returns_without_marking_a_failure(tmp_path):
    saved = binding(tmp_path)
    state = {"last_successful_check": 10}
    with patch.object(update, "native_binary", return_value="claude"), \
            patch.object(update, "native_version", return_value="2.1.269"), \
            patch.object(update, "installed_plugin",
                         return_value={"enabled": True, "version": "0.0.1",
                                       "installPath": str(SCRIPTS.parent)}), \
            patch("mindie_knowledge.releases_io.fetch_releases", return_value=[]), \
            patch("component_update.check",
                  return_value={"status": "deferred", "stage": "lock_busy"}):
        result = update.check(saved, state)

    assert result["status"] == "deferred"
    assert result["stage"] == "lock_busy"
    assert result["components"] == {"status": "deferred", "stage": "lock_busy"}
    assert "last_failed_check" not in result
    assert result["last_successful_check"] == 10


def _child_setup_envelope(monkeypatch, capsys):
    import candidate_validate
    import setup as setup_module

    error = candidate_validate.CheckFailure(
        "runtime_pins", "revision_mismatch", "mindie-knowledge")

    def prepare(source, *args, **kwargs):
        return setup_module.validate_candidate(source)

    monkeypatch.setattr(setup_module, "prepare_binding", prepare)
    monkeypatch.setattr(candidate_validate, "validate",
                        lambda *args, **kwargs: (_ for _ in ()).throw(error))
    monkeypatch.setenv("MINDIE_SETUP_ERROR_ENVELOPE", "1")
    with pytest.raises(SystemExit) as caught:
        setup_module.main(["--source", str(SCRIPTS.parent)])
    assert caught.value.code == 1
    return json.loads(capsys.readouterr().out)


def test_child_internal_failure_keeps_setup_frames_and_reason_code(
        tmp_path, monkeypatch, capsys):
    envelope = json.dumps(_child_setup_envelope(monkeypatch, capsys))
    receipt, event = _record_component_failure(tmp_path, monkeypatch, envelope)

    attributes = event["attributes"]
    assert receipt["status"] == "failed"
    assert attributes["category"] == "internal"
    assert event["reportable"] is True
    assert attributes["error_code"] == "update_internal_failure"
    assert attributes["reason_code"] == "revision_mismatch"
    frames = attributes["stack_frames"]
    assert any(frame["module"] == "mindie_agent_cc.setup"
               and frame["function"] == "validate_candidate" for frame in frames), frames
    assert not any("bounded" in frame["module"] for frame in frames), frames


def product_identity(source, *, components=None):
    import product_contract

    return product_contract.identity(str(source), components=components)


def test_validated_publication_skips_the_network_fetch_but_keeps_local_checks(monkeypatch):
    import candidate_validate
    from mindie_knowledge import runtime_inventory
    from mindie_knowledge.component_releases import COMPONENTS
    from support import ROOT

    baseline = product_identity(ROOT)
    components = {
        "schema": "mindie-component-runtime/1",
        "adapter_source_sha256": baseline["source_sha256"],
        "lock_sha256": "a" * 64,
        "runtime": {name: {"repository": COMPONENTS[name], "version": "0.0.1",
                           "revision": revision}
                    for name, revision in baseline["runtime"].items()},
    }
    expected = product_identity(ROOT, components=components)

    calls = []
    monkeypatch.setattr(runtime_inventory, "verify_lock",
                        lambda plan: calls.append("verify_lock"))
    monkeypatch.setattr(runtime_inventory, "verify",
                        lambda runtime: calls.append("verify"))
    real_runtime_check = candidate_validate.runtime_check

    def runtime_check(scripts):
        calls.append("runtime_check")
        return real_runtime_check(scripts)

    monkeypatch.setattr(candidate_validate, "runtime_check", runtime_check)

    def forbidden(publication):
        raise AssertionError("validated publication must not be fetched again")

    monkeypatch.setattr(candidate_validate, "publication_contract", forbidden)
    result = candidate_validate.validate(
        ROOT, expected, verified_publication=dict(expected, status="validated"))

    assert result["status"] == "validated"
    assert calls == ["verify_lock", "verify", "runtime_check"]


def test_typed_update_error_preserves_child_categories():
    for category in ("resolver", "busy", "deferred", "prerequisite", "cancelled"):
        error = RuntimeError("typed child failure")
        error.category = category
        assert update._typed_update_error(error) is error
        assert error.category == category


def test_legacy_diagnostics_treat_resolver_as_connectivity(tmp_path, monkeypatch):
    import mindie_diagnostics.fallback as fallback

    policy = Path(os.environ["MINDIE_DIAGNOSTICS_CONFIG"])
    logs = Path(os.environ["MINDIE_DIAGNOSTICS_ROOT"])
    configure_reporting(True, config=policy, roots=[str(logs)])

    def legacy_policy(exception):
        if getattr(exception, "category", None) in {"temporary_network", "rate_limited"}:
            return "connectivity", False, "update_transport_unavailable"
        return "internal", True, "update_internal_failure"

    monkeypatch.setattr(fallback, "update_failure_policy", legacy_policy)
    error = RuntimeError("git dns miss")
    error.category = "resolver"

    update._record_update_failure("native_update", error)

    event = _last_event(logs)
    assert event["attributes"]["category"] == "connectivity"
    assert event["attributes"]["error_code"] == "update_transport_unavailable"
    assert event["reportable"] is False
    assert error.category == "resolver"


@pytest.mark.parametrize("kind", ("prerequisite", "internal"))
def test_activate_decodes_child_setup_error_envelope(tmp_path, kind):
    import candidate_validate

    saved = binding(tmp_path)
    prepared = dict(
        python=sys.executable,
        runtime_scripts=str(SCRIPTS),
        config=str(tmp_path / "prepared.json"),
    )
    child_exc = (
        FileNotFoundError("uv is missing")
        if kind == "prerequisite"
        else candidate_validate.CheckFailure("runtime_pins", "revision_mismatch", "mindie-knowledge")
    )

    def run_child(*args, **kwargs):
        assert kwargs["env"]["MINDIE_SETUP_ERROR_ENVELOPE"] == "1"
        kwargs["receipt"].update(started=True, completed=True, cleanup_failed=False, returncode=1)
        failure = RuntimeError("command failed (1)")
        failure.mindie_output = _envelope(child_exc)
        failure.returncode = 1
        raise failure

    state = {"activation_effect": "not_started"}
    expected_cls = FileNotFoundError if kind == "prerequisite" else RuntimeError
    with patch.object(update, "_record"), patch.object(update, "run", side_effect=run_child):
        with pytest.raises(expected_cls) as caught:
            update._activate(saved, prepared, {}, state)
    _, event = _record_check_failure(tmp_path, caught.value)
    if kind == "prerequisite":
        assert caught.value.mindie_stage == "setup"
        assert event["attributes"]["category"] == "prerequisite"
        assert event["reportable"] is False
    else:
        assert caught.value.reason_code == "revision_mismatch"
        assert event["attributes"]["category"] == "internal"
        assert event["attributes"]["reason_code"] == "revision_mismatch"
        assert event["reportable"] is True
