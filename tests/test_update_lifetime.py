"""Caller lifetime, exact native reconciliation, and final feed delivery."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

from support import SCRIPTS, make_config

sys.path.insert(0, str(SCRIPTS))
import bounded
import capture_config
import genstate
import native_claude
import updater


@pytest.fixture
def adapter(tmp_path):
    config = make_config(tmp_path)
    value = json.loads(config.read_text())
    genstate.write_current(dict(generation=str(SCRIPTS.parent), python=sys.executable,
                                adapter_config=str(config), sha="a" * 40), value)
    return value


def test_healthy_work_outlives_scheduler_window_and_returns_actual_feed(adapter):
    clock = [100.0]
    rows = [dict(repository="fixture/public", status="synced", entries=660)]
    calls = []

    def plugin(config, deadline, **_):
        assert deadline is None
        clock[0] += 700
        genstate.write_status(dict(result="switched"), config)
        print(json.dumps(dict(result="switched")))
        return 0

    def run(argv, stdin="", **kwargs):
        calls.append(kwargs)
        assert kwargs["timeout"] is None
        clock[0] += 100
        return json.dumps(rows)

    output = io.StringIO()
    with patch.object(updater, "_check_once", side_effect=plugin), \
            patch.object(updater, "bounded_run", side_effect=run), \
            patch.object(updater.time, "monotonic", side_effect=lambda: clock[0]), \
            patch.object(updater, "_logging_maintenance"), redirect_stdout(output):
        assert updater.check(adapter) == 0
    receipt = json.loads(output.getvalue())
    assert receipt["result"] == receipt["plugin_result"]["result"] == "switched"
    assert receipt["feed_results"] == rows
    assert not genstate.failed_path(adapter).exists()
    assert len(calls) == 1


@pytest.mark.parametrize("extra", [
    dict(receipt_status="failed"), dict(cleanup_status="failed"),
    dict(status="partial", metadata_committed=True, failed_stage="material-promotion"),
])
def test_final_stdout_and_exit_preserve_committed_feed_failure(adapter, extra):
    row = dict(repository="fixture/public", status="synced", commit="committed")
    row.update(extra)

    def plugin(config, *_args, **_kwargs):
        genstate.write_status(dict(result="current"), config)
        print('{"result":"current"}')
        return 0

    out = io.StringIO()
    with patch.object(updater, "_check_once", side_effect=plugin), \
            patch.object(updater, "bounded_run", return_value=json.dumps([row])) as run, \
            patch.object(updater, "_logging_maintenance"), redirect_stdout(out):
        assert updater.check(adapter) == 1
    result = json.loads(out.getvalue())
    assert result["result"] == "degraded"
    assert result["plugin_result"]["result"] == "current"
    assert result["feed_results"] == [row]
    run.assert_called_once()


def test_overlapping_tick_is_excluded_without_new_work(adapter):
    with genstate.check_lock(adapter), patch.object(updater, "_check_once") as once:
        assert updater.check(adapter) == 0
    once.assert_not_called()


def test_feed_cleanup_and_record_failures_preserve_committed_result(adapter):
    row = dict(repository="fixture/public", status="synced", commit="retained")

    def plugin(config, *_args, **_kwargs):
        genstate.write_status(dict(result="current"), config)
        print('{"result":"current"}')
        return 0

    def sync(*_args, receipt, **_kwargs):
        receipt.update(completed=True, returncode=0, cleanup_failed=True,
                       cleanup=[dict(stage="terminate_owned_tree", error_type="PermissionError")])
        return json.dumps([row])

    out = io.StringIO()
    with patch.object(updater, "_check_once", side_effect=plugin), \
            patch.object(updater, "bounded_run", side_effect=sync) as run, \
            patch.object(updater, "write_status", side_effect=PermissionError("fixture")), \
            patch.object(updater, "_logging_maintenance"), redirect_stdout(out):
        assert updater.check(adapter) == 1
    observed = json.loads(out.getvalue())
    assert observed["feed_results"] == [row]
    assert observed["feed_process"]["completed"] is True
    assert observed["feed_record_error"] == observed["result_record_error"] == "PermissionError"
    assert observed["plugin_result"]["result"] == "current"
    run.assert_called_once()


def test_cancelled_staging_is_not_a_failed_candidate(adapter):
    adapter["update_channel"] = "main"
    with patch.object(updater, "resolve_main", return_value="b" * 40), \
            patch.object(updater, "stage_generation",
                         side_effect=bounded.CommandCancelled("caller cancelled")):
        assert updater._check_once(adapter, None, force=False, build=None,
                                   idle=None, install=None, lock_timeout=1) == 1
    assert genstate.read_status(adapter)["result"] == "cancelled"
    assert not genstate.failed_path(adapter).exists()


def test_build_has_no_default_elapsed_deadline(tmp_path):
    (tmp_path / "runtime-requirements.txt").write_text("fixture==1\n")
    with patch.object(updater, "bounded_run") as run:
        updater.build_runtime(tmp_path, None)
    assert len(run.call_args_list) == 2
    assert all(call.kwargs["timeout"] is None for call in run.call_args_list)


def test_native_mutations_have_no_implicit_deadline_but_readback_is_bounded(tmp_path):
    receipt = {}
    with patch.object(native_claude, "sync_stable_package", return_value=tmp_path), \
            patch.object(native_claude, "marketplace_list", return_value=[]), \
            patch.object(native_claude, "marketplace_add") as add, \
            patch.object(native_claude, "plugin_list", return_value=[]) as listing, \
            patch.object(native_claude, "plugin_install", return_value={}) as install, \
            patch.object(native_claude, "verify_readback",
                         return_value=dict(plugin={}, marketplace={})):
        native_claude.install_and_verify(tmp_path, "0.0.1", stable=tmp_path, receipt=receipt)
    assert add.call_args.kwargs["timeout"] is None
    assert install.call_args.kwargs["timeout"] is None
    assert all(call.kwargs["timeout"] == 20 for call in listing.call_args_list)
    assert receipt["outcome"] == "verified"


@pytest.mark.parametrize("mutation,readback", [(False, False), (True, False), (True, True)])
def test_native_interruption_retains_before_unknown_and_verified(adapter, tmp_path, mutation, readback):
    package = tmp_path / "package"
    (package / ".claude-plugin").mkdir(parents=True)
    (package / ".claude-plugin/plugin.json").write_text('{"version":"0.0.1"}')
    launcher = genstate.launch_dir(adapter) / ("b" * 40) / updater.LAUNCHER
    launcher.parent.mkdir(parents=True)
    launcher.write_text("")

    def interrupted(*args, receipt, **kwargs):
        receipt.update(mutation_attempted=mutation, outcome="unknown" if mutation else "not-started")
        raise bounded.CommandCancelled("fixture cancellation")

    with patch.object(native_claude, "install_and_verify", side_effect=interrupted) as install, \
            patch.object(updater, "_readback", return_value={"plugin": {"version": "0.0.1"}},
                         side_effect=None if readback else RuntimeError("unconfirmed")) as verify:
        if mutation and readback:
            result = updater.native_install(dict(adapter, sha="b" * 40), package, None)
            assert result["native_receipt"]["interrupted"] is True
        else:
            with pytest.raises((bounded.CommandCancelled, updater.CheckFailed)):
                updater.native_install(dict(adapter, sha="b" * 40), package, None)
    install.assert_called_once()
    assert verify.call_count == int(mutation)
    observed = genstate.read_status(adapter)["native_install"]
    assert observed["outcome"] == ("not-started" if not mutation else
                                    "verified" if readback else "unknown")
    if mutation and not readback:
        with patch.object(updater, "resolve_main") as resolve:
            assert updater._check_once(adapter, None, force=True, build=None,
                                       idle=None, install=None, lock_timeout=1) == 1
        resolve.assert_not_called()


def test_unknown_native_mutation_never_rolls_back_or_restarts(adapter, tmp_path):
    current = genstate.read_current(adapter)
    generation = tmp_path / "candidate"
    generation.mkdir()
    config = generation / "adapter.json"
    config.write_text(json.dumps(adapter))
    error = updater.CheckFailed("unknown native result")
    error.native_outcome = "unknown"
    with patch.object(updater, "validate_generation_state"), \
            patch.object(capture_config, "prepare_store"), \
            patch.object(updater, "_rollback_native") as rollback, \
            patch.object(updater, "_restore_stopped_service") as restore:
        with pytest.raises(updater.CheckFailed, match="unknown native result"):
            updater.switch(adapter, current, generation, Path(sys.executable), config,
                           "b" * 40, None, lambda *_: {"idle": True, "service": "absent"},
                           lambda *_: (_ for _ in ()).throw(error), 1)
    rollback.assert_not_called()
    restore.assert_not_called()
    assert genstate.read_current(adapter) == current


@pytest.mark.parametrize("phase", ["before", "unknown", "verified"])
def test_native_record_failure_cannot_change_mutation_facts(adapter, tmp_path, phase):
    current = genstate.read_current(adapter)
    generation = tmp_path / "candidate"
    package = generation / "host-package"
    (package / ".claude-plugin").mkdir(parents=True)
    (package / ".claude-plugin/plugin.json").write_text('{"version":"0.0.1"}')
    config = generation / "adapter.json"
    config.write_text(json.dumps(adapter))
    launcher = genstate.launch_dir(adapter) / ("b" * 40) / updater.LAUNCHER
    launcher.parent.mkdir(parents=True)
    launcher.write_text("")
    real_record = updater._record
    calls = []

    def record(selected, **fields):
        native = fields.get("native_install", {})
        if native and (phase == "before" or native.get("stage") != "before-native-call"):
            raise PermissionError("fixture record failure")
        return real_record(selected, **fields)

    def native(*args, receipt, **kwargs):
        calls.append("native")
        receipt.update(outcome=phase, stage="after-native", mutation_attempted=True)
        if phase == "unknown":
            raise RuntimeError("mutation transport interrupted")
        return {}

    with patch.object(updater, "validate_generation_state"), \
            patch.object(capture_config, "prepare_store"), \
            patch.object(native_claude, "install_and_verify", side_effect=native), \
            patch.object(updater, "_readback", side_effect=RuntimeError("readback unavailable")), \
            patch.object(updater, "_record", side_effect=record), \
            patch.object(updater, "_rollback_native") as rollback:
        if phase == "verified":
            observed = updater.switch(adapter, current, generation, Path(sys.executable), config,
                                      "b" * 40, None, lambda *_: {"idle": True, "service": "absent"},
                                      updater.native_install, 1)
            assert observed["outcome"] == "verified"
            assert observed["record_error_type"] == "PermissionError"
            final = updater._applied_status(adapter, observed, result="switched", current_sha="b" * 40)
            assert final["result"] == "degraded"
            assert final["native_install"]["outcome"] == "verified"
            assert genstate.read_current(adapter)["sha"] == "b" * 40
        else:
            with pytest.raises(updater.CheckFailed) as caught:
                updater.switch(adapter, current, generation, Path(sys.executable), config,
                               "b" * 40, None, lambda *_: {"idle": True, "service": "absent"},
                               updater.native_install, 1)
            assert caught.value.native_outcome == ("not-started" if phase == "before" else "unknown")
            assert caught.value.native_receipt["record_error_type"] == "PermissionError"
            assert genstate.read_current(adapter) == current
    rollback.assert_not_called()
    assert len(calls) == int(phase != "before")


@pytest.mark.parametrize("outcome", ["unknown", "verified"])
def test_rollback_appends_to_latest_native_fact(adapter, tmp_path, outcome):
    previous = genstate.read_current(adapter)
    package = tmp_path / "previous-package"
    package.mkdir()
    previous["native_package"] = str(package)
    genstate.write_status(dict(native_install=dict(outcome="verified", sha="b" * 40)), adapter)

    def install(selected, *_args, **_kwargs):
        receipt = dict(outcome=outcome, sha="a" * 40)
        updater._record(selected, native_install=receipt)
        if outcome == "unknown":
            raise updater._native_failure(RuntimeError("rollback interrupted"), receipt)
        return dict(native_receipt=receipt)

    assert updater._rollback_native(adapter, previous, install, None)["restored"] is (outcome == "verified")
    observed = genstate.read_status(adapter)
    assert observed["native_install"] == dict(outcome=outcome, sha="a" * 40)
    assert "rollback" in observed


@pytest.mark.parametrize("rollback_outcome", ["unknown", "verified"])
@pytest.mark.parametrize("rollback_record_failure", [False, True])
def test_pointer_failure_delivers_latest_rollback_fact_through_outer_status(
        adapter, tmp_path, rollback_outcome, rollback_record_failure):
    current = genstate.read_current(adapter)
    previous_generation = tmp_path / "previous"
    previous_package = previous_generation / "host-package"
    previous_package.mkdir(parents=True)
    current["generation"] = str(previous_generation)
    genstate.write_current(current, adapter)
    generation = tmp_path / "candidate"
    generation.mkdir()
    config = generation / "adapter.json"
    config.write_text(json.dumps(adapter))
    calls = []
    write_current = updater.write_current
    record = updater._record

    def install(selected, *_args):
        sha = selected["sha"]
        outcome = "verified" if sha == "b" * 40 else rollback_outcome
        receipt = dict(outcome=outcome, sha=sha)
        calls.append(sha)
        record(adapter, native_install=receipt)
        if outcome == "unknown":
            raise updater._native_failure(RuntimeError("rollback transport uncertain"), receipt)
        return dict(native_receipt=receipt)

    def pointer(value, selected):
        if value["sha"] == "b" * 40:
            raise PermissionError("original pointer save failure")
        return write_current(value, selected)

    def save(selected, **fields):
        if rollback_record_failure and "rollback" in fields:
            raise OSError("rollback record failure")
        return record(selected, **fields)

    with patch.object(updater, "validate_generation_state"), \
            patch.object(capture_config, "prepare_store"), \
            patch.object(updater, "write_current", side_effect=pointer), \
            patch.object(updater, "_record", side_effect=save):
        with pytest.raises(PermissionError, match="original pointer save failure") as caught:
            updater.switch(adapter, current, generation, Path(sys.executable), config,
                           "b" * 40, None, lambda *_: {"idle": True, "service": "absent"}, install, 1)
        out = io.StringIO()
        with redirect_stdout(out):
            assert updater._native_failed_status(adapter, "b" * 40, caught.value) == 1
    assert calls == ["b" * 40, "a" * 40]
    expected = caught.value.native_receipt
    assert expected["sha"] == "a" * 40 and expected["outcome"] == rollback_outcome
    assert json.loads(out.getvalue())["native_install"] == expected
    assert genstate.read_status(adapter)["native_install"] == expected


@pytest.mark.parametrize("entry", ["updater", "setup"])
def test_late_cancel_preserves_real_completed_feed_output(adapter, entry):
    import setup
    cancel = threading.Event()
    rows = [dict(repository="fixture/public", status="synced", commit="committed-before-cancel")]
    reader_name = "_run_posix" if bounded.POSIX else "_run_windows"
    real_reader = getattr(bounded, reader_name)

    def late_cancel(*args, **kwargs):
        result = real_reader(*args, **kwargs)
        cancel.set()
        return result

    def harmless(*args, **kwargs):
        return bounded.run([sys.executable, "-c", "print(" + repr(json.dumps(rows)) + ")"],
                           receipt=kwargs["receipt"], cancel=cancel)

    with patch.object(bounded, reader_name, side_effect=late_cancel):
        if entry == "updater":
            with patch.object(updater, "bounded_run", side_effect=harmless):
                assert updater._feed_sync(adapter, None) is False
            result = genstate.read_status(adapter)
            assert result["feed_results"] == rows
            assert result["feed_process"]["completed"] is True
            assert result["feed_process"]["returncode"] == 0
            assert result["feed_sync"] == "cancelled"
        else:
            with patch.object(setup, "run", side_effect=harmless):
                result = setup.sync_public_feed(sys.executable, Path("fixture-engine.json"))
            assert result["results"] == rows
            assert result["operation_completed"] is True
            assert result["returncode"] == 0
            assert result["cancelled_after_completion"] is True
            assert result["result"] == "sync_failed"


def _alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return code.value == 259
    result = subprocess.run(["ps", "-p", str(pid), "-o", "state="],
                            capture_output=True, text=True)
    return result.returncode == 0 and not result.stdout.strip().startswith("Z")


def test_scoped_cancel_reaps_owned_child_and_grandchild(tmp_path):
    marker = tmp_path / "pids.json"
    program = (
        "import os,subprocess,sys,time,json;"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
        f"open({str(marker)!r},'w').write(json.dumps([os.getpid(),p.pid]));"
        "time.sleep(30)"
    )
    cancel = threading.Event()
    def stop():
        limit = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < limit:
            time.sleep(.02)
        cancel.set()
    thread = threading.Thread(target=stop)
    thread.start()
    with bounded.operation(cancel), pytest.raises(bounded.CommandCancelled):
        bounded.run([sys.executable, "-c", program])
    thread.join()
    pids = json.loads(marker.read_text())
    limit = time.monotonic() + 3
    while any(_alive(pid) for pid in pids) and time.monotonic() < limit:
        time.sleep(.02)
    assert not any(_alive(pid) for pid in pids)


@pytest.mark.skipif(os.name != "posix", reason="POSIX owner pipe; Windows uses owned Job")
def test_hard_owner_death_reaps_child_and_pipe_holder(tmp_path):
    marker = tmp_path / "pids.json"
    target = (
        "import os,subprocess,sys,time,json;"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
        f"open({str(marker)!r},'w').write(json.dumps([os.getpid(),p.pid]));time.sleep(30)"
    )
    parent = (
        f"import sys;sys.path.insert(0,{str(SCRIPTS)!r});import bounded;"
        "scope=bounded.operation();scope.__enter__();"
        f"bounded.run([sys.executable,'-c',{target!r}])"
    )
    process = subprocess.Popen([sys.executable, "-c", parent],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        limit = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < limit:
            time.sleep(.02)
        pids = json.loads(marker.read_text())
        assert all(_alive(pid) for pid in pids)
        process.kill()
        process.wait(timeout=3)
        limit = time.monotonic() + 3
        while any(_alive(pid) for pid in pids) and time.monotonic() < limit:
            time.sleep(.02)
        assert not any(_alive(pid) for pid in pids)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
