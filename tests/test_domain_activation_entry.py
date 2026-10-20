"""Native slash activation selects one configured domain without touching default.

The default and target engine configs, community settings, consent files and
Admissions are real shared sources. The native identity comes only from the
verified ``UserPromptExpansion`` event fields.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for location in (ROOT / "tests", ROOT / "scripts"):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

import entry  # noqa: E402
import support  # noqa: E402
from mindie_knowledge import consent_store  # noqa: E402
from mindie_knowledge.loop.activation import Admission  # noqa: E402

SESSION = "645fae2e-ef23-4e7d-9559-f4dd0cb9db6a"
OTHER_SESSION = "745fae2e-ef23-4e7d-9559-f4dd0cb9db6b"
PROMPT = "143eccc4-86da-4095-bfa9-9b8f3416b31f"
DEFAULT_DOMAIN = "vllm-ascend"
TARGET_DOMAIN = "operators"


class DomainActivationEntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cc-domain-activation-")).resolve()
        self.default_project = self.tmp / "default-project"
        self.default_project.mkdir()
        with patch.object(support, "redactor_path", return_value=sys.executable):
            self.config = support.make_config(self.tmp, sharing=False)
            self.target_tmp = self.tmp / "target"
            self.target_tmp.mkdir()
            support.make_config(self.target_tmp, sharing=True)

        self.default_engine_path = self.tmp / "cc.engine.json"
        self.target_engine_path = self.target_tmp / "cc.engine.json"
        self.default_engine_config = json.loads(
            self.default_engine_path.read_text(encoding="utf-8"))
        target_engine = json.loads(
            self.target_engine_path.read_text(encoding="utf-8"))
        target_engine["domain"] = TARGET_DOMAIN
        self.target_engine_path.write_text(
            json.dumps(target_engine, indent=2) + "\n", encoding="utf-8")
        self.target_engine_config = json.loads(
            self.target_engine_path.read_text(encoding="utf-8"))

        default_community = json.loads(
            Path(self.default_engine_config["community_config"]).read_text(
                encoding="utf-8"))
        target_community = json.loads(
            Path(self.target_engine_config["community_config"]).read_text(
                encoding="utf-8"))
        self.target_generation = target_community["generation"]
        self.default_consent = Path(default_community["consent_config"])
        self.target_consent = Path(target_community["consent_config"])
        consent_store.record_choice(self.default_consent, "disabled")
        consent_store.record_choice(self.target_consent, "contribute")
        self.default_consent_before = self.default_consent.read_bytes()
        self.target_consent_before = self.target_consent.read_bytes()

        self.default_admission = Admission(
            self.default_engine_config["admission_path"])
        self.target_admission = Admission(
            self.target_engine_config["admission_path"])
        self.default_lease = self.default_admission.activate(
            SESSION, project_root=str(self.default_project), root_session=SESSION)

        adapter = json.loads(self.config.read_text(encoding="utf-8"))
        adapter["domain_configs"] = {
            TARGET_DOMAIN: str(self.target_engine_path.resolve()),
        }
        self.config.write_text(
            json.dumps(adapter, indent=2) + "\n", encoding="utf-8")
        self.env_patch = patch.dict(os.environ, {
            "MINDIE_CC_CONFIG": str(self.config),
            "MINDIE_DIAGNOSTICS_ROOT": str(self.tmp / "diagnostics"),
        })
        self.env_patch.start()

    def tearDown(self):
        try:
            self.env_patch.stop()
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)

    def _event(self, arguments="", *, prompt=PROMPT, cwd=None, command="mindie-agent"):
        return dict(
            hook_event_name="UserPromptExpansion",
            expansion_type="slash_command",
            command_name=command,
            command_source="plugin",
            command_args=arguments,
            session_id=SESSION,
            prompt_id=prompt,
            cwd=str(cwd or (self.target_tmp / "project")),
            transcript_path=str(self.tmp / "transcript.jsonl"),
        )

    def _target_lease(self):
        return self.target_admission.active_lease(SESSION)

    def test_native_domain_activation_adds_target_lease_only(self):
        self.assertIsNone(self._target_lease())
        payload = entry.dispatch_event(self._event("--domain operators"))

        self.assertEqual(payload["domain"], TARGET_DOMAIN)
        self.assertEqual(payload["engine_config"],
                         str(self.target_engine_path.resolve()))
        self.assertEqual(payload["generation"], self.target_generation)
        binding = payload["binding"]
        self.assertEqual(binding["session"], SESSION)
        self.assertTrue(binding["enabled"])
        self.assertEqual(binding["session"], SESSION)
        self.assertEqual(binding["root_session"], SESSION)
        self.assertEqual(binding["project_root"],
                         str((self.target_tmp / "project").resolve()))
        self.assertEqual(binding["domain"], TARGET_DOMAIN)
        self.assertEqual(binding["engine_config"],
                         str(self.target_engine_path.resolve()))
        self.assertEqual(binding["generation"], self.target_generation)

        target_lease = self._target_lease()
        self.assertIsNotNone(target_lease)
        self.assertEqual(target_lease["session"], SESSION)
        self.assertEqual(target_lease["root_session"], SESSION)
        self.assertEqual(target_lease["project_root"],
                         str((self.target_tmp / "project").resolve()))
        self.assertNotEqual(target_lease["token"], self.default_lease["token"])

        # Only the target Admission gained a lease; the default token and
        # both consent authorities are byte-for-byte unchanged.
        self.assertEqual(self.default_admission.active_lease(SESSION),
                         self.default_lease)
        self.assertEqual(len(self.default_admission.leases()), 1)
        self.assertEqual(len(self.target_admission.leases()), 1)
        self.assertEqual(self.default_consent.read_bytes(),
                         self.default_consent_before)
        self.assertEqual(self.target_consent.read_bytes(),
                         self.target_consent_before)

    def test_combined_domain_and_install_choice_refuses_before_any_write(self):
        consent_store.record_choice(self.default_consent, "contribute")
        before_consent = self.default_consent.read_bytes()
        before_lease = self.default_admission.active_lease(SESSION)
        cases = (
            "disabled --domain operators",
            "contribute --domain operators",
            "--domain operators disabled",
            "--domain operators contribute",
            "--domain operators reporting-enable",
        )
        for index, arguments in enumerate(cases, 1):
            with self.subTest(arguments=arguments):
                with self.assertRaisesRegex(ValueError, "cannot be combined"):
                    entry.dispatch_event(self._event(
                        arguments, prompt=f"combined-choice-{index}"))
                self.assertEqual(self.default_admission.active_lease(SESSION),
                                 before_lease)
                self.assertEqual(self.default_consent.read_bytes(),
                                 before_consent)
                self.assertIsNone(self._target_lease())
                self.assertFalse(
                    Path(self.target_engine_config["admission_path"]).exists())

    def test_repeated_same_scope_activation_preserves_target_token(self):
        first = entry.dispatch_event(self._event("--domain operators"))
        first_token = self._target_lease()["token"]
        second = entry.dispatch_event(self._event(
            "--domain operators",
            prompt="2c9d6a74-43c1-4edf-9b0e-f0f630b8ad9e",
        ))
        self.assertEqual(second["domain"], TARGET_DOMAIN)
        self.assertEqual(second["binding"]["generation"], self.target_generation)
        self.assertEqual(self._target_lease()["token"], first_token)
        self.assertEqual(self.default_admission.active_lease(SESSION),
                         self.default_lease)
        self.assertEqual(first["engine_config"], second["engine_config"])

    def test_bare_native_deactivate_revokes_only_current_session_across_domains(self):
        entry.dispatch_event(self._event("--domain operators"))
        self.assertEqual(self.default_admission.active_lease(SESSION),
                         self.default_lease)
        self.assertIsNotNone(self._target_lease())
        other_default = self.default_admission.activate(
            OTHER_SESSION,
            project_root=str(self.default_project),
            root_session=OTHER_SESSION,
        )
        other_target = self.target_admission.activate(
            OTHER_SESSION,
            project_root=str(self.target_tmp / "project"),
            root_session=OTHER_SESSION,
        )
        default_consent = self.default_consent.read_bytes()
        target_consent = self.target_consent.read_bytes()

        # A narrower, unsupported request must not silently revoke every domain.
        with self.assertRaisesRegex(ValueError, "takes no arguments"):
            entry.dispatch_event(self._event(
                "--domain operators",
                prompt="native-deactivate-unsupported-scope",
                command="mindie-agent:deactivate",
            ))
        self.assertIsNotNone(self.default_admission.active_lease(SESSION))
        self.assertIsNotNone(self.target_admission.active_lease(SESSION))

        result = entry.dispatch_event(self._event(
            "",
            prompt="native-deactivate-current",
            command="mindie-agent:deactivate",
        ))

        self.assertEqual(result["session"], SESSION)
        self.assertEqual(result["status"], "inactive")
        self.assertTrue(result["deactivated"])
        self.assertTrue(result["changed"])
        self.assertNotIn("partial", result)
        outcomes = {item["domain"]: item for item in result["domains"]}
        self.assertEqual(set(outcomes), {DEFAULT_DOMAIN, TARGET_DOMAIN})
        for outcome in outcomes.values():
            self.assertEqual(outcome["status"], "inactive")
            self.assertTrue(outcome["changed"])
        self.assertIsNone(self.default_admission.active_lease(SESSION))
        self.assertIsNone(self.target_admission.active_lease(SESSION))
        self.assertEqual(
            self.default_admission.active_lease(OTHER_SESSION)["token"],
            other_default["token"],
        )
        self.assertEqual(
            self.target_admission.active_lease(OTHER_SESSION)["token"],
            other_target["token"],
        )
        self.assertEqual(self.default_consent.read_bytes(), default_consent)
        self.assertEqual(self.target_consent.read_bytes(), target_consent)

    def test_partial_registered_domain_deactivation_remains_visible(self):
        entry.dispatch_event(self._event("--domain operators"))
        broken = self.tmp / "broken.engine.json"
        broken.write_text(json.dumps({
            "domain": "broken-target",
            "root": str(self.tmp / "broken"),
        }), encoding="utf-8")
        adapter = json.loads(self.config.read_text(encoding="utf-8"))
        adapter["domain_configs"]["broken-target"] = str(broken.resolve())
        self.config.write_text(
            json.dumps(adapter, indent=2) + "\n", encoding="utf-8")

        result = entry.dispatch_event(self._event(
            "",
            prompt="native-deactivate-partial",
            command="mindie-agent:deactivate",
        ))

        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["deactivated"])
        self.assertTrue(result["changed"])
        self.assertTrue(result["partial"])
        self.assertIn("partial", result["warning"].lower())
        outcomes = {item["domain"]: item for item in result["domains"]}
        for domain in (DEFAULT_DOMAIN, TARGET_DOMAIN):
            self.assertEqual(outcomes[domain]["status"], "inactive")
            self.assertTrue(outcomes[domain]["changed"])
        self.assertEqual(outcomes["broken-target"]["status"], "unavailable")
        self.assertEqual(outcomes["broken-target"]["error_class"], "ValueError")
        self.assertIn("admission_path", outcomes["broken-target"]["detail"])
        self.assertIsNone(self.default_admission.active_lease(SESSION))
        self.assertIsNone(self.target_admission.active_lease(SESSION))

    def test_unknown_and_out_of_scope_domain_refuse_without_default_fallback(self):
        before = self.default_admission.active_lease(SESSION)
        with self.assertRaisesRegex(ValueError, "unknown domain"):
            entry.dispatch_event(self._event("--domain older-operators"))
        self.assertEqual(self.default_admission.active_lease(SESSION), before)
        self.assertEqual(self.default_consent.read_bytes(),
                         self.default_consent_before)
        self.assertFalse(Path(self.target_engine_config["admission_path"]).exists())

        outside = self.tmp / "outside"
        outside.mkdir()
        with self.assertRaisesRegex(ValueError, "outside"):
            entry.dispatch_event(self._event(
                "--domain operators",
                prompt="35d4f9e3-e4d4-45a9-b4ec-8d38c3be09e2",
                cwd=outside,
            ))
        self.assertEqual(self.default_admission.active_lease(SESSION), before)
        self.assertEqual(self.default_consent.read_bytes(),
                         self.default_consent_before)
        self.assertFalse(Path(self.target_engine_config["admission_path"]).exists())

    def test_missing_or_broken_target_helper_fails_visibly(self):
        import builtins

        real_import = builtins.__import__

        def missing(name, *args, **kwargs):
            if name == "mindie_knowledge.loop.domain_routes":
                error = ModuleNotFoundError(
                    "No module named 'mindie_knowledge.loop.domain_routes'")
                error.name = name
                raise error
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=missing):
            with self.assertRaisesRegex(ValueError, "unavailable"):
                entry.dispatch_event(self._event("--domain operators"))
        self.assertEqual(self.default_admission.active_lease(SESSION),
                         self.default_lease)
        self.assertFalse(Path(self.target_engine_config["admission_path"]).exists())

        def broken(name, *args, **kwargs):
            if name == "mindie_knowledge.loop.domain_routes":
                error = ModuleNotFoundError("No module named 'internal_dependency'")
                error.name = "internal_dependency"
                raise error
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=broken):
            with self.assertRaises(ModuleNotFoundError) as caught:
                entry.dispatch_event(self._event("--domain operators"))
        self.assertEqual(caught.exception.name, "internal_dependency")


if __name__ == "__main__":
    unittest.main()
