"""Stable release entries survive native cache removal without a version selector."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

import pytest
from support import SCRIPTS
sys.path.insert(0, str(SCRIPTS))
import bootstrap
import setup


def test_release_entry_survives_cache_removal_and_ignores_ambient_modules(tmp_path):
    installed = tmp_path / "installed"
    installed.mkdir()
    (installed / "mindie_launch.py").write_text(
        "import json,os,sys;print(json.dumps({'config':os.environ['MINDIE_CC_CONFIG'],"
        "'args':sys.argv[1:],'pythonpath':os.environ.get('PYTHONPATH')}))")
    entry = tmp_path / "plugin-data/releases/0.0.1/entry.py"
    config = tmp_path / "immutable-adapter.json"
    with patch.object(setup, "HERE", installed):
        setup.publish_entry(entry, config)
    cache = tmp_path / "native-cache"
    cache.mkdir()
    shutil.rmtree(cache)
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(cache), PYTHONPATH=str(tmp_path / "unrelated"))
    for role in (("hook", "session-start"), ("hook", "stop"), ("mcp", "knowledge")):
        result = subprocess.run([sys.executable, str(entry), *role], env=env,
                                capture_output=True, text=True, check=True)
        value = json.loads(result.stdout)
        assert value == {"config": str(config), "args": list(role), "pythonpath": None}


def test_entry_publication_is_complete_and_same_version_cannot_change_binding(tmp_path):
    entry = tmp_path / "entry.py"
    setup.publish_entry(entry, tmp_path / "first.json")
    before = entry.read_bytes()
    setup.publish_entry(entry, tmp_path / "first.json")
    with pytest.raises(ValueError, match="different bytes"):
        setup.publish_entry(entry, tmp_path / "second.json")
    assert entry.read_bytes() == before
    assert not list(tmp_path.glob(".entry-*"))


def test_stop_cannot_prepare_runtime_even_if_dependency_environment_exists():
    with patch.object(bootstrap, "ensure_runtime") as prepare:
        with pytest.raises(RuntimeError, match="start a new Claude session"):
            bootstrap.main(["hook", "stop"])
    prepare.assert_not_called()


def test_completed_runtime_corruption_never_invokes_uv_to_repair(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "uv.lock").write_text("version=1\n")
    runtimes = tmp_path / "runtimes"
    runtime = runtimes / "fixed"
    with patch.object(bootstrap, "runtime_root", return_value=runtimes), \
            patch.object(bootstrap, "runtime_key", return_value="fixed"), \
            patch.object(bootstrap, "source_identity", return_value="source"), \
            patch.object(bootstrap, "readback", return_value=tmp_path) as readback, \
            patch.object(bootstrap.shutil, "which", return_value="uv"), \
            patch.object(bootstrap.subprocess, "run") as run:
        bootstrap.ensure_runtime(source)
        run.assert_called_once()
        assert runtime.with_suffix(".ready.json").is_file()
        readback.side_effect = ValueError("installed bytes differ")
        with pytest.raises(ValueError, match="installed bytes differ"):
            bootstrap.ensure_runtime(source)
        run.assert_called_once()
