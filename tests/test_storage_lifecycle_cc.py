"""Cross-platform storage lifecycle regressions for the 0.1.25 release.

Every test runs under `conftest.py`'s isolated HOME/XDG/TMPDIR/UV cache plus a
synthetic MindIE data home, so no host generation, plugin-data release or
SQLite bind store is touched.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sysconfig
import subprocess
import sys
import time


from support import SCRIPTS

sys.path.insert(0, str(SCRIPTS))

import bootstrap
import bounded
import storage_reclaim
import storage_releases
import storage_lifecycle as lifecycle


def _binding(tmp_path, version="0.1.25"):
    base = tmp_path / "mindie-agent"
    state = base / "cc" / "adapter-state"
    config_home = tmp_path / "config"
    config_home.mkdir(parents=True, exist_ok=True)
    runtime = base / "runtimes" / "cc" / "current"
    (runtime / "bin").mkdir(parents=True, exist_ok=True)
    python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("# fake interpreter\n", encoding="utf-8")
    adapter = state / "installations" / "current" / "cc.adapter.json"
    adapter.parent.mkdir(parents=True, exist_ok=True)
    adapter.write_text("{}\n", encoding="utf-8")
    base_config = config_home / "cc.json"
    base_config.write_text(json.dumps({
        "schema": "mindie-cc-installation/1", "facade_protocol": 1,
        "base_config": str(base_config), "state_dir": str(state),
        "python": str(python), "runtime_scripts": str(SCRIPTS),
        "adapter_config": str(adapter),
        "component_source": str(tmp_path / "source"),
        "product_validation": {"native_version": version},
    }), encoding="utf-8")
    return {
        "schema": "mindie-cc-installation/1", "facade_protocol": 1,
        "base_config": str(base_config), "state_dir": str(state),
        "python": str(python), "runtime_scripts": str(SCRIPTS),
        "adapter_config": str(adapter), "config": str(adapter),
        "component_source": str(tmp_path / "source"),
        "product_validation": {"native_version": version},
    }


def _age(path, seconds=7200.0):
    stamp = time.time() - seconds
    path = Path(path)
    for root, directories, files in os.walk(path):
        os.utime(root, (stamp, stamp))
        for name in files:
            try:
                os.utime(Path(root) / name, (stamp, stamp))
            except OSError:
                pass
        for name in directories:
            try:
                os.utime(Path(root) / name, (stamp, stamp))
            except OSError:
                pass


def _generation(root, name, *, components=True):
    entry = root / name
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "runtime").mkdir(exist_ok=True)
    if components:
        (entry / "components.json").write_text(
            json.dumps({"generation_key": name}), encoding="utf-8")
    return entry


def test_legacy_runtimes_installations_and_component_generations_are_swept(tmp_path):
    binding = _binding(tmp_path)
    state = Path(binding["state_dir"])
    runtimes = Path(binding["python"]).parents[2]
    components = state / "component-runtimes"
    installations = state / "installations"
    current = runtimes / "current"
    current.mkdir(parents=True, exist_ok=True)
    current.with_suffix(".ready.json").write_text("{}", encoding="utf-8")
    (current / "bin").mkdir(exist_ok=True)
    (current / "bin" / "python").write_text("", encoding="utf-8")
    prev = runtimes / "previous"
    prev.mkdir()
    prev.with_suffix(".ready.json").write_text("{}", encoding="utf-8")
    old = [runtimes / ("legacy-" + str(index)) for index in range(3)]
    for entry in old:
        entry.mkdir()
        entry.with_suffix(".ready.json").write_text("{}", encoding="utf-8")
    for entry in old + [prev, current]:
        _age(entry, 7200 + (3600 if entry in old else 0))
    _age(installations / "current", 20000)
    kept_install = []
    for index in range(4):
        entry = installations / ("generation-" + str(index))
        entry.mkdir(parents=True)
        _age(entry, 7200 + (1000 if index < 2 else 0))
        kept_install.append(entry)
    kept_components = []
    for index in range(4):
        entry = _generation(components, "generation-" + str(index))
        _age(entry, 7200 + (1000 if index < 2 else 0))
        kept_components.append(entry)

    report = lifecycle.sweep_installations(binding, force=True, budget_seconds=20.0)

    assert report["status"] in {"completed", "partial"}
    for entry in old:
        assert not entry.exists(), entry
    assert current.is_dir() and prev.is_dir()
    assert not kept_install[0].exists() and not kept_install[1].exists()
    assert kept_install[2].is_dir() and kept_install[3].is_dir()
    assert not kept_components[0].exists() and not kept_components[1].exists()
    assert kept_components[2].is_dir() and kept_components[3].is_dir()


def _write_binds(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    connection.execute(
        "CREATE TABLE binds(tool_use_id TEXT PRIMARY KEY, session TEXT NOT NULL, "
        "tool TEXT NOT NULL, local_tool TEXT NOT NULL, args_digest TEXT NOT NULL, "
        "call_digest TEXT NOT NULL, cwd TEXT, transcript TEXT, status TEXT NOT NULL, "
        "created REAL NOT NULL, updated REAL NOT NULL)")
    connection.executemany("INSERT INTO binds VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
    connection.commit()
    connection.close()


def _hold_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    if os.name == "nt":
        import msvcrt

        os.write(descriptor, b"\0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return descriptor


def _release_lock(descriptor):
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)
    os.close(descriptor)


def test_expired_bind_rows_and_dead_remote_sessions_are_pruned(tmp_path):
    binding = _binding(tmp_path)
    state = Path(binding["state_dir"])
    now = time.time()
    old = now - 8 * 24 * 3600
    rows = [
        ("old-done", "dead", "tool", "local", "a", "c", None, None, "succeeded", old, old),
        ("old-failed", "dead", "tool", "local", "a", "c", None, None, "failed", old, old),
        ("old-bound", "pending", "tool", "local", "a", "c", None, None, "bound", old, old),
        ("old-claimed", "pending", "tool", "local", "a", "c", None, None, "claimed", old, old),
        ("new-done", "recent", "tool", "local", "a", "c", None, None, "succeeded", now, now),
    ]
    _write_binds(state / "mcp-binds.sqlite3", rows)
    remote = state / "remote"
    dead = remote / "dead"
    dead.mkdir(parents=True)
    (dead / "endpoints" / "one").mkdir(parents=True)
    (dead / "endpoints" / "one" / "record.json").write_text(
        json.dumps({"status": "completed"}), encoding="utf-8")
    unknown = remote / "unknown"
    unknown.mkdir(parents=True)
    (unknown / "job.json").write_text(
        json.dumps({"error_details": {"submission_state": "uncertain"}}), encoding="utf-8")
    pending = remote / "pending"
    pending.mkdir(parents=True)
    (pending / "job.json").write_text(json.dumps({"status": "running"}), encoding="utf-8")
    locked = remote / "locked"
    lock = locked / "locks" / "endpoint.lock"
    lock.parent.mkdir(parents=True)
    descriptor = _hold_lock(lock)
    recent = remote / "recent"
    recent.mkdir(parents=True)
    (recent / "job.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    for entry in (dead, unknown, pending, locked):
        _age(entry, 8 * 24 * 3600)
    try:
        report = lifecycle.prune_sessions(binding, budget_seconds=20.0)
    finally:
        _release_lock(descriptor)

    assert report["binds_removed"] == 2
    connection = sqlite3.connect(str(state / "mcp-binds.sqlite3"))
    try:
        remaining = {row[0] for row in connection.execute("SELECT tool_use_id FROM binds")}
    finally:
        connection.close()
    assert remaining == {"old-bound", "old-claimed", "new-done"}
    assert not dead.exists()
    assert unknown.is_dir() and pending.is_dir() and locked.is_dir() and recent.is_dir()


def test_release_entry_rebinds_to_merged_interpreter_and_old_roots_retire(tmp_path, monkeypatch):
    binding = _binding(tmp_path)
    plugin_data = tmp_path / "plugin-data"
    releases = plugin_data / "releases"
    old_python = str(Path(binding["python"]))
    for version in ("0.1.20", "0.1.24", "0.1.25", "0.1.26"):
        entry = releases / version / "entry.py"
        entry.parent.mkdir(parents=True)
        entry.write_text(
            "import os\n"
            f"exec_or_wait(['{old_python}', '-I', 'launch.py', *sys.argv[1:]], {{}})\n",
            encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(plugin_data))
    merged = dict(binding, python=str(tmp_path / "merged" / "bin" / "python"),
                  runtime_scripts=str(tmp_path / "merged" / "scripts"))

    refreshed = lifecycle.refresh_release_entry(merged)

    assert refreshed["status"] == "refreshed"
    active = (releases / "0.1.25" / "entry.py").read_text(encoding="utf-8")
    assert str(merged["python"]) in active
    assert old_python not in active
    assert str(merged["runtime_scripts"]) in active
    assert old_python in (releases / "0.1.24" / "entry.py").read_text(encoding="utf-8")
    assert lifecycle.refresh_release_entry(merged)["status"] == "unchanged"

    pruned = lifecycle.prune_release_entries(merged)

    assert pruned["reclaimed_dirs"] == 1
    assert not (releases / "0.1.20").exists()
    assert (releases / "0.1.24").is_dir()
    assert (releases / "0.1.25").is_dir()
    assert (releases / "0.1.26").is_dir()


def test_permission_denied_release_retirement_keeps_the_tree_and_defers(tmp_path, monkeypatch):
    binding = _binding(tmp_path)
    plugin_data = tmp_path / "plugin-data"
    old = plugin_data / "releases" / "0.1.20"
    old.mkdir(parents=True)
    (old / "entry.py").write_text("# old\n", encoding="utf-8")
    (plugin_data / "releases" / "0.1.25").mkdir()

    import mindie_knowledge.storage as knowledge_storage

    monkeypatch.setattr(knowledge_storage, "live_process_prefixes",
                        lambda **kwargs: (set(), True))
    monkeypatch.setattr(knowledge_storage, "is_path_used_by_live_process",
                        lambda *args, **kwargs: False)

    def denied(*args, **kwargs):
        raise PermissionError("NTFS sharing violation")

    monkeypatch.setattr(knowledge_storage, "retire_directory", denied)
    report = storage_releases.prune_release_entries(
        binding, plugin_data=str(plugin_data), keep_versions=1)

    assert report["deferred_in_use"] == 1
    assert (old / "entry.py").is_file()
    assert not list((plugin_data / "releases").glob(".trash-*"))


def test_plugin_upgrade_with_component_floor_builds_one_runtime(tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    current_python = tmp_path / "current" / "bin" / "python"
    current_python.parent.mkdir(parents=True)
    current_python.write_text("", encoding="utf-8")
    config = tmp_path / "cc.json"
    config.write_text(json.dumps({
        "component_source": str(tmp_path / "installed"),
        "component_commands": {"sim": [str(current_python), "-m", "mindie_sim"]},
        "python": str(current_python),
        "state_dir": str(tmp_path / "data" / "cc" / "adapter-state"),
        "base_config": str(config),
        "product_validation": {"source_sha256": "old"},
    }), encoding="utf-8")
    monkeypatch.setenv("MINDIE_CC_CONFIG", str(config))
    merged = {"python": str(tmp_path / "merged" / "bin" / "python"),
              "scripts": str(tmp_path / "merged" / "scripts")}
    commands = []

    def fake_run(stage, command, **kwargs):
        commands.append((stage, command))
        return json.dumps(merged)

    monkeypatch.setattr(bootstrap, "_run", fake_run)
    monkeypatch.setattr(bootstrap, "_baseline_runtime",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("baseline must not be built")))

    python, scripts = bootstrap._ensure_runtime(source, prepare=True)

    assert (python, scripts) == (Path(merged["python"]), Path(merged["scripts"]))
    assert len(commands) == 1
    stage, command = commands[0]
    assert stage == "component-floor"
    assert command[0] == str(current_python)
    assert command[3].endswith("component_update.py")
    assert command[-1] == str(current_python)


def test_component_floor_not_needed_builds_only_the_baseline(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    current_python = tmp_path / "current" / "python"
    current_python.parent.mkdir(parents=True)
    current_python.write_text("", encoding="utf-8")
    config = tmp_path / "cc.json"
    config.write_text(json.dumps({
        "component_source": str(tmp_path),
        "python": str(current_python),
        "state_dir": str(tmp_path / "data" / "cc" / "adapter-state"),
        "base_config": str(config),
        "product_validation": {"source_sha256": "old"},
    }), encoding="utf-8")
    monkeypatch.setenv("MINDIE_CC_CONFIG", str(config))
    baseline_python = tmp_path / "baseline" / "bin" / "python"
    monkeypatch.setattr(bootstrap, "_run", lambda *args, **kwargs: "null")
    monkeypatch.setattr(bootstrap, "_baseline_runtime",
                        lambda *args, **kwargs: (baseline_python, tmp_path / "scripts"))

    python, scripts = bootstrap._ensure_runtime(source, prepare=True)

    assert python == baseline_python
    assert scripts == tmp_path / "scripts"


def test_missing_current_interpreter_builds_baseline_then_component_floor(tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    config = tmp_path / "cc.json"
    config.write_text(json.dumps({
        "component_source": str(tmp_path),
        "python": str(tmp_path / "missing" / "python"),
        "state_dir": str(tmp_path / "data" / "cc" / "adapter-state"),
        "base_config": str(config),
        "product_validation": {"source_sha256": "old"},
    }), encoding="utf-8")
    monkeypatch.setenv("MINDIE_CC_CONFIG", str(config))
    baseline_python = tmp_path / "baseline" / "bin" / "python"
    baseline_python.parent.mkdir(parents=True)
    baseline_python.write_text("", encoding="utf-8")
    calls = []
    monkeypatch.setattr(bootstrap, "_baseline_runtime",
                        lambda *args, **kwargs: (baseline_python, tmp_path / "scripts"))

    def fake_run(stage, command, **kwargs):
        calls.append(command)
        return json.dumps({"python": str(tmp_path / "merged" / "python"),
                           "scripts": str(tmp_path / "merged" / "scripts")})

    monkeypatch.setattr(bootstrap, "_run", fake_run)
    python, _scripts = bootstrap._ensure_runtime(source, prepare=True)

    assert python == tmp_path / "merged" / "python"
    assert len(calls) == 1 and calls[0][0] == str(baseline_python)


def test_uv_cache_moves_only_when_the_default_crosses_the_runtime_device(tmp_path, monkeypatch):
    runtime = tmp_path / "runtimes" / "cc" / "key"
    runtime.parent.mkdir(parents=True)
    runtime.mkdir()

    def device(path):
        return 2 if str(path).startswith(str(runtime.parent)) else 1

    monkeypatch.setattr(bootstrap, "_device", device)
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    env = bootstrap.same_volume_uv_cache(runtime, {})
    assert env["UV_CACHE_DIR"] == str(runtime.parent / ".uv-cache")

    explicit = bootstrap.same_volume_uv_cache(runtime, {"UV_CACHE_DIR": "/explicit"})
    assert explicit["UV_CACHE_DIR"] == "/explicit"
    monkeypatch.setattr(bootstrap, "_device", lambda path: 2)
    same = bootstrap.same_volume_uv_cache(runtime, {})
    assert "UV_CACHE_DIR" not in same


def test_windows_runtime_key_names_keep_a_legacy_64_character_spelling():
    key = "a" * 64
    assert bootstrap.runtime_key_names(key, windows=True) == (key[:24], key)
    assert bootstrap.runtime_key_names(key, windows=False) == (key,)


def test_completed_legacy_runtime_directory_is_read_without_rebuilding(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    key = "b" * 64
    runtimes = tmp_path / "runtimes"
    runtime = runtimes / key
    (runtime / "bin").mkdir(parents=True)
    python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("", encoding="utf-8")
    expected = dict(key=key, python=str(python), source_sha256="source",
                    lock_sha256=hashlib.sha256(b"version = 1\n").hexdigest(),
                    abi=sys.implementation.cache_tag,
                    platform=sysconfig.get_platform())
    runtime.with_suffix(".ready.json").write_text(json.dumps(expected), encoding="utf-8")
    monkeypatch.setattr(bootstrap, "runtime_root", lambda: runtimes)
    monkeypatch.setattr(bootstrap, "runtime_key", lambda source: key)
    monkeypatch.setattr(bootstrap, "readback",
                        lambda *args, **kwargs: tmp_path / "scripts")
    monkeypatch.setattr("product_contract.source_identity", lambda source: "source")
    monkeypatch.setattr(bootstrap, "_run",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("uv must not run")))

    selected_python, scripts = bootstrap._baseline_runtime(source, prepare=True)

    assert selected_python == python
    assert scripts == tmp_path / "scripts"


def test_live_lease_protects_a_component_generation_until_the_tree_is_terminated(tmp_path):
    binding = _binding(tmp_path)
    state = Path(binding["state_dir"])
    generation = _generation(state / "component-runtimes", "leased")
    _age(generation, 7200)
    newer = [_generation(state / "component-runtimes", name)
             for name in ("newer-one", "newer-two")]
    for entry in newer:
        _age(entry, 1000)
    ready = tmp_path / "lease-ready"
    program = (
        "import os, sys, time\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from mindie_knowledge.storage import acquire_runtime_lease\n"
        "lease = acquire_runtime_lease(os.environ['MINDIE_TEST_LEASE_DIR'])\n"
        "open(sys.argv[2], 'w').write('ready')\n"
        "time.sleep(60)\n")
    environment = dict(os.environ, MINDIE_TEST_LEASE_DIR=str(generation))
    process = subprocess.Popen(
        [sys.executable, "-c", program, str(SCRIPTS), str(ready)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=environment,
        start_new_session=os.name == "posix")
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), process.communicate(timeout=2)[1].decode(errors="replace")
        from mindie_knowledge.storage import is_lease_held

        assert is_lease_held(generation)
        lifecycle.sweep_installations(binding, force=True, budget_seconds=15.0)
        assert generation.is_dir()
    finally:
        bounded._kill_tree(process)
        process.wait(timeout=10)
        process.stderr.close()
    deadline = time.monotonic() + 10
    while True:
        from mindie_knowledge.storage import is_lease_held

        if not is_lease_held(generation) or time.monotonic() >= deadline:
            break
        time.sleep(0.02)
    assert not is_lease_held(generation)
    _age(generation, 7200)
    lifecycle.sweep_installations(binding, force=True, budget_seconds=15.0)
    assert not generation.exists()


def test_cleanup_fields_never_replace_a_completed_result_status():
    completed = {"status": "activated", "switched": True}
    assert lifecycle.cleanup_fields({"status": "completed"}) == {}
    lifecycle.attach_cleanup(completed, {
        "status": "partial", "partial": True, "warnings": ["budget exhausted"],
        "deferred_in_use": 1})
    assert completed["status"] == "activated" and completed["switched"] is True
    assert completed["cleanup_warning"]
    assert completed["cleanup"]["deferred_in_use"] == 1


def test_expired_session_state_cleanup_is_reachable_from_the_native_entry(monkeypatch):
    import entry_state

    seen = {}

    def fake_tick(**kwargs):
        seen.update(kwargs)
        return {"status": "completed"}

    monkeypatch.setattr(lifecycle, "maintenance_tick", fake_tick)
    assert entry_state.maintain_session_state() == {"status": "completed"}
    assert seen["budget_seconds"] == 1.0


def _remote_session(state, name, payload, *, age=8 * 24 * 3600):
    session = state / "remote" / name
    session.mkdir(parents=True)
    if payload is not None:
        (session / "job.json").write_text(json.dumps(payload), encoding="utf-8")
    _age(session, age)
    return session


def _job_record(state="succeeded", submission="acknowledged"):
    """The real `remote-dev.job.v1` record, including its nested authorization."""
    record = {
        "schema_version": "remote-dev.job.v1", "job_id": "job-1",
        "description": None, "target": {"cwd": "/remote"},
        "connection": {"cwd": "/remote"}, "command_preview": "sleep 1",
        "cwd": "/remote", "remote_dir": "/remote/.remote-dev/jobs/job-1",
        "started_at": "2026-01-01T00:00:00Z",
        "authorization": {"token": "private-token", "job_id": "job-1"},
    }
    if state is not None:
        record["state"] = state
    if submission is not None:
        record["error_details"] = {"submission_state": submission}
    return record


def test_runtime_leases_hold_a_shared_lease_on_the_managed_generation(tmp_path):
    from mindie_knowledge.storage import is_lease_held

    binding = _binding(tmp_path)
    generation = tmp_path / "data" / "mindie-agent" / "runtimes" / "cc" / "leased"
    python = generation / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    binding["python"] = str(python)

    with lifecycle.runtime_leases(binding) as leases:
        assert leases, "runtime_leases must acquire the managed owning directory"
        assert is_lease_held(generation)
    assert not is_lease_held(generation)


def test_remote_dev_job_schema_terminal_and_unresolved_states_decide_reclamation(tmp_path):
    binding = _binding(tmp_path)
    state = Path(binding["state_dir"])
    expired = _remote_session(state, "expired", _job_record("succeeded"))
    timed_out = _remote_session(state, "timed-out", _job_record("timeout"))
    running = _remote_session(state, "running", _job_record("running"))
    unknown = _remote_session(state, "unknown", _job_record("unknown"))
    missing = _remote_session(state, "missing", _job_record(None, None))

    report = lifecycle.prune_sessions(binding, budget_seconds=20.0)

    assert report["status"] == "completed", report
    assert not expired.exists() and not timed_out.exists()
    assert running.is_dir() and unknown.is_dir() and missing.is_dir()


def test_submission_reply_states_are_resolved_only_when_acknowledged():
    accepted = ("acknowledged", "not_sent", "not_executed")
    for value in accepted:
        assert not storage_reclaim._contains_unresolved(
            _job_record("succeeded", value)), value
    for value in ("uncertain", "unknown", "pending", "submitting", "in_flight",
                  "something_else", None, 0, ["uncertain"]):
        record = _job_record("succeeded", None)
        record["error_details"] = {"submission_state": value}
        assert storage_reclaim._contains_unresolved(record), value
    assert not storage_reclaim._contains_unresolved(_job_record("succeeded", None))
    # The nested authorization block is not a pre-reply job record.
    assert not storage_reclaim._contains_unresolved(
        {"authorization": {"token": "private", "job_id": "job-1"}})


def test_oversized_or_truncated_session_inspection_never_deletes(tmp_path):
    binding = _binding(tmp_path)
    state = Path(binding["state_dir"])
    crowded = _remote_session(state, "crowded", None)
    for index in range(401):
        (crowded / ("entry-%03d" % index)).write_text("", encoding="utf-8")
    _age(crowded)
    huge = _remote_session(state, "huge", None)
    (huge / "job.json").write_text(
        json.dumps({"state": "succeeded", "padding": "x" * (513 * 1024)}),
        encoding="utf-8")
    _age(huge)
    broken = _remote_session(state, "broken", None)
    (broken / "job.json").write_text("{not-json", encoding="utf-8")
    _age(broken)

    report = lifecycle.prune_sessions(binding, budget_seconds=20.0)

    assert report["reclaimed_dirs"] == 0, report
    assert crowded.is_dir() and huge.is_dir() and broken.is_dir()


def test_bind_store_failure_defers_remote_pruning_instead_of_using_empty_protection(
        tmp_path, monkeypatch):
    binding = _binding(tmp_path)
    state = Path(binding["state_dir"])
    session = _remote_session(state, "dead", _job_record("succeeded"))
    (state / "mcp-binds.sqlite3").write_text("", encoding="utf-8")

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(storage_reclaim.sqlite3, "connect", unavailable)
    report = storage_reclaim.prune_sessions(binding, budget_seconds=20.0)

    assert report["status"] == "partial"
    assert report["partial"] is True
    assert report["remote"] == {"status": "deferred", "reason": "binds_unavailable",
                                "removed": 0, "deferred": 0, "partial": True}
    assert session.is_dir()


def test_incomplete_process_snapshot_defers_every_release_candidate(tmp_path, monkeypatch):
    binding = _binding(tmp_path)
    plugin_data = tmp_path / "plugin-data"
    releases = plugin_data / "releases"
    for version in ("0.1.20", "0.1.24", "0.1.25"):
        entry = releases / version
        entry.mkdir(parents=True)
        (entry / "entry.py").write_text("# entry\n", encoding="utf-8")

    import mindie_knowledge.storage as knowledge_storage

    monkeypatch.setattr(knowledge_storage, "live_process_prefixes",
                        lambda **kwargs: (set(), False))
    report = storage_releases.prune_release_entries(
        binding, plugin_data=str(plugin_data), keep_versions=2)

    assert report["status"] == "partial"
    assert report["partial"] is True
    assert report["deferred_in_use"] == 1
    assert "incomplete process snapshot" in report["warnings"]
    assert (releases / "0.1.20").is_dir()
