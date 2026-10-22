"""Independent CC update, entry and authority contracts for 2026-09-27.

The setup/consent/Stop lanes live in ``test_independent_cc.py``; shared fixtures
and helpers are imported from ``independent_cc_support``.
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


class EntryContractTests(LaneCase):
    may_start_knowledge_service = True

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
                "disabled",
                session=first_session,
                prompt=str(uuid.uuid4()),
                cwd=project,
            )
        )
        self.assertEqual(payload.get("first_use"), "disabled")
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
        self.assertEqual(second.get("first_use"), "disabled")
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
        self.assertEqual(third.get("first_use"), "disabled")


    def test_subagent_does_not_inherit_the_parent_binding(self):
        import admission
        import entry

        _config, project = self._config(False)
        parent = str(uuid.uuid4())
        entry.dispatch_event(
            self._event(
                "mindie-agent",
                "disabled",
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
        transcript.write_text("{}\n", encoding="utf-8")
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
                adapter = json.loads(config.read_text(encoding="utf-8"))
                engine = json.loads(Path(adapter["engine_config"]).read_text(encoding="utf-8"))
                self.assertFalse(Path(engine["admission_path"]).exists())

    def test_first_entry_reports_one_missing_choice_without_independent_opt_in(self):
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
        self.assertEqual(payload["choices"], [])
        self.assertEqual(payload["experience"], "needs-configuration")
        self.assertNotIn("reporting_choice", payload)
        self.assertEqual(payload["reporting"]["status"], "needs_choice")
        self.assertFalse(payload["reporting"]["enabled"])

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
        self.assertEqual(cold["experience"], "needs-configuration")
        contribute = cold
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
                "disabled",
                session=str(uuid.uuid4()),
                prompt=str(uuid.uuid4()),
                cwd=project,
            )
        )
        self.assertEqual(path.read_bytes(), original)


class SharedConsentTests(LaneCase):
    def test_kimi_choice_is_the_same_profile_authority(self):
        """Uses a sibling adapter config in the same profile directory, not a second CC config."""
        import consent
        import support

        case = self.tmp / "both"
        case.mkdir()
        (case / "project").mkdir()
        config = support.make_config(case, sharing=False)
        kimi_config = case / "kimi.json"
        kimi_config.write_text("{}\n", encoding="utf-8")
        from mindie_knowledge import consent_store
        consent_store.record_choice(kimi_config.parent / "mindie-consent.json", "later")
        self._use(config)
        saved = consent.load()
        self.assertEqual(saved.get("choice"), "later")
        self.assertEqual(saved.get("state"), "ok")
        other = self.tmp / "isolated"
        other.mkdir()
        other_config = other / "cc.json"
        self.assertEqual(consent.load(other_config)["state"], "missing")


class DeclaredAuthorityWriteTests(LaneCase):
    """Final core context: one declared lock, no write-then-replay, corrupt bytes stay."""

    def _authority(self, name):
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

        config, legacy, canonical = self._authority("corrupt-disable")
        raw = b"{broken-community\n"
        legacy.write_bytes(raw)
        with self.assertRaises(ValueError):
            sharing.write_disabled()
        self.assertEqual(legacy.read_bytes(), raw)
        self.assertFalse(canonical.exists())
        self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["community_config"], str(legacy))


    def _assert_blocks_on_declared_lock(self, kind):
        import multiprocessing
        import time

        config, legacy, canonical = self._authority("lock-" + kind)
        ctx = multiprocessing.get_context("spawn")
        ready = ctx.Event()
        release = ctx.Event()
        entered = ctx.Event()
        holder = ctx.Process(
            target=_hold_canonical_lock,
            args=(str(legacy), ready, release),
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
            self.assertTrue((legacy.parent / (legacy.name + ".lock")).exists())
            release.set()
            worker.join(6)
            detail = ""
            error_path = config.with_suffix(".worker-error")
            if error_path.is_file():
                detail = error_path.read_text(encoding="utf-8")[-800:]
            self.assertFalse(worker.is_alive(), detail)
            self.assertEqual(worker.exitcode, 0, detail)
        finally:
            release.set()
            for proc in (worker, holder):
                if proc.is_alive():
                    proc.terminate()
                    proc.join(2)
        self.assertFalse((canonical.parent / (canonical.name + ".lock")).exists())
        self.assertTrue((legacy.parent / (legacy.name + ".lock")).exists())
        return config, legacy, canonical


    def test_disable_waits_on_the_declared_lock(self):
        _config, legacy, canonical = self._assert_blocks_on_declared_lock("disable")
        self.assertIs(json.loads(legacy.read_text(encoding="utf-8")).get("enabled"), False)
        self.assertFalse(canonical.is_file())


class BootstrapCommunityWriterTests(LaneCase):
    """setup.write_community uses the installed shared authority."""

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

    def test_missing_file_is_a_first_setup(self):
        import setup

        explicit = self.tmp / "first-explicit.json"
        self.assertFalse(explicit.exists())
        self.assertEqual(
            setup.write_community(explicit, self._explicit(self.tmp / "explicit-root")),
            "enabled",
        )
        written = json.loads(explicit.read_text(encoding="utf-8"))
        self.assertEqual(written.get("schema"), "mindie-community-config/1")
        self.assertIs(written.get("enabled"), True)
        self.assertEqual(
            Path(written["consent_config"]).resolve(),
            (explicit.parent / "mindie-consent.json").resolve(),
        )
        default_off = self.tmp / "first-off.json"
        self.assertEqual(setup.write_community(default_off, None), "off")
        off = json.loads(default_off.read_text(encoding="utf-8"))
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
        , encoding="utf-8")
        self.assertEqual(
            setup.write_community(path, self._explicit(self.tmp / "repair-root")),
            "enabled",
        )
        written = json.loads(path.read_text(encoding="utf-8"))
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

        accepted = set(identity.COMMANDS)
        committed = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        committed_matchers = {
            item.get("matcher")
            for item in committed["hooks"]["UserPromptExpansion"]
            if isinstance(item, dict)
        }
        with self.subTest("committed hooks.json"):
            self.assertEqual(sorted(accepted - committed_matchers), [])


if __name__ == "__main__":
    unittest.main()
