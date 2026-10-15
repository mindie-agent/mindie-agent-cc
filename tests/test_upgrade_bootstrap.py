"""Stable release entries survive native cache removal without a version selector."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
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
            patch.object(bootstrap.bounded, "run") as run:
        bootstrap.ensure_runtime(source)
        run.assert_called_once()
        assert runtime.with_suffix(".ready.json").is_file()
        readback.side_effect = ValueError("installed bytes differ")
        with pytest.raises(ValueError, match="installed bytes differ"):
            bootstrap.ensure_runtime(source)
        run.assert_called_once()


@pytest.mark.parametrize("termination", ["kill", "terminate"] if os.name == "posix" else ["kill"])
def test_interrupted_cold_bootstrap_reaps_uv_tree_without_publishing_ready(tmp_path, termination):
    from test_update_lifetime import _alive

    source = tmp_path / "source"
    source.mkdir()
    (source / "uv.lock").write_text("version=1\n", encoding="utf-8")
    marker = tmp_path / "uv-tree.json"
    uv = tmp_path / "fake_uv.py"
    uv.write_text(
        "import json,os,subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\n"
        f"open({str(marker)!r},'w').write(json.dumps([os.getpid(),child.pid]))\n"
        "time.sleep(30)\n", encoding="utf-8")
    runtimes = tmp_path / "runtimes"
    program = (
        f"import sys;from pathlib import Path;sys.path.insert(0,{str(SCRIPTS)!r});"
        "import bootstrap;"
        f"bootstrap.runtime_root=lambda:Path({str(runtimes)!r});"
        "bootstrap.runtime_key=lambda source:'fixed';"
        "bootstrap.source_identity=lambda source:'source';"
        "bootstrap.shutil.which=lambda name:sys.executable;"
        "run=bootstrap._run;"
        f"bootstrap._run=lambda stage,argv,**kw:run(stage,[sys.executable,{str(uv)!r}],**kw);"
        f"bootstrap.ensure_runtime(Path({str(source)!r}))"
    )
    process = subprocess.Popen([sys.executable, "-c", program],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    pids = []
    try:
        deadline = time.monotonic() + 8
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert marker.exists(), process.communicate(timeout=2)[1].decode()
        pids = json.loads(marker.read_text(encoding="utf-8"))
        assert all(_alive(pid) for pid in pids)
        getattr(process, termination)()
        process.wait(timeout=5)
        deadline = time.monotonic() + 3
        while any(_alive(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(.02)
        assert not any(_alive(pid) for pid in pids)
        assert not (runtimes / "fixed.ready.json").exists()
        if termination == "terminate":
            assert b"locked-runtime-install" in process.stderr.read()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        process.stderr.close()
        for pid in pids:
            if _alive(pid):
                import signal
                os.kill(pid, signal.SIGTERM)


@pytest.mark.skipif(os.name != "posix", reason="POSIX SIGTERM handler; Windows owner death tested separately")
def test_cold_bootstrap_cancellation_interrupts_a_held_prepare_lock(tmp_path):
    from install_state import OperationLock

    source = tmp_path / "source"
    source.mkdir()
    (source / "uv.lock").write_text("version=1\n", encoding="utf-8")
    runtimes = tmp_path / "runtimes"
    waiting = tmp_path / "waiting"
    program = f"""
import sys
from pathlib import Path
sys.path.insert(0, {str(SCRIPTS)!r})
import bootstrap, install_state
bootstrap.runtime_root = lambda: Path({str(runtimes)!r})
bootstrap.runtime_key = lambda source: 'fixed'
original = install_state._lock_once
def lock_once(*args, **kwargs):
    acquired = original(*args, **kwargs)
    if not acquired:
        Path({str(waiting)!r}).touch()
    return acquired
install_state._lock_once = lock_once
bootstrap.ensure_runtime(Path({str(source)!r}))
"""
    with OperationLock({"state_dir": str(runtimes)}).exclusive():
        process = subprocess.Popen([sys.executable, "-c", program],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 5
            while not waiting.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            assert waiting.exists()
            process.terminate()
            process.wait(timeout=2)
            assert b"CommandCancelled" in process.stderr.read()
            assert not (runtimes / "fixed.ready.json").exists()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            process.stderr.close()
