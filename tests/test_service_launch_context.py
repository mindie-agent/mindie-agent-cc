"""Adapter path selection and shared service ownership, without a model call."""
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

from support import SCRIPTS

sys.path.insert(0, str(SCRIPTS))
import knowledge_service
import paths


def test_context_declares_names_without_loading_provider_or_requiring_claude(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "settings.json").write_text("{invalid", encoding="utf-8")
    values = {
        "ANTHROPIC_AUTH_TOKEN": "fixture-provider-secret",
        "HTTPS_PROXY": "https://fixture-proxy-secret",
        "PYTHONPATH": "must-not-transfer",
        "CLAUDE_CONFIG_DIR": str(profile),
    }
    with patch.dict(os.environ, values, clear=True), \
            patch.object(paths.Path, "home", return_value=tmp_path / "home"), \
            patch.object(paths.shutil, "which", return_value=None):
        declaration = paths.service_launch_context(tmp_path / "adapter.json", {})
    assert declaration["paths"]["CLAUDE_CONFIG_DIR"] == str(profile)
    assert "MINDIE_CC_BIN" not in declaration["paths"]
    assert "ANTHROPIC_AUTH_TOKEN" in declaration["environment"]
    assert "HTTPS_PROXY" in declaration["environment"]
    assert "https_proxy" in declaration["environment"]
    assert "NODE_EXTRA_CA_CERTS" in declaration["environment"]
    assert "PYTHONPATH" not in declaration["environment"]
    assert not set(declaration["paths"]) & set(declaration["environment"])
    serialized = json.dumps(declaration)
    for secret in values.values():
        if secret != str(profile):
            assert secret not in serialized


def test_new_generation_rebinds_adapter_and_preserves_selected_service_paths(tmp_path):
    previous = {
        "paths": {
            "MINDIE_CC_CONFIG": str(tmp_path / "old" / "adapter.json"),
            "CLAUDE_CONFIG_DIR": str(tmp_path / "selected-profile"),
            "MINDIE_CC_BIN": str(tmp_path / "bin" / "claude"),
            "MINDIE_DIAGNOSTICS_CONFIG": str(tmp_path / "diagnostics.json"),
            "MINDIE_DIAGNOSTICS_ROOT": str(tmp_path / "diagnostics"),
        },
        "environment": [],
    }
    new_adapter = tmp_path / "new" / "adapter.json"
    with patch.dict(os.environ, {}, clear=True), \
            patch.object(paths.shutil, "which", side_effect=AssertionError("existing selection lost")):
        declaration = paths.service_launch_context(new_adapter, {}, previous)
    assert declaration["paths"] == dict(previous["paths"], MINDIE_CC_CONFIG=str(new_adapter))
    assert declaration["environment"] == [*paths.NATIVE_PROVIDER_ENV, *paths.TRANSPORT_ENV]


def test_generation_does_not_reselect_paths_from_ambient_environment(tmp_path):
    previous = {"paths": {
        key: str(tmp_path / "old" / key) for key in (
            "CLAUDE_CONFIG_DIR", "MINDIE_CC_BIN",
            "MINDIE_DIAGNOSTICS_CONFIG", "MINDIE_DIAGNOSTICS_ROOT",
        )
    }}
    choices = {key: str(tmp_path / "new" / key) for key in previous["paths"]}
    adapter_profile = tmp_path / "adapter-profile"
    with patch.dict(os.environ, choices, clear=True), \
            patch.object(paths.shutil, "which", side_effect=AssertionError("must retain selection")):
        declaration = paths.service_launch_context(
            tmp_path / "adapter.json", {"claude_config_dir": str(adapter_profile)}, previous,
        )
    assert declaration["paths"] == dict(
        previous["paths"], MINDIE_CC_CONFIG=str(tmp_path / "adapter.json"),
    )


def test_first_install_honors_adapter_profile_and_explicit_native_path(tmp_path):
    choices = {
        "CLAUDE_CONFIG_DIR": str(tmp_path / "caller-profile"),
        "MINDIE_CC_BIN": str(tmp_path / "bin" / "claude"),
        "MINDIE_DIAGNOSTICS_CONFIG": str(tmp_path / "diagnostics.json"),
        "MINDIE_DIAGNOSTICS_ROOT": str(tmp_path / "diagnostics"),
    }
    profile = tmp_path / "adapter-profile"
    with patch.dict(os.environ, choices, clear=True), \
            patch.object(paths.shutil, "which", return_value=None):
        declaration = paths.service_launch_context(
            tmp_path / "adapter.json", {"claude_config_dir": str(profile)},
        )
    assert declaration["paths"] == dict(
        choices, CLAUDE_CONFIG_DIR=str(profile), MINDIE_CC_CONFIG=str(tmp_path / "adapter.json"),
    )


def test_unresolved_explicit_command_does_not_invent_an_executable_path(tmp_path):
    with patch.dict(os.environ, {"MINDIE_CC_BIN": "missing-fixture-claude"}, clear=True), \
            patch.object(paths.Path, "home", return_value=tmp_path / "home"), \
            patch.object(paths.shutil, "which", return_value=None):
        with pytest.raises(ValueError, match="explicit native executable is unavailable"):
            paths.service_launch_context(tmp_path / "adapter.json", {})


def test_existing_context_without_native_path_does_not_acquire_ambient_binary(tmp_path):
    previous = {"paths": {"CLAUDE_CONFIG_DIR": str(tmp_path / "selected")}}
    with patch.dict(os.environ, {"MINDIE_CC_BIN": "unrelated-claude"}, clear=True), \
            patch.object(paths.shutil, "which", side_effect=AssertionError("must retain absence")):
        declaration = paths.service_launch_context(tmp_path / "adapter.json", {}, previous)
    assert "MINDIE_CC_BIN" not in declaration["paths"]


def test_invalid_previous_context_is_rejected_before_writing(tmp_path):
    from mindie_knowledge.loop.service_context import ContextError

    previous = {"paths": {"CLAUDE_CONFIG_DIR": "relative-path"}}
    with pytest.raises(ContextError, match="service_launch_path_invalid"):
        paths.service_launch_context(tmp_path / "adapter.json", {}, previous)


@pytest.mark.parametrize("outcome", ["ready", "starting"])
def test_prepare_delegates_to_core_owner_and_preserves_readiness(outcome, tmp_path):
    engine = tmp_path / "engine.json"
    with patch("mindie_knowledge.loop.cli.prepare_service", return_value=outcome) as prepare, \
            patch("mindie_knowledge.loop.process.spawn_service",
                  side_effect=AssertionError("adapter must not detach a second starter")):
        assert knowledge_service.prepare_service(engine) == outcome
    prepare.assert_called_once_with(str(engine.resolve()))


@pytest.mark.parametrize("error", [OSError("registration failed"), RuntimeError("owner unconfirmed")])
def test_prepare_delivers_core_failure_without_another_start(error, tmp_path):
    with patch("mindie_knowledge.loop.cli.prepare_service", side_effect=error) as prepare:
        with pytest.raises(type(error), match=str(error)):
            knowledge_service.prepare_service(tmp_path / "engine.json")
    prepare.assert_called_once()
