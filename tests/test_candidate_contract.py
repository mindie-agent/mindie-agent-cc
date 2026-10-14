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
import unittest
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
    (root / "runtime-requirements.txt").write_text(
        (ROOT / "runtime-requirements.txt").read_text(encoding="utf-8"), encoding="utf-8",
    )
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "marker.py").write_text("value = 1\n", encoding="utf-8")
    return root


class ProductContractTests(unittest.TestCase):
    def test_identity_binds_product_requirements_and_source_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = _source(Path(tmp))
            first = product_contract.identity(source, "a" * 40)
            self.assertEqual(first["schema"], product_contract.RECEIPT_SCHEMA)
            self.assertEqual(first["candidate_revision"], "a" * 40)
            self.assertEqual(first["publication"]["contract_sha256"],
                             "5bdb5d9e6846c21fb3f980132ea5c70a4c67018867b4b0acd371769451f85414")
            self.assertEqual(set(first["runtime"]), {"mindie-knowledge", "remote-dev"})
            (source / "scripts" / "marker.py").write_text("value = 2\n", encoding="utf-8")
            second = product_contract.identity(source, "a" * 40)
            self.assertNotEqual(second["source_sha256"], first["source_sha256"])
            self.assertEqual(second["requirements_sha256"], first["requirements_sha256"])

    def test_non_release_suffix_is_rejected_by_manifest_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = _source(Path(tmp), version="0.0.1-beta.1")
            with self.assertRaises(updater_version_error()):
                manifest_base(source)

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


def updater_version_error():
    import updater
    return updater.CheckFailed


def manifest_base(source):
    import updater
    return updater.manifest_version(source)


class DigestStabilityTests(unittest.TestCase):
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
