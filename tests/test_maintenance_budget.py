"""Session maintenance passes its budget through the real diagnostics CLI."""
import os
import json
from pathlib import Path
import sys

from mindie_diagnostics.integration import configure_reporting

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def test_session_retention_child_finishes_within_declared_outer_budget(tmp_path, monkeypatch):
    import maintenance

    config = os.environ["MINDIE_DIAGNOSTICS_CONFIG"]
    assert configure_reporting(False, config=config,
                               roots=[os.environ["MINDIE_DIAGNOSTICS_ROOT"]])["status"] == "configured"
    adapter = Path(os.environ["MINDIE_CC_CONFIG"])
    engine = tmp_path / "engine.json"
    engine.write_text(json.dumps(dict(root=str(tmp_path / "data"), domain="test",
                                     admission_path=str(tmp_path / "admission.sqlite3"))))
    adapter.write_text(json.dumps(dict(base_config=str(adapter), engine_config=str(engine),
                                      state_dir=str(tmp_path / "state"))))
    real_run, children = maintenance.run, []

    def run(argv, **kwargs):
        if "activate" in argv:
            return '{"status":"disabled"}'
        children.append((argv, kwargs))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(maintenance, "run", run)
    result = maintenance.session_start()
    assert result["status"] == "ready" and result["diagnostics"]["status"] == "ok"
    assert len(children) == 1
    argv, options = children[0]
    budget = float(argv[argv.index("--budget-seconds") + 1])
    assert options["timeout"] >= budget + 5
