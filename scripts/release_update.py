"""Select a compatible Release, then use Claude's native catalog and installer.

[Role]
Checks for compatible formal releases in the background and invokes Claude
Code's native plugin updater when a verified release is available.

[Invariants]
- Runs only as an owned background maintenance child.
- Never mutates user consent choices or retries business operations.

[Timeouts & Locks]
- Enforces `CHECK_INTERVAL = 86400`, `FAILURE_BACKOFF = 60`, and
  `ACTIVATION_BACKOFF = 5` under `OperationLock`.

[Side-Effect Policy]
- Writes update check status under `state_dir() / "update"` and delegates plugin
  installation to the native `claude` CLI.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sys
import threading
import time

from bounded import run
from harness import HARNESS, REPOSITORY, compatibility, native_version
from install_state import OperationLock, LockTimeout, atomic_write, read_binding
from paths import MARKETPLACE, PLUGIN_QUALIFIED
from product_contract import native_version as plugin_version, source_identity

CHECK_INTERVAL = 86400
FAILURE_BACKOFF = 60
ACTIVATION_BACKOFF = 5
RECEIPT_NAME = "release-update.json"


class NativePluginDisabled(RuntimeError):
    """A saved native disable takes precedence over background preparation."""


class BackgroundCheck:
    """One facade-owned maintenance child; EOF cancels it with existing ownership."""

    def __init__(self, select_binding):
        self.select_binding = select_binding
        self.cancel = threading.Event()
        self.thread = None
        self.next_check_at = 0.0
        self.start_lock = threading.Lock()

    def start_if_due(self):
        if not self.start_lock.acquire(blocking=False):
            return
        try:
            self._start_if_due()
        finally:
            self.start_lock.release()

    def _start_if_due(self):
        if self.cancel.is_set() or self.thread is not None and self.thread.is_alive():
            return
        now = time.time()
        if now < self.next_check_at:
            return
        try:
            binding = self.select_binding()
            before = read_receipt(binding)
            self.next_check_at = next_check_at(before)
            if now < self.next_check_at:
                return
        except Exception as exc:
            self.next_check_at = now + FAILURE_BACKOFF
            self._record_error(exc)
            return

        def work():
            failed = False
            try:
                env = dict(os.environ, MINDIE_CC_CONFIG=binding["adapter_config"],
                           CLAUDE_CONFIG_DIR=binding["claude_config_dir"])
                env.pop("PYTHONPATH", None)
                run([binding["python"], str(Path(binding["runtime_scripts"]) / "release_update.py")],
                    env=env, cancel=self.cancel, allow_service=True,
                    timeout=1500.0)
            except Exception as exc:
                failed = True
                if not self.cancel.is_set():
                    self._record_error(exc)
            finally:
                now = time.time()
                self.next_check_at = now + FAILURE_BACKOFF
                try:
                    after = read_receipt(binding)
                    if not failed and after != before:
                        self.next_check_at = max(now + ACTIVATION_BACKOFF, next_check_at(after))
                except Exception as exc:
                    if not self.cancel.is_set():
                        self._record_error(exc)

        self.thread = threading.Thread(target=work, name="mindie-release-check")
        self.thread.start()

    @staticmethod
    def _record_error(exc):
        import diagnostic_support
        diagnostic_support.failure("release.update", "background_check", "internal",
                                   exception=exc, reportable=False)

    def close(self):
        self.cancel.set()
        if self.thread is not None:
            self.thread.join()


def receipt_path(binding):
    return Path(binding["state_dir"]) / RECEIPT_NAME


def read_receipt(binding):
    from entry import _read_regular_json

    state, value = _read_regular_json(receipt_path(binding))
    if state == "missing":
        return {}
    if state is not None:
        raise ValueError("release update receipt is unreadable; not replaying installation")
    return value


def _time_field(receipt, key):
    value = receipt.get(key)
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("release check time is invalid: " + key)
    return value


def _pending_install(receipt):
    if receipt.get("installation_complete") is True or receipt.get("selection_closed") is True:
        return False
    if not isinstance(receipt.get("candidate"), dict):
        if receipt.get("native_effect") in {"unknown", "completed"}:
            raise ValueError("unsettled native installation lacks its selected Release")
        return False
    if receipt.get("native_effect") in {"unknown", "completed"}:
        return True
    if (receipt.get("activation_effect") in {"unknown", "incomplete"}
            or receipt.get("preparation_effect") == "unknown"):
        return True
    return (receipt.get("native_effect") in {"not_started", "verified"}
            and receipt.get("status") in {
                "selected", "installing", "failed", "deferred",
                "incomplete", "incompatible_harness",
            })


def next_check_at(receipt):
    success = _time_field(receipt, "last_successful_check")
    failure = _time_field(receipt, "last_failed_check")
    activation = _time_field(receipt, "last_activation_attempt")
    if receipt.get("status") == "native_disabled":
        return (_time_field(receipt, "last_attempt_at") or 0) + FAILURE_BACKOFF
    if _pending_install(receipt):
        next_time = 0 if activation is None else activation + ACTIVATION_BACKOFF
    else:
        next_time = 0 if success is None else success + CHECK_INTERVAL
    if failure is not None and (success is None or failure >= success):
        next_time = max(0 if activation is None else activation + ACTIVATION_BACKOFF,
                        failure + FAILURE_BACKOFF)
    return next_time


def due(binding, now=None):
    return (time.time() if now is None else now) >= next_check_at(read_receipt(binding))


def native_binary(binding):
    from mindie_knowledge.loop.cli import config_at

    engine = config_at(binding["engine_config"])
    native = engine.get("service_launch_context", {}).get("paths", {}).get("MINDIE_CC_BIN")
    if not isinstance(native, str) or not os.path.isabs(native):
        raise ValueError("native Claude executable is not bound to the installation")
    return native


def _native_json(native, args):
    value = json.loads(run(
        [native, "plugin", *args], timeout=120.0, max_output=4 * 1024 * 1024,
    ))
    return value


def installed_plugin(native):
    rows = _native_json(native, ["list", "--json"])
    if not isinstance(rows, list):
        raise ValueError("native plugin list is malformed")
    matches = [row for row in rows if isinstance(row, dict) and row.get("id") == PLUGIN_QUALIFIED]
    if len(matches) != 1 or matches[0].get("scope") != "user":
        raise ValueError("one user-scoped MindIE native installation is required")
    row = matches[0]
    if type(row.get("enabled")) is not bool:
        raise ValueError("native plugin enablement is malformed")
    if row["enabled"] is False:
        raise NativePluginDisabled("native MindIE plugin is disabled")
    if not isinstance(row.get("installPath"), str) or not os.path.isabs(row["installPath"]):
        raise ValueError("native installation path is invalid")
    return row


def _marketplace(native):
    rows = _native_json(native, ["marketplace", "list", "--json"])
    if not isinstance(rows, list):
        raise ValueError("native marketplace list is malformed")
    matches = [row for row in rows if isinstance(row, dict) and row.get("name") == MARKETPLACE]
    if len(matches) != 1:
        raise ValueError("native MindIE marketplace identity is unavailable")
    return matches[0]


def _profile_settings(binding):
    from entry import _read_regular_json

    home = binding.get("claude_config_dir")
    if not isinstance(home, str) or not os.path.isabs(home):
        raise ValueError("native profile is not explicitly bound")
    path = Path(home) / "settings.json"
    state, settings = _read_regular_json(path)
    if state is not None:
        raise ValueError("native settings are unavailable; not replacing them")
    marketplaces = settings.get("extraKnownMarketplaces")
    if not isinstance(marketplaces, dict):
        raise ValueError("native marketplace declarations are malformed")
    existing = marketplaces.get(MARKETPLACE)
    if not isinstance(existing, dict):
        raise ValueError("native MindIE marketplace declaration is unavailable")
    source = existing.get("source")
    if (not isinstance(source, dict) or source.get("source") != "git"
            or source.get("url") != f"https://github.com/{REPOSITORY}.git"):
        raise ValueError("native MindIE marketplace is not the declared official Git source")
    return path, settings, marketplaces, existing


def disable_unfiltered_updates(binding):
    """Use native per-marketplace policy; preserve source, enablement and others."""
    path, settings, marketplaces, existing = _profile_settings(binding)
    if existing.get("autoUpdate") is False:
        return
    marketplaces[MARKETPLACE] = dict(existing, autoUpdate=False)
    atomic_write(path, settings)


def _declare_ref(binding, tag):
    """Change only this marketplace's selected native Git ref and update policy."""
    path, settings, marketplaces, existing = _profile_settings(binding)
    marketplaces[MARKETPLACE] = dict(existing, source=dict(existing["source"], ref=tag),
                                    autoUpdate=False)
    atomic_write(path, settings)


def _record(binding, value):
    atomic_write(receipt_path(binding), value)


def _mutation(binding, state, native, args, stage):
    """Persist uncertain effect before one native call; never erase that fact."""
    state.update(status="installing", stage=stage, native_effect="unknown", process={})
    try:
        _record(binding, state)
    except Exception:
        state.update(native_effect="not_started", process={"started": False})
        raise
    process = {}
    try:
        output = run([native, "plugin", *args], timeout=120.0, check=False, receipt=process,
                     max_output=4 * 1024 * 1024)
    except BaseException as original:
        if process.get("started") is False:
            state["native_effect"] = "not_started"
        state["process"] = process
        try:
            _record(binding, state)
        except Exception as recording:
            state["recording_error"] = type(recording).__name__
            original.add_note("native outcome recording failed: " + type(recording).__name__)
        raise
    state["process"] = process
    if process.get("completed") is True and process.get("cleanup_failed") is False:
        state["native_effect"] = "completed"
    _record(binding, state)
    if (process.get("returncode") != 0 or process.get("completed") is not True
            or process.get("cleanup_failed") is not False):
        raise RuntimeError("native installation did not complete cleanly; inspect its receipt")
    return output


def _verify_catalog(native, candidate):
    row = _marketplace(native)
    metadata = candidate["metadata"]
    if (row.get("source") != "git"
            or row.get("url") != f"https://github.com/{REPOSITORY}.git"
            or row.get("ref") != candidate["tag_name"]):
        raise ValueError("native catalog differs from the selected Release")
    location = row.get("installLocation")
    if not isinstance(location, str) or not os.path.isabs(location):
        raise ValueError("native catalog path is invalid")
    source = Path(location)
    revision = run(["git", "-C", str(source), "rev-parse", "HEAD"], timeout=30.0).strip()
    if revision != metadata["source"]["sha"]:
        raise ValueError("native catalog checkout differs from the selected immutable SHA")
    if compatibility(source) != metadata["compatibility"]:
        raise ValueError("native checkout compatibility differs from its Release declaration")
    if plugin_version(source) != metadata["version"]:
        raise ValueError("native checkout version differs from its Release")
    return source


def install_selected(binding, native, candidate, state):
    """Native records and exact checkout bytes resolve any prior uncertain call."""
    unresolved = state.get("native_effect") in {"unknown", "completed"}
    stage = state.get("stage")
    if unresolved:
        process = state.get("process", {})
        if (process.get("completed") is not True
                or process.get("cleanup_failed") is not False):
            raise RuntimeError("previous native writer is not confirmed settled; not replayed")
    row = installed_plugin(native)
    verified = state.get("native_effect") == "verified" and row.get("version") == candidate["metadata"]["version"]
    if not unresolved and not verified:
        _declare_ref(binding, candidate["tag_name"])
        _mutation(
            binding, state, native,
            ["marketplace", "add", f"https://github.com/{REPOSITORY}.git#{candidate['tag_name']}",
             "--scope", "user"],
            "catalog",
        )
    source = _verify_catalog(native, candidate)
    version = candidate["metadata"]["version"]
    if row.get("version") != version:
        if unresolved and stage == "plugin":
            raise RuntimeError("previous native installation remains unconfirmed; not replayed")
        _mutation(binding, state, native,
                  ["update", PLUGIN_QUALIFIED, "--scope", "user", "--json", "-y"], "plugin")
        row = installed_plugin(native)
    if row.get("version") != version:
        raise ValueError("native installer did not select the expected version")
    installed = Path(row["installPath"])
    if source_identity(installed) != source_identity(source):
        raise ValueError("native installed bytes differ from the selected Git source")
    state.update(stage="installed", native_effect="verified", installed=row)
    _record(binding, state)
    return installed


def _activation_settled(state):
    process = state.get("activation_process", {})
    return process.get("completed") is True and process.get("cleanup_failed") is False


def _preparation_observer(binding, state):
    def observe(stage, process):
        state["preparation_stage"] = stage
        if process is None:
            state.update(preparation_effect="unknown", preparation_process={})
            try:
                _record(binding, state)
            except Exception:
                state.update(preparation_effect="not_started",
                             preparation_process={"started": False})
                raise
            return
        state["preparation_process"] = process
        if process.get("started") is False:
            state["preparation_effect"] = "not_started"
        elif (process.get("completed") is True and process.get("cleanup_failed") is False
              and process.get("returncode", 0) == 0):
            state["preparation_effect"] = "completed"
        else:
            state["preparation_effect"] = "unknown"
        _record(binding, state)
    return observe


def _observe_activation(binding, state):
    """Resolve completed publication or prove no activation effect, without writes."""
    from mindie_knowledge.loop.cli import config_at
    from mindie_knowledge.loop.handoff import _probe
    from mindie_knowledge.loop.lifecycle import inspect_retirement

    if not _activation_settled(state):
        return
    candidate = read_binding(state["prepared"]["config"])
    try:
        with OperationLock(binding).exclusive(timeout=0):
            current = read_binding(binding["base_config"])
            if current == candidate:
                state.update(installation_complete=True, activation_effect="activated",
                             status="activated", activation_observation="canonical_published")
                return
            if current != binding:
                raise ValueError("installation changed while activation was unresolved")
            retired = inspect_retirement(binding["engine_config"])
            if retired is not None:
                return
            engine = config_at(candidate["engine_config"])
            observed = _probe(engine, 5.0, config_path=candidate["engine_config"])
            state["activation_observation"] = observed
            if observed == "absent":
                state["activation_effect"] = "not_started"
            elif observed == "unknown":
                old_engine = config_at(binding["engine_config"])
                old_observed = _probe(old_engine, 5.0, config_path=binding["engine_config"])
                state["activation_old_owner"] = old_observed
                if old_observed == "ready":
                    state["activation_effect"] = "not_started"
    except LockTimeout:
        state["activation_observation"] = "busy"


def _activate(binding, prepared, env, state):
    if state.get("activation_effect") == "unknown" and not _activation_settled(state):
        raise RuntimeError("previous activation writer is not confirmed settled")
    state.update(activation_effect="unknown", activation_process={},
                 last_activation_attempt=time.time())
    try:
        _record(binding, state)
    except Exception:
        state.update(activation_effect="not_started", activation_process={"started": False})
        raise
    process = {}
    try:
        output = run(
            [prepared["python"], str(Path(prepared["runtime_scripts"]) / "setup.py"),
             "--activate", prepared["config"]],
            timeout=120.0, env=env, allow_service=True, receipt=process,
        )
        activated = json.loads(output)
        if not isinstance(activated, dict) or activated.get("status") not in {
            "activated", "current", "deferred", "incomplete",
        }:
            raise ValueError("activation returned an invalid result")
        state["activation"] = activated
        state["activation_process"] = process
        if not _activation_settled(state):
            raise RuntimeError("activation command cleanup is not confirmed")
        state.update(activation_effect=activated["status"], status=activated["status"],
                     stage="activation", last_activation_attempt=time.time())
        if activated["status"] in {"activated", "current"}:
            state["installation_complete"] = True
    except BaseException as original:
        state["activation_process"] = process
        if process.get("started") is False:
            state["activation_effect"] = "not_started"
        try:
            _record(binding, state)
        except Exception as recording:
            state["recording_error"] = type(recording).__name__
            original.add_note("activation outcome recording failed: " + type(recording).__name__)
        raise
    _record(binding, state)
    return activated


def check(binding, state=None):
    from bootstrap import ensure_runtime
    from mindie_knowledge.releases import select_release, supports_harness
    from mindie_knowledge.releases_io import fetch_releases, verify_release_source

    state = dict(read_receipt(binding)) if state is None else state
    native = native_binary(binding)
    version = native_version(native)
    native_installed = installed_plugin(native)
    pending = _pending_install(state)
    if pending and state.get("preparation_effect") == "unknown":
        process = state.get("preparation_process", {})
        if process.get("cleanup_failed") is True:
            raise RuntimeError("previous preparation writer is not confirmed settled")
    if pending and "prepared" in state and state.get("activation_effect") not in {
        "not_started", "deferred", "unknown", "incomplete", "activated", "current",
    }:
        raise ValueError("prepared candidate lacks a known activation phase")
    if pending and state.get("activation_effect") in {"unknown", "incomplete"}:
        _observe_activation(binding, state)
        if state.get("installation_complete") is True:
            state.pop("last_failed_check", None)
            return state
        pending = _pending_install(state)
    previous = dict(state)
    if (pending and state.get("native_effect") == "verified"
            and state.get("activation_effect") in {"not_started", "deferred"}
            and not supports_harness(state["candidate"]["metadata"]["compatibility"],
                                     harness_name=HARNESS, harness_version=version)):
        # A completed native installation is not an uncertain writer. Preserve
        # its facts, but do not pin selection forever to an incompatible host.
        state["selection_closed"] = True
        pending = False
    if pending:
        candidate = previous["candidate"]
    else:
        result = select_release(
            fetch_releases(REPOSITORY), repository=REPOSITORY, harness_name=HARNESS,
            harness_version=version,
            installed_version=native_installed["version"],
        )
        if result["candidate"] is None:
            state.update(result, last_successful_check=time.time(), harness_version=version)
            state.pop("last_failed_check", None)
            return state
        candidate = result["candidate"]
        verify_release_source(candidate["metadata"], repository=REPOSITORY, harness_name=HARNESS)
        state.update(status="selected", candidate=candidate,
                     last_successful_check=time.time(), harness_version=version)
        if previous.get("candidate") != candidate or previous.get("native_effect") != "verified":
            state.update(native_effect="not_started", process={}, activation_effect="not_started",
                         activation_process={})
            state.pop("prepared", None)
            state.pop("installed", None)
            state.pop("preparation_effect", None)
            state.pop("preparation_process", None)
            state.pop("preparation_stage", None)
        state.pop("installation_complete", None)
        state.pop("selection_closed", None)
        state.pop("last_failed_check", None)
        _record(binding, state)
    if not supports_harness(candidate["metadata"]["compatibility"], harness_name=HARNESS,
                            harness_version=version):
        state.update(status="incompatible_harness", harness_version=version,
                     last_failed_check=time.time())
        return state
    env = dict(os.environ, MINDIE_CC_CONFIG=binding["base_config"])
    env.pop("PYTHONPATH", None)
    prepared = state.get("prepared")
    if prepared is None:
        installed = install_selected(binding, native, candidate, state)
        observer = _preparation_observer(binding, state)
        python, scripts = ensure_runtime(installed, on_process=observer)
        observer("binding-prepare", None)
        process = {}
        try:
            output = run(
                [str(python), str(scripts / "setup.py"), "--source", str(installed),
                 "--config", binding["base_config"]],
                timeout=120.0, env=env, receipt=process,
            )
        except BaseException as original:
            try:
                observer("binding-prepare", process)
            except Exception as recording:
                original.add_note("preparation outcome recording failed: " + type(recording).__name__)
            raise
        observer("binding-prepare", process)
        if state["preparation_effect"] != "completed":
            raise RuntimeError("binding preparation cleanup is not confirmed")
        prepared = json.loads(output)
        prepared.update(python=str(python), runtime_scripts=str(scripts))
        state["prepared"] = prepared
        state["activation_effect"] = "not_started"
        _record(binding, state)
    else:
        if state.get("native_effect") != "verified":
            raise ValueError("prepared activation lacks verified native installation")
        row = installed_plugin(native)
        installed = state.get("installed", {})
        if any(row.get(key) != installed.get(key) for key in ("version", "installPath")):
            raise ValueError("native selection changed before prepared activation")
    selected = read_binding(prepared["config"])
    if (selected["base_config"] != binding["base_config"]
            or selected["state_dir"] != binding["state_dir"]
            or selected["python"] != prepared["python"]
            or selected["runtime_scripts"] != prepared["runtime_scripts"]):
        raise ValueError("prepared runtime identity changed before activation")
    _activate(binding, prepared, env, state)
    state.pop("last_failed_check", None)
    return state


def main():
    binding = read_binding()
    profile = binding.get("claude_config_dir")
    if not isinstance(profile, str) or not os.path.isabs(profile):
        raise ValueError("native profile is not explicitly bound")
    os.environ["CLAUDE_CONFIG_DIR"] = profile
    # This is the old preparation check lock, not a version/current registry.
    lock = OperationLock({"state_dir": str(Path(binding["state_dir"]) / "release-check")})
    try:
        with lock.exclusive(timeout=0):
            if not due(binding):
                return
            result = read_receipt(binding)
            result["last_attempt_at"] = time.time()
            try:
                result = check(binding, result)
            except NativePluginDisabled:
                result["status"] = "native_disabled"
            except Exception as exc:
                result.update(status="failed", last_failed_check=time.time(),
                              error_type=type(exc).__name__)
                import diagnostic_support
                diagnostic_support.failure("release.update", "native_update", "internal",
                                           exception=exc, reportable=False)
            _record(binding, result)
    except LockTimeout:
        return


if __name__ == "__main__":
    from bounded import operation
    with operation(signals=True):
        main()
