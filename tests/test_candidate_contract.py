"""Product identity and candidate failure receipts stay protocol-owned.

The validator is exercised against a synthetic source. Publication fetches and
native installation are not part of this check.
"""

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import product_contract


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "candidate_validate_under_test", SCRIPTS / "candidate_validate.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source(root, version="0.0.1"):
    plugin = root / ".claude-plugin"
    plugin.mkdir()
    (plugin / "plugin.json").write_text(json.dumps({
        "name": "mindie-agent", "version": version,
    }), encoding="utf-8")
    (root / "product-contract.json").write_text(
        (ROOT / "product-contract.json").read_text(encoding="utf-8"), encoding="utf-8",
    )
    for name in ("pyproject.toml", "uv.lock", "harness-compatibility.json"):
        (root / name).write_bytes((ROOT / name).read_bytes())
    project = root / "pyproject.toml"
    packaged_version = tomllib.loads(project.read_text(encoding="utf-8"))["project"]["version"]
    project.write_text(project.read_text(encoding="utf-8").replace(
        f'version = "{packaged_version}"', f'version = "{version}"', 1), encoding="utf-8")
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "marker.py").write_text("value = 1\n", encoding="utf-8")
    return root


class ProductContractTests(unittest.TestCase):
    def test_candidate_requires_owner_death_guard_before_install(self):
        import shutil

        validator = _load_validator()
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "candidate"
            for path in product_contract.package_files(ROOT):
                target = source / path.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
            validator.adapter_check(source)
            (source / "scripts" / "owned_process.py").unlink()
            with self.assertRaisesRegex(ValueError, "missing bounded runtime entry: owned_process.py"):
                validator.adapter_check(source)

    def test_candidate_requires_the_c6_organizer_helper(self):
        import shutil

        validator = _load_validator()
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "candidate"
            for path in product_contract.package_files(ROOT):
                target = source / path.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
            validator.adapter_check(source)
            (source / "scripts" / "organizer_distill.py").unlink()
            with self.assertRaisesRegex(
                    ValueError, "missing bounded runtime entry: organizer_distill.py"):
                validator.adapter_check(source)

    def test_identity_binds_product_requirements_and_source_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = _source(Path(tmp))
            first = product_contract.identity(source, "a" * 40)
            self.assertEqual(first["schema"], product_contract.RECEIPT_SCHEMA)
            self.assertEqual(first["candidate_revision"], "a" * 40)
            declared = json.loads((source / "product-contract.json").read_text())
            self.assertEqual(first["publication"]["contract_sha256"],
                             declared["publication"]["contract_sha256"])
            self.assertEqual(set(first["runtime"]), {"mindie-knowledge", "remote-dev", "mindie-diagnostics"})
            (source / "scripts" / "marker.py").write_text("value = 2\n", encoding="utf-8")
            second = product_contract.identity(source, "a" * 40)
            self.assertNotEqual(second["source_sha256"], first["source_sha256"])
            self.assertEqual(second["requirements_sha256"], first["requirements_sha256"])

    def test_non_release_suffix_is_rejected_by_manifest_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = _source(Path(tmp), version="0.0.1-beta.1")
            with self.assertRaises(ValueError):
                _load_validator().release_check(source)

    def test_release_metadata_does_not_select_or_sort_runtime_versions(self):
        import mindie_knowledge.state_layout as layout
        with tempfile.TemporaryDirectory() as tmp:
            source = _source(Path(tmp), version="0.0.2")
            with patch.object(layout, "RELEASE_VERSION", "9.0.0"):
                _load_validator().release_check(source)
            self.assertEqual(product_contract.identity(source)["native_version"], "0.0.2")

    def test_invalid_state_protection_metadata_is_rejected(self):
        import mindie_knowledge.state_layout as layout
        validator = _load_validator()
        with tempfile.TemporaryDirectory() as tmp:
            source = _source(Path(tmp))
            for boundary in ("invalid", "0.0.1rc1", None):
                with self.subTest(boundary=boundary), \
                        patch.object(layout, "RELEASE_VERSION", boundary):
                    with self.assertRaises(validator.CheckFailure):
                        validator.release_check(source)

    def test_native_project_and_installed_distribution_must_match(self):
        validator = _load_validator()
        with tempfile.TemporaryDirectory() as tmp:
            source = _source(Path(tmp))
            expected = product_contract.identity(source)
            with patch.object(validator, "installed_revisions"), \
                    patch.object(validator, "adapter_check"), \
                    patch.object(validator.importlib.metadata, "version", return_value="0.0.2"):
                with self.assertRaises(validator.CheckFailure) as caught:
                    validator.validate(source, expected)
                self.assertEqual(caught.exception.failure["code"], "release_version_mismatch")
            project = source / "pyproject.toml"
            project.write_text(project.read_text(encoding="utf-8").replace(
                'version = "0.0.1"', 'version = "0.0.2"', 1), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "versions differ"):
                product_contract.identity(source)

    def test_failure_receipt_rejects_success_text_from_failing_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = _source(Path(tmp))
            expected = product_contract.identity(source, "b" * 40)
            output = json.dumps(dict(expected, status="validated"))
            error = product_contract.failure_receipt(output, expected, 1)
            self.assertEqual(error.stage, "process")
            self.assertEqual(error.code, "nonzero_exit")

    def test_validator_reports_protocol_failure_without_paths(self):
        validator = _load_validator()
        with tempfile.TemporaryDirectory() as tmp:
            source = _source(Path(tmp))
            expected = product_contract.identity(source, "c" * 40)
            expected["source_sha256"] = "0" * 64
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "candidate_validate.py"),
                 "--source", str(source), "--identity", json.dumps(expected)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(completed.returncode, 1)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["failure"]["stage"], "source")
            self.assertEqual(payload["failure"]["code"], "source_changed")
            self.assertNotIn(str(source), completed.stdout)
            self.assertEqual(validator.CheckFailure("source", "source_changed").failure["code"],
                             "source_changed")

    def test_probe_preserves_captured_validator_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = _source(Path(tmp))
            scripts = source / "scripts"
            (scripts / "candidate_validate.py").write_text("print('placeholder')\n", encoding="utf-8")
            expected = product_contract.identity(source, "d" * 40)
            failure = json.dumps(dict(
                expected, status="failed",
                failure={"stage": "runtime_api", "code": "api_incompatible"},
            ))

            def command(_argv, timeout=None, max_output=65536):
                error = RuntimeError("command failed (1)")
                error.mindie_output = failure + "\n"
                error.returncode = 1
                raise error

            with self.assertRaises(product_contract.CandidateValidationError) as caught:
                product_contract.probe(sys.executable, scripts, command, revision="d" * 40)
            self.assertEqual(caught.exception.stage, "runtime_api")
            self.assertEqual(caught.exception.code, "api_incompatible")
            self.assertNotIn(str(source), str(caught.exception))


class DigestStabilityTests(unittest.TestCase):
    def test_installed_runtime_and_git_state_do_not_change_package_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = _source(Path(tmp))
            before = product_contract.source_identity(source)
            for directory in (".venv", ".git", "config", "tests"):
                path = source / directory / "unrelated"
                path.parent.mkdir()
                with path.open("wb") as stream:
                    stream.truncate(product_contract.SOURCE_LIMIT + 1)
            self.assertEqual(product_contract.source_identity(source), before)
            (source / "scripts" / "marker.py").write_text("value = 3\n")
            self.assertNotEqual(product_contract.source_identity(source), before)

    def test_source_digest_is_order_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = _source(Path(tmp))
            (source / "scripts" / "b.py").write_text("b = 1\n", encoding="utf-8")
            (source / "scripts" / "a.py").write_text("a = 1\n", encoding="utf-8")
            first = product_contract.source_identity(source)
            second = product_contract.source_identity(source)
            self.assertEqual(first, second)
            self.assertEqual(len(first), 64)
            self.assertNotEqual(first, hashlib.sha256(b"").hexdigest())


if __name__ == "__main__":
    unittest.main()
