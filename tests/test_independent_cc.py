"""Independent CC oracles for the 2026-09-27 contracts.

These tests do not copy production branch structure. A handwritten adapter
config is only a localization fixture for Stop and entry behavior. It is not
an install-path pass; install identity is the setup subprocess below.

Profiles, diagnostics, and HOME for these tests stay under this lane's state
directory. They do not start the Claude host.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
import uuid
from importlib.metadata import distribution
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
LANE_STATE = Path(__file__).resolve().parents[2] / "state" / "component-tests"


_ENV_KEYS = (
    "HOME",
    "XDG_CONFIG_HOME",
    "XDG_STATE_HOME",
    "XDG_DATA_HOME",
    "MINDIE_CC_CONFIG",
    "MINDIE_DIAGNOSTICS_CONFIG",
    "MINDIE_DIAGNOSTICS_ROOT",
    "CLAUDE_CONFIG_DIR",
    "PYTHONDONTWRITEBYTECODE",
)


def _service_pids(root: Path) -> list[int]:
    needle = str(root)
    found = []
    listing = subprocess.run(
        ["ps", "-ax", "-o", "pid=,command="],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    own = {os.getpid(), os.getppid()}
    for raw in listing.stdout.splitlines():
        line = raw.strip()
        if needle not in line or "mindie_knowledge.loop.cli" not in line:
            continue
        pid_text, _, _command = line.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid not in own:
            found.append(pid)
    return found


def reap_services(root: Path) -> None:
    for pid in _service_pids(root):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


class LaneCase(unittest.TestCase):
    def setUp(self):
        LANE_STATE.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="case-", dir=LANE_STATE))
        self._saved_env = {key: os.environ.get(key) for key in _ENV_KEYS}
        os.environ["HOME"] = str(self.tmp / "home")
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "xdg")
        os.environ["XDG_STATE_HOME"] = str(self.tmp / "xdg-state")
        os.environ["XDG_DATA_HOME"] = str(self.tmp / "xdg-data")
        os.environ["MINDIE_DIAGNOSTICS_CONFIG"] = str(self.tmp / "diagnostics.json")
        os.environ["MINDIE_DIAGNOSTICS_ROOT"] = str(self.tmp / "diag-root")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.tmp / "claude-config")
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        os.environ["MINDIE_CC_CONFIG"] = str(self.tmp / "unset-cc.json")
        for key in (
            "HOME",
            "XDG_CONFIG_HOME",
            "XDG_STATE_HOME",
            "XDG_DATA_HOME",
            "MINDIE_DIAGNOSTICS_ROOT",
            "CLAUDE_CONFIG_DIR",
        ):
            Path(os.environ[key]).mkdir(parents=True, exist_ok=True)
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        tests = str(ROOT / "tests")
        if tests not in sys.path:
            sys.path.insert(0, tests)

    def tearDown(self):
        reap_services(self.tmp)
        shutil.rmtree(self.tmp, ignore_errors=True)
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _env(self, config: Path) -> dict:
        env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        env["MINDIE_CC_CONFIG"] = str(config)
        return env

    def _use(self, config: Path) -> None:
        os.environ["MINDIE_CC_CONFIG"] = str(config)


def _store_path(case: Path) -> Path:
    return case / "domain" / "vllm-ascend" / "store-v3.sqlite3"


def _capture_rows(case: Path):
    store = _store_path(case)
    if not store.is_file():
        return []
    db = sqlite3.connect(store)
    try:
        return db.execute(
            "SELECT summary, transcript, status FROM captures"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        db.close()


class SetupIdentityTests(LaneCase):
    def _pin_rejection(self):
        import setup

        try:
            setup.probe_runtime(sys.executable)
        except SystemExit as exc:
            return str(exc)
        return None

    def _run_setup(self, config: Path, root: Path, extra=()):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "setup.py"),
                "--config",
                str(config),
                "--root",
                str(root),
                "--knowledge-python",
                sys.executable,
                "--no-schedule",
                "--no-native-install",
                "--no-public-feed",
                *extra,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=self._env(config),
        )

    def test_production_setup_obeys_official_pin(self):
        reason = self._pin_rejection()
        config = self.tmp / "prod" / "cc.json"
        data = self.tmp / "prod" / "data"
        proc = self._run_setup(config, data)
        if reason is None:
            self.assertEqual(proc.returncode, 0, proc.stderr[-1500:])
            community = json.loads(config.with_name("mindie-community.json").read_text())
            self.assertEqual(
                community.get("consent_config"),
                str((config.parent / "mindie-consent.json").resolve()),
            )
            self.assertIs(community.get("enabled"), False)
            return
        self.assertNotEqual(proc.returncode, 0, proc.stdout[-500:])
        self.assertIn("mindie-knowledge", proc.stderr)
        self.assertNotIn("remote-dev does not match", proc.stderr)
        self.assertFalse(config.exists())
        self.assertFalse(data.exists())
        self.assertIn("does not match the required official Git commit", reason)

    def test_ok_then_nonzero_probe_is_rejected_when_pin_stage_passes(self):
        """The exit-1-after-OK runtime probe is a separate gate from the pin check."""
        import setup
        from unittest.mock import patch

        fault = "print('OK', flush=True)\nraise SystemExit(1)\n"
        real = setup.run

        def wrapped(argv, data="", timeout=15, **kwargs):
            script = argv[-1] if argv else ""
            if "commit_id" in script:
                return "OK\n"
            return real(argv, data, timeout=timeout, **kwargs)

        with patch.object(setup, "run", wrapped), patch.object(setup, "PROBE_SCRIPT", fault):
            with self.assertRaises(SystemExit) as caught:
                setup.probe_runtime(sys.executable)
        message = str(caught.exception)
        self.assertIn("knowledge runtime probe failed", message)
        self.assertNotIn("exact required commits", message)

    def _knowledge_install(self):
        before = distribution("mindie-knowledge").read_text("direct_url.json") or ""
        meta = json.loads(before)
        vcs = meta.get("vcs_info") or {}
        return before, meta.get("url") or "", vcs.get("vcs"), vcs.get("commit_id")

    def test_explicit_local_candidate_accepts_only_recorded_vcs_commit(self):
        """A git+file install may be named explicitly. A directory install with
        no commit_id, or a declared SHA that is not that commit_id, must not
        become the production identity. direct_url.json is never rewritten."""
        before, url, vcs, commit = self._knowledge_install()
        self.assertEqual(vcs, "git", "phase2 runtime must be a git install, not dir_info")
        self.assertTrue(url.startswith("file:"), url[:160])
        self.assertRegex(commit or "", r"^[0-9a-f]{40}$")
        import setup

        pinned = setup.runtime_pins()["mindie-knowledge"]["commit"]
        wrong = "0" * 40
        for label, declared in (("zeros", wrong), ("requirements-pin", pinned)):
            if declared == commit:
                continue
            with self.subTest(declared=label):
                config = self.tmp / label / "cc.json"
                data = self.tmp / label / "data"
                proc = self._run_setup(
                    config,
                    data,
                    extra=("--allow-local-candidate", f"mindie-knowledge={declared}"),
                )
                after = distribution("mindie-knowledge").read_text("direct_url.json") or ""
                self.assertEqual(after, before)
                self.assertNotEqual(proc.returncode, 0, proc.stdout[-400:])
                self.assertFalse(config.exists())
                self.assertFalse(data.exists())
        config = self.tmp / "exact" / "cc.json"
        data = self.tmp / "exact" / "data"
        proc = self._run_setup(
            config,
            data,
            extra=("--allow-local-candidate", f"mindie-knowledge={commit}"),
        )
        after = distribution("mindie-knowledge").read_text("direct_url.json") or ""
        self.assertEqual(after, before, "candidate setup rewrote direct_url.json")
        self.assertEqual(
            proc.returncode,
            0,
            textwrap.shorten(proc.stderr, width=1500, placeholder=" ..."),
        )
        report = json.loads(proc.stdout)
        self.assertEqual(report.get("local_candidates", {}).get("mindie-knowledge"), commit)
        community = json.loads(config.with_name("mindie-community.json").read_text())
        self.assertEqual(
            Path(community["consent_config"]).resolve(),
            (config.parent / "mindie-consent.json").resolve(),
        )
        self.assertIs(community.get("enabled"), False)
        self.assertFalse((data / "cc" / "bootstrap-venv").exists())


class ConsentFileTests(LaneCase):
    def _config(self, case: Path):
        import support

        case.mkdir(parents=True, exist_ok=True)
        config = support.make_config(case, sharing=False)
        self._use(config)
        return config

    def test_install_writer_records_consent_authority(self):
        import setup

        community = self.tmp / "mindie-community.json"
        setup.write_community(community, None)
        written = json.loads(community.read_text())
        expected = str((community.parent / "mindie-consent.json").resolve())
        self.assertEqual(written.get("consent_config"), expected)
        self.assertIs(written.get("enabled"), False)

        project = self.tmp / "project"
        project.mkdir()
        parser = argparse.ArgumentParser()
        payload = setup.community_settings(
            argparse.Namespace(
                community_repository="owner/repo",
                community_project_root=[str(project)],
                community_account=None,
                community_fork=None,
                community_visibility="public",
                community_branch=None,
            ),
            parser,
        )
        enabled = self.tmp / "enabled-community.json"
        setup.write_community(enabled, payload)
        enabled_data = json.loads(enabled.read_text())
        self.assertEqual(
            Path(enabled_data.get("consent_config", "")).resolve(),
            (enabled.parent / "mindie-consent.json").resolve(),
        )

    def test_status_does_not_migrate_or_rewrite(self):
        import entry

        case = self.tmp / "status"
        config = self._config(case)
        engine = Path(json.loads(config.read_text())["engine_config"])
        community = Path(json.loads(config.read_text())["community_config"])
        marker = case / "state" / "cc.first-use.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"choice": "read-only"}) + "\n")
        before = {
            "adapter": config.read_bytes(),
            "engine": engine.read_bytes(),
            "community": community.read_bytes(),
            "marker": marker.read_bytes(),
        }
        payload = entry.status_payload()
        with self.subTest("does not re-ask"):
            self.assertEqual(payload.get("choices"), [])
        with self.subTest("does not create consent"):
            self.assertFalse((config.parent / "mindie-consent.json").exists())
        with self.subTest("does not copy community"):
            self.assertFalse((config.parent / "mindie-community.json").exists())
        with self.subTest("adapter bytes"):
            self.assertEqual(config.read_bytes(), before["adapter"])
        with self.subTest("engine bytes"):
            self.assertEqual(engine.read_bytes(), before["engine"])
        with self.subTest("community bytes"):
            self.assertEqual(community.read_bytes(), before["community"])
        with self.subTest("marker bytes"):
            self.assertEqual(marker.read_bytes(), before["marker"])

    def test_field_update_does_not_clear_a_damaged_consent_file(self):
        import consent

        case = self.tmp / "damaged"
        config = self._config(case)
        path = config.parent / "mindie-consent.json"
        original = b"{not-json-consent\n"
        path.write_bytes(original)
        rejected = []
        for call in (
            lambda: consent.record_choice("later"),
            lambda: consent.record_reporting("enabled"),
        ):
            try:
                call()
            except Exception as exc:
                rejected.append(type(exc).__name__)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(len(rejected), 2, rejected)
        self.assertNotEqual(consent.load()["state"], "ok")

    def test_unreadable_consent_is_not_replaced(self):
        import consent

        case = self.tmp / "unreadable"
        config = self._config(case)
        path = config.parent / "mindie-consent.json"
        original = b'{"schema":"mindie-consent/1","choice":"later","reporting":"disabled"}\n'
        path.write_bytes(original)
        path.chmod(0)
        try:
            try:
                path.read_bytes()
            except OSError:
                blocked = True
            else:
                blocked = False
            if not blocked:
                self.skipTest("mode 000 is still readable by the owner on this OS")
            rejected = False
            try:
                consent.record_choice("read-only")
            except Exception:
                rejected = True
            path.chmod(0o600)
            self.assertEqual(path.read_bytes(), original)
            self.assertTrue(rejected)
        finally:
            path.chmod(0o600)

    def test_choice_and_reporting_do_not_clobber_each_other(self):
        import consent

        case = self.tmp / "fields"
        config = self._config(case)
        self.assertEqual(consent.record_choice("read-only"), "read-only")
        self.assertEqual(consent.record_reporting("later"), "later")
        saved = consent.load()
        self.assertEqual(saved["choice"], "read-only")
        self.assertEqual(saved["reporting"], "later")
        self.assertEqual(consent.record_choice("disabled"), "disabled")
        saved = consent.load()
        self.assertEqual(saved["choice"], "disabled")
        self.assertEqual(saved["reporting"], "later")

    def test_cross_process_updates_keep_both_fields(self):
        import consent
        import multiprocessing

        case = self.tmp / "race"
        config = self._config(case)
        path = consent.consent_path()
        path.write_text(
            json.dumps(
                {
                    "schema": "mindie-consent/1",
                    "choice": "read-only",
                    "reporting": "later",
                }
            )
            + "\n"
        )
        ctx = multiprocessing.get_context("spawn")
        barrier = ctx.Barrier(2)
        rounds = 20
        home = os.environ["HOME"]
        proc_choice = ctx.Process(
            target=_race_worker,
            args=(str(config), home, str(SCRIPTS), "choice", "disabled", rounds, barrier),
        )
        proc_report = ctx.Process(
            target=_race_worker,
            args=(str(config), home, str(SCRIPTS), "reporting", "enabled", rounds, barrier),
        )
        proc_choice.start()
        proc_report.start()
        proc_choice.join(30)
        proc_report.join(30)
        self.assertFalse(proc_choice.is_alive())
        self.assertFalse(proc_report.is_alive())
        details = []
        for label, proc in (("choice", proc_choice), ("reporting", proc_report)):
            if proc.exitcode != 0:
                details.append(f"{label} exit {proc.exitcode}")
        self.assertEqual(
            details,
            [],
            "cross-process consent update failed: " + "; ".join(details),
        )
        saved = json.loads(path.read_text())
        self.assertEqual(saved.get("choice"), "disabled")
        self.assertEqual(saved.get("reporting"), "enabled")


def _race_worker(config, home, scripts, kind, value, rounds, barrier):
    os.environ["HOME"] = home
    os.environ["MINDIE_CC_CONFIG"] = config
    os.environ["XDG_CONFIG_HOME"] = str(Path(home) / "xdg")
    os.environ["XDG_STATE_HOME"] = str(Path(home) / "state")
    os.environ["MINDIE_DIAGNOSTICS_CONFIG"] = str(Path(home) / "diagnostics.json")
    os.environ["MINDIE_DIAGNOSTICS_ROOT"] = str(Path(home) / "diag")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.path.insert(0, scripts)
    import consent

    for _ in range(rounds):
        barrier.wait(20)
        if kind == "choice":
            consent.record_choice(value)
        else:
            consent.record_reporting(value)


class StopGateTests(LaneCase):
    def _arm(self, case: Path, *, sharing: bool, choice=None, corrupt=False, authority=None):
        import support
        from mindie_knowledge.loop.handoff import prepare_schema
        from paths import engine_config_path

        case.mkdir(parents=True, exist_ok=True)
        project = case / "project"
        outside = case / "outside"
        project.mkdir(exist_ok=True)
        outside.mkdir(exist_ok=True)
        config = support.make_config(case, sharing=sharing, roots=[str(project)])
        self._use(config)
        consent_path = config.parent / "mindie-consent.json"
        if corrupt:
            consent_path.write_bytes(b"{not-json-consent\n")
        elif choice is not None:
            consent_path.write_text(
                json.dumps(
                    {
                        "schema": "mindie-consent/1",
                        "choice": choice,
                        "reporting": "later",
                    }
                )
                + "\n"
            )
        if authority is not None:
            target = case / "authority" / "mindie-consent.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(authority) + "\n")
            community = Path(json.loads(config.read_text())["community_config"])
            data = json.loads(community.read_text())
            data["consent_config"] = str(target.resolve())
            community.write_text(json.dumps(data, indent=2) + "\n")
        prepare_schema(str(engine_config_path()))
        current_dir = case / "state" / "update"
        current_dir.mkdir(parents=True, exist_ok=True)
        (current_dir / "current.json").write_text(
            json.dumps(
                {
                    "generation": str(ROOT),
                    "python": sys.executable,
                    "adapter_config": str(config),
                    "sha": "independent-test",
                }
            )
            + "\n"
        )
        return config, project, outside

    def _stop(self, case: Path, project: Path, *, session=None, prompt=None, sentinel="SENTINEL"):
        import admission

        session = session or str(uuid.uuid4())
        prompt = prompt or str(uuid.uuid4())
        admission.activate(session, project_root=str(project), root_session=session)
        transcript = project / "turn.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(sentinel + "\n")
        payload = {
            "hook_event_name": "Stop",
            "session_id": session,
            "prompt_id": prompt,
            "cwd": str(project),
            "transcript_path": str(transcript),
            "last_assistant_message": sentinel,
            "stop_hook_active": False,
        }
        config = Path(os.environ["MINDIE_CC_CONFIG"])
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "mindie_launch.py"), "hook", "stop"],
            input=json.dumps(payload).encode(),
            capture_output=True,
            env=self._env(config),
            timeout=8,
        )
        return proc, _capture_rows(case), (case / "domain" / "vllm-ascend" / "wake.json").exists()

    def _migrate(self):
        import consent

        return consent.migrate_community()

    def test_authorized_contribute_stop_is_received_once(self):
        case = self.tmp / "allow"
        _config, project, _outside = self._arm(case, sharing=True, choice="contribute")
        sentinel = "SENTINEL-contribute-once"
        try:
            session, prompt = str(uuid.uuid4()), str(uuid.uuid4())
            first, rows, _wake = self._stop(
                case, project, session=session, prompt=prompt, sentinel=sentinel
            )
            self.assertEqual(first.returncode, 0, first.stderr.decode()[:500])
            self.assertEqual(json.loads(first.stdout.decode() or "{}"), {})
            self.assertEqual([row[0] for row in rows], [sentinel])
            second, rows, _wake = self._stop(
                case, project, session=session, prompt=prompt, sentinel=sentinel
            )
            self.assertEqual(second.returncode, 0, second.stderr.decode()[:500])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][2], "queued")
        finally:
            reap_services(case)

    def test_disabled_community_blocks_contribute_consent(self):
        case = self.tmp / "off"
        _config, project, _outside = self._arm(case, sharing=False, choice="contribute")
        try:
            proc, rows, wake = self._stop(case, project, sentinel="SENTINEL-disabled-community")
            self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:500])
            self.assertEqual(rows, [])
            self.assertFalse(wake)
            self.assertEqual(_service_pids(case), [])
        finally:
            reap_services(case)

    def test_out_of_scope_stop_does_not_persist(self):
        case = self.tmp / "scope"
        _config, _project, outside = self._arm(case, sharing=True, choice="contribute")
        try:
            proc, rows, wake = self._stop(case, outside, sentinel="SENTINEL-out-of-scope")
            self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:500])
            self.assertEqual(rows, [])
            self.assertFalse(wake)
        finally:
            reap_services(case)

    def test_disallowed_or_damaged_consent_does_not_persist_stop(self):
        """After the explicit community migration stamps consent_config, the
        core gate must refuse capture. A legacy file with no such field is a
        different case and is not this test."""
        scenarios = [
            ("later", dict(choice="later")),
            ("read-only", dict(choice="read-only")),
            ("disabled", dict(choice="disabled")),
            ("corrupt", dict(corrupt=True)),
        ]
        for name, kwargs in scenarios:
            with self.subTest(name=name):
                case = self.tmp / name
                _config, project, _outside = self._arm(case, sharing=True, **kwargs)
                migration = self._migrate()
                self.assertFalse(migration.get("conflict"), migration)
                self.assertFalse(migration.get("errors"), migration)
                sentinel = "SENTINEL-" + name
                try:
                    proc, rows, wake = self._stop(case, project, sentinel=sentinel)
                    self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:500])
                    self.assertEqual(json.loads(proc.stdout.decode() or "{}"), {})
                    self.assertEqual(rows, [], rows)
                    self.assertFalse(wake)
                    self.assertEqual(_service_pids(case), [])
                finally:
                    reap_services(case)

    def test_unstamped_legacy_config_still_captures(self):
        """No consent_config field: the published gate keeps the old read.
        This is compatibility, not proof that a migrated install may capture."""
        case = self.tmp / "legacy-field"
        _config, project, _outside = self._arm(case, sharing=True, choice="later")
        try:
            proc, rows, _wake = self._stop(case, project, sentinel="SENTINEL-legacy-field")
            self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:400])
            self.assertEqual([row[0] for row in rows], ["SENTINEL-legacy-field"])
        finally:
            reap_services(case)

    def test_field_target_wins_over_sibling_contribute(self):
        case = self.tmp / "field-target"
        config, project, _outside = self._arm(case, sharing=True, choice="contribute")
        migration = self._migrate()
        self.assertFalse(migration.get("errors"), migration)
        community = Path(json.loads(config.read_text())["community_config"])
        authority = case / "disabled-authority.json"
        authority.write_text(
            json.dumps(
                {
                    "schema": "mindie-consent/1",
                    "choice": "disabled",
                    "reporting": "later",
                }
            )
            + "\n"
        )
        data = json.loads(community.read_text())
        data["consent_config"] = str(authority.resolve())
        community.write_text(json.dumps(data, indent=2) + "\n")
        pointer = json.loads(config.read_text())["community_config"]
        try:
            proc, rows, wake = self._stop(case, project, sentinel="SENTINEL-field-target")
            self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:400])
            self.assertEqual(rows, [])
            self.assertFalse(wake)
        finally:
            reap_services(case)
        self.assertEqual(json.loads(config.read_text())["community_config"], pointer)

    def test_corrupt_shared_authority_does_not_fall_back(self):
        case = self.tmp / "corrupt-shared"
        config, project, _outside = self._arm(case, sharing=True, choice="contribute")
        legacy = Path(json.loads(config.read_text())["community_config"])
        legacy_bytes = legacy.read_bytes()
        shared = config.parent / "mindie-community.json"
        shared.write_bytes(b"{not-a-community-document\n")
        engine = Path(json.loads(config.read_text())["engine_config"])
        for path in (config, engine):
            document = json.loads(path.read_text())
            document["community_config"] = str(shared.resolve())
            path.write_text(json.dumps(document, indent=2) + "\n")
        try:
            proc, rows, wake = self._stop(case, project, sentinel="SENTINEL-corrupt-shared")
            self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:400])
            self.assertEqual(rows, [])
            self.assertFalse(wake)
        finally:
            reap_services(case)
        self.assertEqual(json.loads(config.read_text())["community_config"], str(shared.resolve()))
        self.assertEqual(legacy.read_bytes(), legacy_bytes)
        self.assertEqual(shared.read_bytes(), b"{not-a-community-document\n")

    def test_stop_does_not_rewrite_install_files(self):
        case = self.tmp / "rewrite"
        config, project, _outside = self._arm(case, sharing=True, choice="contribute")
        engine = Path(json.loads(config.read_text())["engine_config"])
        community = Path(json.loads(config.read_text())["community_config"])
        before = (config.read_bytes(), engine.read_bytes(), community.read_bytes())
        try:
            proc, _rows, _wake = self._stop(case, project, sentinel="SENTINEL-rewrite")
            self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:400])
        finally:
            reap_services(case)
        self.assertEqual(config.read_bytes(), before[0])
        self.assertEqual(engine.read_bytes(), before[1])
        self.assertEqual(community.read_bytes(), before[2])
        self.assertFalse((config.parent / "mindie-community.json").exists())


class EntryContractTests(LaneCase):
    def _config(self, sharing=False):
        import support

        case = self.tmp / "entry"
        case.mkdir(parents=True, exist_ok=True)
        project = case / "project"
        project.mkdir(exist_ok=True)
        config = support.make_config(
            case, sharing=sharing, roots=[str(project)] if sharing else None
        )
        self._use(config)
        return config, project

    def _event(self, command, args="", *, session, prompt, cwd, **extra):
        event = {
            "hook_event_name": "UserPromptExpansion",
            "expansion_type": "slash_command",
            "command_name": command,
            "command_source": "plugin",
            "command_args": args,
            "session_id": session,
            "prompt_id": prompt,
            "cwd": str(cwd),
            "transcript_path": str(Path(cwd) / "transcript.jsonl"),
        }
        event.update(extra)
        return event

    def test_saved_choice_is_reused_for_a_new_task_without_another_prompt(self):
        import entry

        _config, project = self._config(False)
        first_session = str(uuid.uuid4())
        payload = entry.dispatch_event(
            self._event(
                "mindie-agent",
                "later",
                session=first_session,
                prompt=str(uuid.uuid4()),
                cwd=project,
            )
        )
        self.assertEqual(payload.get("first_use"), "later")
        self.assertEqual(payload.get("choices"), [])
        second = entry.dispatch_event(
            self._event(
                "mindie-agent",
                "",
                session=str(uuid.uuid4()),
                prompt=str(uuid.uuid4()),
                cwd=project,
            )
        )
        self.assertEqual(second.get("first_use"), "later")
        self.assertEqual(second.get("choices"), [])
        self.assertNotIn("sharing-enable", json.dumps(second))
        outside = self.tmp / "entry" / "outside"
        outside.mkdir()
        third = entry.dispatch_event(
            self._event(
                "mindie-agent:mindie-agent",
                "",
                session=str(uuid.uuid4()),
                prompt=str(uuid.uuid4()),
                cwd=outside,
            )
        )
        self.assertEqual(third.get("choices"), [])
        self.assertEqual(third.get("first_use"), "later")

    def test_status_does_not_import_marker_and_entry_imports_once(self):
        import consent
        import entry

        config, project = self._config(False)
        marker = self.tmp / "entry" / "state" / "cc.first-use.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"choice": "read-only"}) + "\n")
        status = entry.status_payload()
        self.assertEqual(status.get("choices"), [])
        self.assertFalse((config.parent / "mindie-consent.json").exists())
        entered = entry.dispatch_event(
            self._event(
                "mindie-agent",
                "",
                session=str(uuid.uuid4()),
                prompt=str(uuid.uuid4()),
                cwd=project,
            )
        )
        self.assertEqual(consent.load().get("choice"), "read-only")
        self.assertEqual(entered.get("choices"), [])
        marker.unlink()
        again = entry.dispatch_event(
            self._event(
                "mindie-agent",
                "",
                session=str(uuid.uuid4()),
                prompt=str(uuid.uuid4()),
                cwd=project,
            )
        )
        self.assertEqual(again.get("first_use"), "read-only")
        self.assertEqual(again.get("choices"), [])

    def test_subagent_does_not_inherit_the_parent_binding(self):
        import admission
        import entry

        _config, project = self._config(False)
        parent = str(uuid.uuid4())
        entry.dispatch_event(
            self._event(
                "mindie-agent",
                "read-only",
                session=parent,
                prompt=str(uuid.uuid4()),
                cwd=project,
            )
        )
        child = str(uuid.uuid4())
        with self.assertRaises(ValueError):
            entry.dispatch_event(
                self._event(
                    "mindie-agent",
                    "",
                    session=child,
                    prompt=str(uuid.uuid4()),
                    cwd=project,
                    agent_id="fork-1",
                )
            )
        self.assertIsNotNone(admission.gate().active_lease(parent))
        self.assertIsNone(admission.gate().active_lease(child))

    def test_ordinary_mention_does_not_choose_or_bind(self):
        config, project = self._config(False)
        transcript = project / "transcript.jsonl"
        transcript.write_text("{}\n")
        base = {
            "hook_event_name": "UserPromptExpansion",
            "command_name": "mindie-agent",
            "command_args": "read-only",
            "session_id": str(uuid.uuid4()),
            "prompt_id": str(uuid.uuid4()),
            "cwd": str(project),
            "transcript_path": str(transcript),
        }
        for extra in (
            {"expansion_type": "slash_command", "command_source": "user"},
            {"expansion_type": "prompt", "command_source": "plugin"},
        ):
            with self.subTest(extra=extra):
                proc = subprocess.run(
                    [sys.executable, str(SCRIPTS / "bridge.py"), "expansion"],
                    input=json.dumps({**base, **extra}).encode(),
                    capture_output=True,
                    env=self._env(config),
                    timeout=8,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:400])
                self.assertFalse((config.parent / "mindie-consent.json").exists())
                adapter = json.loads(config.read_text())
                engine = json.loads(Path(adapter["engine_config"]).read_text())
                self.assertFalse(Path(engine["admission_path"]).exists())

    def test_first_entry_offers_reporting_with_the_knowledge_choice(self):
        import entry

        _config, project = self._config(False)
        payload = entry.dispatch_event(
            self._event(
                "mindie-agent",
                "",
                session=str(uuid.uuid4()),
                prompt=str(uuid.uuid4()),
                cwd=project,
            )
        )
        self.assertGreaterEqual(len(payload.get("choices") or []), 3)
        reporting = payload.get("reporting_choice")
        self.assertIsInstance(reporting, dict, json.dumps(payload.get("reporting"), default=str)[:400])
        self.assertTrue(reporting.get("independent_of_knowledge_contribution"))

    def test_contribute_completes_on_the_unified_entry(self):
        import consent
        import entry

        _config, project = self._config(False)
        cold = entry.dispatch_event(
            self._event(
                "mindie-agent",
                "",
                session=str(uuid.uuid4()),
                prompt=str(uuid.uuid4()),
                cwd=project,
            )
        )
        contribute = next(item for item in cold["choices"] if item["id"] == "contribute")
        with self.subTest("user instruction"):
            self.assertNotIn("sharing-enable", contribute.get("next", ""))
        with self.subTest("same command records the choice"):
            payload = entry.dispatch_event(
                self._event(
                    "mindie-agent",
                    "contribute --repository owner/repo --account user "
                    f"--project-root {project} --visibility public",
                    session=str(uuid.uuid4()),
                    prompt=str(uuid.uuid4()),
                    cwd=project,
                )
            )
            saved = consent.load()
            self.assertEqual(saved.get("choice"), "contribute")
            self.assertEqual(payload.get("choices"), [])
            again = entry.dispatch_event(
                self._event(
                    "mindie-agent",
                    "",
                    session=str(uuid.uuid4()),
                    prompt=str(uuid.uuid4()),
                    cwd=project,
                )
            )
            self.assertEqual(again.get("choices"), [])
            self.assertEqual(again.get("first_use"), "contribute")

    def test_entry_choice_does_not_replace_damaged_consent(self):
        import entry

        config, project = self._config(False)
        path = config.parent / "mindie-consent.json"
        original = b"{not-json-consent\n"
        path.write_bytes(original)
        entry.dispatch_event(
            self._event(
                "mindie-agent",
                "later",
                session=str(uuid.uuid4()),
                prompt=str(uuid.uuid4()),
                cwd=project,
            )
        )
        self.assertEqual(path.read_bytes(), original)

    def test_skill_text_does_not_require_sharing_enable(self):
        text = (ROOT / "skills" / "mindie-agent" / "SKILL.md").read_text()
        self.assertNotIn("sharing-enable", text)


class AuthorityMigrationTests(LaneCase):
    def test_divergent_scopes_are_not_merged(self):
        import consent
        import support

        case = self.tmp / "conflict"
        project = case / "project"
        outside = case / "outside"
        project.mkdir(parents=True)
        outside.mkdir()
        config = support.make_config(case, sharing=True, roots=[str(project)])
        self._use(config)
        declared = Path(json.loads(config.read_text())["community_config"])
        shared = config.parent / "mindie-community.json"
        other = json.loads(declared.read_text())
        other["project_roots"] = [str(project.resolve()), str(outside.resolve())]
        other["enabled"] = True
        shared.write_text(json.dumps(other, indent=2) + "\n")
        declared_bytes = declared.read_bytes()
        shared_bytes = shared.read_bytes()
        result = consent.migrate_community()
        self.assertTrue(result.get("conflict"), result)
        self.assertFalse(result.get("migrated"))
        self.assertEqual(declared.read_bytes(), declared_bytes)
        self.assertEqual(shared.read_bytes(), shared_bytes)
        self.assertEqual(json.loads(config.read_text())["community_config"], str(declared))

    def test_corrupt_declared_migration_is_not_success(self):
        import consent
        import support

        case = self.tmp / "bad-declared"
        case.mkdir()
        config = support.make_config(case, sharing=True)
        self._use(config)
        declared = Path(json.loads(config.read_text())["community_config"])
        declared.write_bytes(b"{broken-community\n")
        result = consent.migrate_community()
        self.assertTrue(result.get("errors"), result)
        self.assertFalse(result.get("migrated"))
        self.assertNotIn("effective", result)
        self.assertEqual(declared.read_bytes(), b"{broken-community\n")
        self.assertFalse((config.parent / "mindie-community.json").exists())

    def test_stamp_preserves_generation(self):
        import consent
        import support

        case = self.tmp / "generation"
        project = case / "project"
        project.mkdir(parents=True)
        config = support.make_config(case, sharing=True, roots=[str(project)])
        self._use(config)
        declared = Path(json.loads(config.read_text())["community_config"])
        generation = json.loads(declared.read_text())["generation"]
        first = consent.migrate_community()
        self.assertFalse(first.get("errors"), first)
        effective = Path(first["effective"])
        stamped = json.loads(effective.read_text())
        self.assertEqual(stamped.get("generation"), generation)
        self.assertTrue(stamped.get("consent_config"))
        second = consent.migrate_community()
        self.assertFalse(second.get("conflict"), second)
        self.assertEqual(json.loads(effective.read_text()).get("generation"), generation)

    def test_kimi_choice_is_the_same_profile_authority(self):
        """Uses the committed kimi adapter scripts, not a second CC config."""
        import consent
        import support

        raw = os.environ.get("MINDIE_TEST_KIMI_SCRIPTS")
        self.assertTrue(raw, "set MINDIE_TEST_KIMI_SCRIPTS to the kimi adapter scripts")
        kimi_scripts = Path(raw)
        self.assertTrue((kimi_scripts / "consent.py").is_file(), kimi_scripts)
        case = self.tmp / "both"
        case.mkdir()
        (case / "project").mkdir()
        config = support.make_config(case, sharing=False)
        kimi_config = case / "kimi.json"
        kimi_config.write_text("{}\n")
        env = self._env(config)
        env.pop("MINDIE_CC_CONFIG", None)
        env["MINDIE_KIMI_CONFIG"] = str(kimi_config)
        env["HOME"] = os.environ["HOME"]
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import os,sys; sys.path.insert(0, sys.argv[1]);"
                "os.environ['MINDIE_KIMI_CONFIG']=sys.argv[2];"
                "import consent; consent.record_choice('later')",
                str(kimi_scripts),
                str(kimi_config),
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        self._use(config)
        saved = consent.load()
        self.assertEqual(saved.get("choice"), "later")
        self.assertEqual(saved.get("state"), "ok")
        other = self.tmp / "isolated"
        other.mkdir()
        other_config = other / "cc.json"
        other_config.write_text("{}\n")
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json,os,sys; sys.path.insert(0, sys.argv[1]);"
                "os.environ['MINDIE_CC_CONFIG']=sys.argv[2];"
                "import consent; print(json.dumps(consent.load()))",
                str(SCRIPTS),
                str(other_config),
            ],
            capture_output=True,
            text=True,
            env=self._env(other_config),
            timeout=20,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr[-800:])
        self.assertEqual(json.loads(probe.stdout)["state"], "missing")


def _hold_canonical_lock(lock_key, ready, release):
    from mindie_knowledge.loop.settings import CommunityWriteContext

    with CommunityWriteContext(lock_key):
        ready.set()
        if not release.wait(4):
            raise SystemExit("canonical lock holder was not released")


def _profile_mutation(config, home, scripts, kind, entered):
    os.environ["HOME"] = home
    os.environ["MINDIE_CC_CONFIG"] = config
    os.environ["XDG_CONFIG_HOME"] = str(Path(home) / "xdg")
    os.environ["XDG_STATE_HOME"] = str(Path(home) / "state")
    os.environ["MINDIE_DIAGNOSTICS_CONFIG"] = str(Path(home) / "diagnostics.json")
    os.environ["MINDIE_DIAGNOSTICS_ROOT"] = str(Path(home) / "diag")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.path.insert(0, scripts)
    try:
        if kind == "stamp":
            import consent

            entered.set()
            consent.migrate_community()
        elif kind == "disable":
            import sharing

            entered.set()
            sharing.write_disabled()
        else:
            raise SystemExit("unknown mutation")
    except Exception:
        import traceback

        Path(config).with_suffix(".worker-error").write_text(traceback.format_exc())
        raise


def _community_mutation_worker(config, home, scripts, kind, barrier):
    os.environ["HOME"] = home
    os.environ["MINDIE_CC_CONFIG"] = config
    os.environ["XDG_CONFIG_HOME"] = str(Path(home) / "xdg")
    os.environ["XDG_STATE_HOME"] = str(Path(home) / "state")
    os.environ["MINDIE_DIAGNOSTICS_CONFIG"] = str(Path(home) / "diagnostics.json")
    os.environ["MINDIE_DIAGNOSTICS_ROOT"] = str(Path(home) / "diag")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.path.insert(0, scripts)
    barrier.wait(15)
    if kind == "stamp":
        import consent

        consent.migrate_community()
    else:
        import sharing

        sharing.write_disabled()


class StampRaceTests(LaneCase):
    def test_first_stamp_keeps_a_concurrent_disable(self):
        """The one-time consent_config stamp and a settings write must both survive.

        Twenty synchronized pairs. A lost update drops either enabled=false
        or consent_config. This does not patch a private reader.
        """
        import multiprocessing
        import support

        losses = []
        home = os.environ["HOME"]
        ctx = multiprocessing.get_context("spawn")
        for index in range(20):
            case = self.tmp / f"race-{index}"
            project = case / "project"
            project.mkdir(parents=True)
            config = support.make_config(case, sharing=True, roots=[str(project)])
            self._use(config)
            (config.parent / "mindie-consent.json").write_text(
                json.dumps(
                    {"schema": "mindie-consent/1", "choice": "later", "reporting": "disabled"}
                )
                + "\n"
            )
            barrier = ctx.Barrier(2)
            stamp = ctx.Process(
                target=_community_mutation_worker,
                args=(str(config), home, str(SCRIPTS), "stamp", barrier),
            )
            disable = ctx.Process(
                target=_community_mutation_worker,
                args=(str(config), home, str(SCRIPTS), "disable", barrier),
            )
            stamp.start()
            disable.start()
            try:
                stamp.join(20)
                disable.join(20)
            finally:
                for proc in (stamp, disable):
                    if proc.is_alive():
                        proc.terminate()
                        proc.join(2)
            if stamp.exitcode != 0 or disable.exitcode != 0:
                losses.append(f"{index}: exit {stamp.exitcode}/{disable.exitcode}")
                continue
            pointer = json.loads(config.read_text()).get("community_config")
            try:
                data = json.loads(Path(pointer).read_text())
            except (OSError, ValueError, TypeError) as exc:
                losses.append(f"{index}: unreadable authority {exc}")
                continue
            if data.get("enabled") is not False or not data.get("consent_config"):
                losses.append(
                    f"{index}: enabled={data.get('enabled')} "
                    f"field={data.get('consent_config')!r}"
                )
        self.assertEqual(losses, [])


class CanonicalWriteTests(LaneCase):
    """Final core context: one canonical lock, no write-then-replay, corrupt bytes stay."""

    def _legacy(self, name):
        import support

        case = self.tmp / name
        project = case / "project"
        project.mkdir(parents=True)
        config = support.make_config(case, sharing=True, roots=[str(project)])
        self._use(config)
        legacy = config.parent / "cc.community.json"
        canonical = config.parent / "mindie-community.json"
        return config, legacy, canonical

    def test_write_disabled_preserves_corrupt_community_bytes(self):
        import sharing

        config, legacy, canonical = self._legacy("corrupt-disable")
        raw = b"{broken-community\n"
        legacy.write_bytes(raw)
        with self.assertRaises(ValueError):
            sharing.write_disabled()
        self.assertEqual(legacy.read_bytes(), raw)
        self.assertFalse(canonical.exists())
        self.assertEqual(json.loads(config.read_text())["community_config"], str(legacy))

    def test_disable_after_migration_does_not_replay_onto_legacy(self):
        import consent
        import sharing

        config, legacy, canonical = self._legacy("no-replay")
        before = legacy.read_bytes()
        result = consent.migrate_community()
        self.assertFalse(result.get("errors"), result)
        self.assertEqual(Path(result["effective"]).resolve(), canonical.resolve())
        self.assertEqual(legacy.read_bytes(), before)
        stamped = json.loads(canonical.read_text())
        self.assertEqual(stamped.get("generation"), "gen-test")
        self.assertTrue(stamped.get("consent_config"))
        sharing.write_disabled()
        self.assertEqual(legacy.read_bytes(), before)
        current = json.loads(canonical.read_text())
        self.assertIs(current.get("enabled"), False)
        self.assertEqual(current.get("consent_config"), stamped["consent_config"])
        self.assertNotEqual(current.get("generation"), "gen-test")
        pointer = json.loads(config.read_text())["community_config"]
        self.assertEqual(Path(pointer).resolve(), canonical.resolve())
        self.assertFalse((legacy.parent / (legacy.name + ".lock")).exists())
        self.assertTrue((canonical.parent / (canonical.name + ".lock")).exists())

    def _assert_blocks_on_canonical_lock(self, kind):
        import multiprocessing
        import time

        config, legacy, canonical = self._legacy("lock-" + kind)
        ctx = multiprocessing.get_context("spawn")
        ready = ctx.Event()
        release = ctx.Event()
        entered = ctx.Event()
        holder = ctx.Process(
            target=_hold_canonical_lock,
            args=(str(canonical), ready, release),
        )
        worker = ctx.Process(
            target=_profile_mutation,
            args=(str(config), os.environ["HOME"], str(SCRIPTS), kind, entered),
        )
        holder.start()
        try:
            self.assertTrue(ready.wait(5), "canonical lock was not acquired")
            worker.start()
            self.assertTrue(entered.wait(8), kind + " did not reach the write")
            time.sleep(0.4)
            self.assertTrue(
                worker.is_alive(),
                kind + " finished while the canonical community lock was held",
            )
            self.assertFalse((legacy.parent / (legacy.name + ".lock")).exists())
            release.set()
            worker.join(6)
            detail = ""
            error_path = config.with_suffix(".worker-error")
            if error_path.is_file():
                detail = error_path.read_text()[-800:]
            self.assertFalse(worker.is_alive(), detail)
            self.assertEqual(worker.exitcode, 0, detail)
        finally:
            release.set()
            for proc in (worker, holder):
                if proc.is_alive():
                    proc.terminate()
                    proc.join(2)
        self.assertTrue((canonical.parent / (canonical.name + ".lock")).exists())
        self.assertFalse((legacy.parent / (legacy.name + ".lock")).exists())
        return config, legacy, canonical

    def test_migration_waits_on_the_canonical_lock(self):
        config, _legacy, canonical = self._assert_blocks_on_canonical_lock("stamp")
        pointer = json.loads(config.read_text())["community_config"]
        self.assertEqual(Path(pointer).resolve(), canonical.resolve())
        stamped = json.loads(canonical.read_text())
        self.assertEqual(stamped.get("generation"), "gen-test")
        self.assertTrue(stamped.get("consent_config"))

    def test_disable_waits_on_the_canonical_lock(self):
        _config, legacy, canonical = self._assert_blocks_on_canonical_lock("disable")
        self.assertIs(json.loads(legacy.read_text()).get("enabled"), False)
        self.assertFalse(canonical.is_file())


class BootstrapCommunityWriterTests(LaneCase):
    """setup.write_community is the pre-runtime bootstrap writer.

    The invalid-file case matches integration-20260928/cc-bootstrap-corrupt-repro.json:
    explicit settings must not replace an existing damaged authority.
    """

    def _explicit(self, root: Path):
        import setup

        root.mkdir(parents=True, exist_ok=True)
        return setup.community_settings(
            argparse.Namespace(
                community_repository="owner/repo",
                community_project_root=[str(root)],
                community_account=None,
                community_fork=None,
                community_visibility="public",
                community_branch=None,
            ),
            argparse.ArgumentParser(),
        )

    def test_invalid_existing_file_is_preserved(self):
        import setup

        payload = self._explicit(self.tmp / "project")
        samples = {
            "repro": b"{broken-community",
            "foreign-schema": b'{"schema": "foreign-format/1", "enabled": true}\n',
            "non-object": b"[1]\n",
        }
        for name, raw in samples.items():
            with self.subTest(name=name):
                path = self.tmp / name / "mindie-community.json"
                path.parent.mkdir()
                path.write_bytes(raw)
                with self.assertRaises(SystemExit) as caught:
                    setup.write_community(path, payload)
                self.assertIn("nothing was written", str(caught.exception.code))
                self.assertEqual(path.read_bytes(), raw)
        path = self.tmp / "unreadable" / "mindie-community.json"
        path.parent.mkdir()
        raw = b"{broken-community"
        path.write_bytes(raw)
        path.chmod(0)
        try:
            with self.assertRaises(SystemExit) as caught:
                setup.write_community(path, payload)
            self.assertIn("nothing was written", str(caught.exception.code))
        finally:
            path.chmod(0o600)
        self.assertEqual(path.read_bytes(), raw)

    def test_missing_file_is_a_first_setup(self):
        import setup

        explicit = self.tmp / "first-explicit.json"
        self.assertFalse(explicit.exists())
        self.assertEqual(
            setup.write_community(explicit, self._explicit(self.tmp / "explicit-root")),
            "enabled",
        )
        written = json.loads(explicit.read_text())
        self.assertEqual(written.get("schema"), "mindie-community-config/1")
        self.assertIs(written.get("enabled"), True)
        self.assertEqual(
            Path(written["consent_config"]).resolve(),
            (explicit.parent / "mindie-consent.json").resolve(),
        )
        default_off = self.tmp / "first-off.json"
        self.assertEqual(setup.write_community(default_off, None), "off")
        off = json.loads(default_off.read_text())
        self.assertEqual(off.get("schema"), "mindie-community-config/1")
        self.assertIs(off.get("enabled"), False)

    def test_current_schema_is_repaired_and_extensions_survive(self):
        import setup

        path = self.tmp / "repair.json"
        path.write_text(
            json.dumps(
                {
                    "schema": "mindie-community-config/1",
                    "enabled": "not-a-bool",
                    "generation": "stale-generation",
                    "repository": "local/unconfigured",
                    "branch": "main",
                    "project_roots": "not-a-list",
                    "idle_seconds": 300,
                    "adapter_note": "keep",
                }
            )
            + "\n"
        )
        self.assertEqual(
            setup.write_community(path, self._explicit(self.tmp / "repair-root")),
            "enabled",
        )
        written = json.loads(path.read_text())
        self.assertEqual(written.get("schema"), "mindie-community-config/1")
        self.assertIs(written.get("enabled"), True)
        self.assertNotEqual(written.get("generation"), "stale-generation")
        self.assertIsInstance(written.get("project_roots"), list)
        self.assertEqual(written.get("adapter_note"), "keep")
        self.assertEqual(
            Path(written["consent_config"]).resolve(),
            (path.parent / "mindie-consent.json").resolve(),
        )


class HookManifestTests(LaneCase):
    def test_expansion_matchers_cover_every_accepted_command(self):
        import identity
        from native_claude import render_hooks

        accepted = set(identity.COMMANDS)
        committed = json.loads((ROOT / "hooks" / "hooks.json").read_text())
        committed_matchers = {
            item.get("matcher")
            for item in committed["hooks"]["UserPromptExpansion"]
            if isinstance(item, dict)
        }
        rendered = render_hooks(
            sys.executable,
            str(SCRIPTS / "mindie_launch.py"),
            str(self.tmp / "cc.json"),
        )
        rendered_matchers = {
            item.get("matcher")
            for item in rendered["hooks"]["UserPromptExpansion"]
            if isinstance(item, dict)
        }
        with self.subTest("committed hooks.json"):
            self.assertEqual(sorted(accepted - committed_matchers), [])
        with self.subTest("render_hooks"):
            self.assertEqual(sorted(accepted - rendered_matchers), [])


if __name__ == "__main__":
    unittest.main()
