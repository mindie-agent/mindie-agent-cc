"""Independent CC oracles for the 2026-09-27 contracts.

This module covers setup identity, consent files and the Stop gate. The
update/entry contracts live in ``test_independent_cc_update.py``; both modules
share ``independent_cc_support`` so the lane fixtures have one implementation.
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
import time
import unittest
import uuid
from pathlib import Path

from independent_cc_support import (  # noqa: F401
    IdentityPrecondition,
    KNOWLEDGE_CHECKOUT_ENV,
    LANE_STATE,
    LaneCase,
    ROOT,
    SCRIPTS,
    _capture_rows,
    _file_url,
    _git,
    _git_env,
    _hold_canonical_lock,
    _profile_mutation,
    _runtime_pins,
    _service_pids,
    _service_pids_until,
    _store_path,
    _wait_for_service_exit,
    knowledge_checkout,
    reap_services,
)


class SetupIdentityTests(LaneCase):
    def test_production_runtime_obeys_official_lock_pins(self):
        from candidate_validate import installed_revisions
        from product_contract import requirements
        installed_revisions(requirements(ROOT)[0])

    def test_local_git_source_is_rejected_even_when_commit_matches(self):
        import importlib.metadata
        from candidate_validate import CheckFailure, installed_revisions
        from product_contract import requirements

        pins = requirements(ROOT)[0]
        commit = pins["mindie-knowledge"]
        site_dir = self.tmp / "local-dist"
        dist_info = site_dir / "mindie_knowledge-0.0.0.dist-info"
        dist_info.mkdir(parents=True)
        (dist_info / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: mindie-knowledge\nVersion: 0.0.0\n",
            encoding="utf-8",
        )
        (dist_info / "direct_url.json").write_text(
            json.dumps(
                {
                    "url": _file_url(self.tmp / "local-knowledge-repo"),
                    "vcs_info": {"vcs": "git", "commit_id": commit},
                }
            ),
            encoding="utf-8",
        )
        sys.path.insert(0, str(site_dir))
        try:
            with self.assertRaises(CheckFailure) as caught:
                installed_revisions(pins)
            self.assertEqual(caught.exception.failure.get("code"), "revision_mismatch")
            self.assertEqual(caught.exception.failure.get("component"), "mindie-knowledge")
        finally:
            sys.path.remove(str(site_dir))


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
        written = json.loads(community.read_text(encoding="utf-8"))
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
        enabled_data = json.loads(enabled.read_text(encoding="utf-8"))
        self.assertEqual(
            Path(enabled_data.get("consent_config", "")).resolve(),
            (enabled.parent / "mindie-consent.json").resolve(),
        )

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
        ):
            try:
                call()
            except Exception as exc:
                rejected.append(type(exc).__name__)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(len(rejected), 1, rejected)
        self.assertNotEqual(consent.load()["state"], "ok")






class StopGateTests(LaneCase):
    may_start_knowledge_service = True

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
            , encoding="utf-8")
        if authority is not None:
            target = case / "authority" / "mindie-consent.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(authority) + "\n", encoding="utf-8")
            community = Path(json.loads(config.read_text(encoding="utf-8"))["community_config"])
            data = json.loads(community.read_text(encoding="utf-8"))
            data["consent_config"] = str(target.resolve())
            community.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        prepare_schema(str(engine_config_path()))
        return config, project, outside

    def _stop(self, case: Path, project: Path, *, session=None, prompt=None,
              sentinel="SENTINEL", hook_cwd=None, manual=False):
        from datetime import datetime, timezone

        session = session or str(uuid.uuid4())
        prompt = prompt or str(uuid.uuid4())
        if manual:
            from admission import activate
            activate(session, project_root=str(project))
        # The native artifact supplies capture scope; Stop never grants entry.
        transcript = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "fixture" / f"{session}.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        if not transcript.exists():
            stamp = datetime.now(timezone.utc).isoformat()
            queue = dict(type="queue-operation", operation="enqueue",
                         sessionId=session, timestamp=stamp)
            transcript.write_text(json.dumps(queue) + "\n" + json.dumps(dict(
                type="assistant", sessionId=session, cwd=str(project),
                timestamp=stamp,
                message=dict(role="assistant", content=sentinel),
            )) + "\n", encoding="utf-8")
        payload = {
            "hook_event_name": "Stop",
            "session_id": session,
            "prompt_id": prompt,
            "cwd": str(hook_cwd or project),
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
        return proc, _capture_rows(case), _store_path(case).with_name("wake.json").exists()


    def test_authorized_contribute_stop_is_received_once(self):
        case = self.tmp / "allow"
        _config, project, _outside = self._arm(case, sharing=True, choice="contribute")
        sentinel = "SENTINEL-contribute-once"
        try:
            session, prompt = str(uuid.uuid4()), str(uuid.uuid4())
            first, rows, _wake = self._stop(
                case, project, session=session, prompt=prompt, sentinel=sentinel, manual=True
            )
            self.assertEqual(first.returncode, 0, first.stderr.decode()[:500])
            self.assertEqual(json.loads(first.stdout.decode() or "{}"), {})
            self.assertEqual([row[0] for row in rows], [""])  # notification carries no duplicate body
            second, rows, _wake = self._stop(
                case, project, session=session, prompt=prompt, sentinel=sentinel, manual=True
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
            proc, rows, wake = self._stop(case, project, sentinel="SENTINEL-disabled-community", manual=True)
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
            proc, rows, wake = self._stop(case, outside, sentinel="SENTINEL-out-of-scope", manual=True)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:500])
            self.assertEqual(rows, [])
            self.assertFalse(wake)
        finally:
            reap_services(case)

    def test_hook_cwd_cannot_override_native_artifact_scope(self):
        case = self.tmp / "artifact-scope"
        _config, project, outside = self._arm(case, sharing=True, choice="contribute")
        proc, rows, wake = self._stop(case, outside, hook_cwd=project, manual=True)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:500])
        self.assertEqual(rows, [])
        self.assertFalse(wake)

    def test_ordinary_stop_preserves_explicit_deactivation(self):
        import admission

        case = self.tmp / "revoked-task"
        _config, project, _outside = self._arm(case, sharing=True, choice="contribute")
        session = str(uuid.uuid4())
        admission.activate(session, project_root=str(project))
        admission.deactivate(session)
        proc, rows, wake = self._stop(case, project, session=session)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:500])
        self.assertEqual(rows, [])
        self.assertFalse(wake)
        self.assertEqual(admission.gate().inspect(session)["status"], "inactive")

    def test_disallowed_or_damaged_consent_does_not_persist_stop(self):
        """The declared consent authority must refuse disallowed capture."""
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
                sentinel = "SENTINEL-" + name
                try:
                    proc, rows, wake = self._stop(case, project, sentinel=sentinel, manual=True)
                    self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:500])
                    output = json.loads(proc.stdout.decode() or "{}")
                    if name == "corrupt":
                        self.assertEqual(set(output), {"systemMessage"})
                        self.assertIn("capture encountered an error", output["systemMessage"])
                        self.assertNotIn("decision", output)
                        self.assertNotIn("continue", output)
                    else:
                        self.assertEqual(output, {})
                    self.assertEqual(rows, [], rows)
                    self.assertFalse(wake)
                    self.assertEqual(_service_pids(case), [])
                finally:
                    reap_services(case)


    def test_field_target_wins_over_sibling_contribute(self):
        case = self.tmp / "field-target"
        config, project, _outside = self._arm(case, sharing=True, choice="contribute")
        community = Path(json.loads(config.read_text(encoding="utf-8"))["community_config"])
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
        , encoding="utf-8")
        data = json.loads(community.read_text(encoding="utf-8"))
        data["consent_config"] = str(authority.resolve())
        community.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        pointer = json.loads(config.read_text(encoding="utf-8"))["community_config"]
        try:
            proc, rows, wake = self._stop(case, project, sentinel="SENTINEL-field-target", manual=True)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:400])
            self.assertEqual(rows, [])
            self.assertFalse(wake)
        finally:
            reap_services(case)
        self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["community_config"], pointer)

    def test_corrupt_shared_authority_does_not_fall_back(self):
        case = self.tmp / "corrupt-shared"
        config, project, _outside = self._arm(case, sharing=True, choice="contribute")
        legacy = Path(json.loads(config.read_text(encoding="utf-8"))["community_config"])
        legacy_bytes = legacy.read_bytes()
        shared = config.parent / "mindie-community.json"
        shared.write_bytes(b"{not-a-community-document\n")
        engine = Path(json.loads(config.read_text(encoding="utf-8"))["engine_config"])
        for path in (config, engine):
            document = json.loads(path.read_text(encoding="utf-8"))
            document["community_config"] = str(shared.resolve())
            path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        try:
            proc, rows, wake = self._stop(case, project, sentinel="SENTINEL-corrupt-shared", manual=True)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:400])
            self.assertEqual(rows, [])
            self.assertFalse(wake)
        finally:
            reap_services(case)
        self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["community_config"], str(shared.resolve()))
        self.assertEqual(legacy.read_bytes(), legacy_bytes)
        self.assertEqual(shared.read_bytes(), b"{not-a-community-document\n")

    def test_stop_does_not_rewrite_install_files(self):
        case = self.tmp / "rewrite"
        config, project, _outside = self._arm(case, sharing=True, choice="contribute")
        engine = Path(json.loads(config.read_text(encoding="utf-8"))["engine_config"])
        community = Path(json.loads(config.read_text(encoding="utf-8"))["community_config"])
        before = (config.read_bytes(), engine.read_bytes(), community.read_bytes())
        try:
            proc, _rows, _wake = self._stop(case, project, sentinel="SENTINEL-rewrite", manual=True)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:400])
        finally:
            reap_services(case)
        self.assertEqual(config.read_bytes(), before[0])
        self.assertEqual(engine.read_bytes(), before[1])
        self.assertEqual(community.read_bytes(), before[2])
        self.assertFalse((config.parent / "mindie-community.json").exists())
