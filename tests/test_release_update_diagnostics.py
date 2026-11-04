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
