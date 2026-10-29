"""An independent runtime survives native reload and failed preparation."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

import pytest
from support import ROOT, SCRIPTS

sys.path.insert(0, str(SCRIPTS))
import bootstrap
import component_update
import candidate_validate
from product_contract import identity


def test_component_cli_starts_isolated_from_a_unicode_path(tmp_path, monkeypatch):
    scripts = tmp_path / "插件 with spaces" / "scripts"
    shutil.copytree(SCRIPTS, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    (ambient / "product_contract.py").write_text("raise RuntimeError('ambient import')\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(ambient))
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(scripts / "component_update.py"), "--help"],
        cwd=ambient, capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert "current_python" in result.stdout
    assert not list(scripts.rglob("*.pyc"))


def test_candidate_probe_rejects_a_broken_isolated_update_entry(tmp_path):
    scripts = tmp_path / "candidate" / "scripts"
    shutil.copytree(SCRIPTS, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    (scripts / "product_contract.py").unlink()
    with patch.object(candidate_validate, "build_probe_script", return_value="print('OK')"):
        with pytest.raises(RuntimeError) as caught:
            candidate_validate.runtime_check(scripts)
    assert caught.value.returncode != 0


def test_current_component_runtime_skips_rebuilding_its_adapter(tmp_path):
    baseline = identity(ROOT)
    with patch("mindie_knowledge.component_runtime.discover", return_value={"changed": False}), \
            patch("mindie_knowledge.component_runtime.build") as build:
        assert component_update.prepare(ROOT, sys.executable, tmp_path,
                                        current_source=baseline["source_sha256"]) is None
    build.assert_not_called()


def test_failed_component_candidate_does_not_activate_or_change_profile(tmp_path):
    binding = dict(python=sys.executable, state_dir=str(tmp_path), base_config=str(tmp_path / "cc.json"),
                   product_validation=identity(ROOT))
    Path(binding["base_config"]).write_text(json.dumps(binding))
    before = Path(binding["base_config"]).read_bytes()
    with patch.object(component_update, "prepare", side_effect=RuntimeError("build failed")), \
            patch("bounded.run") as run:
        with pytest.raises(RuntimeError, match="build failed"):
            component_update.check(binding, ROOT)
    run.assert_not_called()
    assert Path(binding["base_config"]).read_bytes() == before


def test_bootstrap_reuses_the_effective_component_interpreter(tmp_path, monkeypatch):
    config = tmp_path / "cc.json"
    config.write_text(json.dumps(dict(component_source=str(ROOT), product_validation=identity(ROOT),
                                     python=sys.executable)))
    monkeypatch.setenv("MINDIE_CC_CONFIG", str(config))
    with patch.object(bootstrap, "readback", return_value=SCRIPTS), \
            patch.object(bootstrap, "_baseline_runtime") as baseline:
        python, scripts = bootstrap.ensure_runtime(ROOT)
    assert (python, scripts) == (Path(sys.executable), SCRIPTS)
    baseline.assert_not_called()


def test_custom_only_plugin_upgrade_does_not_claim_component_inventory(tmp_path, monkeypatch):
    config = tmp_path / "cc.json"
    config.write_text(json.dumps({"component_commands": {
        "profiler": ["/user/profiler-wrapper", "--team-config"]}}))
    monkeypatch.setenv("MINDIE_CC_CONFIG", str(config))
    with patch("mindie_knowledge.component_runtime.discover") as discover, \
            patch("mindie_knowledge.component_runtime.build") as build:
        assert component_update.prepare(ROOT, sys.executable, tmp_path,
                                        plugin_upgrade=True) is None
    discover.assert_not_called()
    build.assert_not_called()
