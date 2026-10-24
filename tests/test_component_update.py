"""An independent runtime survives native reload and failed preparation."""
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest
from support import ROOT, SCRIPTS

sys.path.insert(0, str(SCRIPTS))
import bootstrap
import component_update
from product_contract import identity


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
