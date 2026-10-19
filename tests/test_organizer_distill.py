"""C6 organizer branch: shared prompt/schema/validation, native receipts, no paid doubles.

Every model invocation is a synthetic native result through a patched bounded
runner. The tests prepare real `mindie-distill-request/1` values through the
shared runtime, so the branch is checked against the landed C6 contract rather
than a copied prompt, schema or decision validator.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import organizer  # noqa: E402
import organizer_distill  # noqa: E402

try:
    from mindie_knowledge.graded import distill
except ImportError:  # The 0.0.2 runtime has no C6 API; this lane needs joint source.
    raise unittest.SkipTest("joint C6 runtime is not on PYTHONPATH")

from mindie_knowledge.loop.process import AGENT_ERROR_EXIT_CODES  # noqa: E402
from mindie_knowledge.materials import summarizer  # noqa: E402

MODEL_ENV = {"ANTHROPIC_MODEL": "fixture-model"}
ORGANIZER = Path(organizer.__file__).resolve()
RAW_USAGE = dict(input_tokens=10, cache_creation_input_tokens=3,
                 cache_read_input_tokens=2, output_tokens=5)
KNOWN_USAGE = dict(input_tokens=13, cached_input_tokens=2, output_tokens=5)


def missing_distill_api():
    """Simulate the 0.0.2 runtime where `graded.distill` does not exist."""
    import builtins

    real = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "mindie_knowledge.graded" and fromlist and "distill" in fromlist:
            raise ImportError("simulated 0.0.2 runtime")
        if name == "mindie_knowledge.graded.distill":
            raise ImportError("simulated 0.0.2 runtime")
        return real(name, globals, locals, fromlist, level)

    return patch("builtins.__import__", side_effect=guarded)


def fake_redactor(root):
    """A local scanner double; no network or real secret scanning."""
    path = Path(root) / "redactor"
    path.write_text(
        "#!" + sys.executable + "\n"
        "import sys\n"
        "sys.stdin.buffer.read()\n"
        "print('[]')\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


def make_request(task_state="verified_milestone", outcome="verified", repetitions=2):
    with tempfile.TemporaryDirectory() as tmp:
        request = distill.prepare_request(
            [dict(kind="experiment_ledger", outcome=outcome,
                  source_locator="ledger://fixture-run-1",
                  content="Observed the fixture rule under the stated conditions.",
                  evidence_refs=["run-1"], repetitions=repetitions)],
            session_digest=summarizer.digest(["distill-fixture", task_state, outcome]),
            task_state=task_state,
            redactor_executable=str(fake_redactor(tmp)),
            redaction_key="k" * 32,
        )
    return request


def decision_for(request):
    selected = request["sanitized_inputs"][0]
    frontmatter = {
        "schema": "mindie-graded-unit/1",
        "id": "unit-fixture-observed-rule",
        "tier": "L2",
        "category": "sop",
        "evidence_strength": "single_observation",
        "domain_path": "ops/fixture",
        "inherits_from": [],
        "title": "Fixture observed rule",
        "triggers": {"error_signatures": ["FIXTURE_ERROR"], "keywords": ["fixture"]},
        "conditions": {"soc": ["Ascend910B"]},
        "provenance": {
            "source_type": "session_distill",
            "source_locator": selected["source_locator"],
            "source_digest": selected["source_digest"],
            "evidence_refs": ["run-1"],
            "supersedes": [],
        },
    }
    return {
        "schema_version": "mindie-distill-decision/1",
        "session_digest": request["session_digest"],
        "evidence_inputs": request["evidence_inputs"],
        "action": "propose_graded_units",
        "drop_reason": None,
        "units": [{
            "target_tier": "L2",
            "category": "sop",
            "evidence_strength": "single_observation",
            "domain_path": "ops/fixture",
            "operation": "create",
            "target_unit_id": "unit-fixture-observed-rule",
            "frontmatter": frontmatter,
            "markdown_body": "Apply the observed rule only under the declared conditions.",
        }],
    }


def native_envelope(metadata, *, is_error=False, usage=RAW_USAGE):
    return json.dumps(dict(
        type="result",
        subtype="error_during_execution" if is_error else "success",
        is_error=is_error,
        num_turns=1,
        result="",
        structured_output=metadata,
        usage=usage,
        session_id="native-session",
    ))


def fake_run(output=None, exc=None, started=True, captured=None):
    def run(argv, data, *, timeout=None, env=None, cwd=None, max_output,
            receipt=None, **kwargs):
        if captured is not None:
            captured.append(dict(argv=list(argv), stdin=data, cwd=cwd,
                                 max_output=max_output, env=env))
        if receipt is not None:
            receipt["started"] = started
        if exc is not None:
            raise exc
        return output
    return run


def worker_request(request):
    value = distill.with_worker_identity(request, organizer.identity(dict(MODEL_ENV)))
    value["sanitized_inputs"] = request["sanitized_inputs"]
    return value


class DistillWorkerTests(unittest.TestCase):
    def setUp(self):
        native = patch.object(organizer, "claude_bin", return_value="fixture-claude")
        native.start()
        self.addCleanup(native.stop)

    def execute(self, request, output=None, exc=None, started=True, captured=None):
        with patch.object(organizer, "native_environment",
                          return_value=dict(MODEL_ENV)), \
             patch.object(organizer.bounded, "run",
                          fake_run(output, exc, started, captured)):
            return organizer.run(request)

    def assert_worker_outcome(self, value, request):
        summarizer.validate_outcome(value, worker_request(request))

    def test_identity_declares_the_landed_c6_capability(self):
        identity = organizer.identity(dict(MODEL_ENV))
        self.assertEqual(identity["capabilities"], [distill.REQUEST_SCHEMA])
        self.assertIs(summarizer.validate_identity(identity), identity)

    def test_missing_shared_api_has_no_capability_and_summary_still_runs(self):
        from tests.test_organizer import make_payload, metadata_for

        with missing_distill_api():
            identity = organizer.identity(dict(MODEL_ENV))
            self.assertNotIn("capabilities", identity)
            payload = make_payload()
            with patch.object(organizer, "native_environment",
                              return_value=dict(MODEL_ENV)), \
                 patch.object(organizer.bounded, "run",
                              fake_run(native_envelope(metadata_for(payload)))):
                value = organizer.run(payload)
        self.assertEqual(value["status"], "returned")
        summarizer.validate_outcome(value, payload)

    def test_success_uses_shared_prompt_schema_and_decision_validation(self):
        request = make_request()
        decision = decision_for(request)
        captured = []
        value = self.execute(request, native_envelope(decision), captured=captured)

        self.assertEqual(value["status"], "returned")
        self.assertEqual(value["result"], decision)
        self.assertTrue(value["usage_known"])
        self.assertEqual(value["usage"], KNOWN_USAGE)
        self.assertEqual(value["billing_status"], "reported")
        self.assertEqual(value["model_calls"], 1)
        self.assertFalse(value["cleanup_failed"])
        self.assertEqual(value["task_id"], request["session_digest"])
        self.assertTrue(value["body_version"].startswith("distill:"))
        self.assertEqual(distill.validate_decision(value["result"], request), decision)
        self.assert_worker_outcome(value, request)

        self.assertEqual(len(captured), 1)
        call = captured[0]
        argv = call["argv"]
        self.assertEqual(json.loads(argv[argv.index("--json-schema") + 1]),
                         distill.decision_schema())
        self.assertEqual(argv[argv.index("--model") + 1], "fixture-model")
        self.assertEqual(call["stdin"], distill.build_prompt(request))
        self.assertIn("Never claim that Stop means completion", call["stdin"])

    def test_engine_prepared_worker_request_round_trips_with_matching_identity(self):
        request = make_request()
        decision = decision_for(request)
        worker = distill.with_worker_identity(request, organizer.identity(dict(MODEL_ENV)))
        captured = []
        value = self.execute(worker, native_envelope(decision), captured=captured)
        self.assertEqual(len(captured), 1)
        self.assertEqual(value["status"], "returned")
        self.assertEqual(value["result"], decision)
        self.assertEqual(value["policy_identity"], worker["policy_identity"])
        self.assertEqual(value["batch_id"], worker["batch_id"])
        self.assertEqual(value["input_digest"], worker["input_digest"])
        summarizer.validate_outcome(value, worker)

    def test_in_flight_returns_noop_without_a_paid_model_call(self):
        for task_state in ("in_flight", "verified_milestone"):
            with self.subTest(task_state=task_state):
                outcome = "verified" if task_state == "in_flight" else "in_flight"
                request = make_request(task_state=task_state, outcome=outcome, repetitions=1)
                with patch.object(organizer, "native_environment",
                                  return_value=dict(MODEL_ENV)), \
                     patch.object(organizer.bounded, "run",
                                  side_effect=AssertionError("in-flight must not invoke")):
                    value = organizer.run(request)
                self.assertEqual(value["status"], "returned")
                self.assertEqual(value["result"]["action"], "noop")
                self.assertEqual(value["result"]["drop_reason"], "in_flight_incomplete")
                self.assertIsNone(value["usage"])
                self.assertFalse(value["usage_known"])
                self.assertEqual(value["model_calls"], 0)
                self.assertEqual(value["billing_status"], "not_called")
                self.assert_worker_outcome(value, request)

    def test_spawn_failure_before_send_is_known_uncalled(self):
        request = make_request()
        captured = []
        value = self.execute(request, exc=OSError("no such binary"),
                             started=False, captured=captured)
        self.assertEqual(len(captured), 1)
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["error"], "configuration")
        self.assertEqual(value["error_reason"], "native_start_failed")
        self.assertIsNone(value["result"])
        self.assertEqual(value["model_calls"], 0)
        self.assertEqual(value["billing_status"], "not_called")
        self.assert_worker_outcome(value, request)

    def test_policy_drift_is_known_before_the_paid_call(self):
        request = make_request()
        worker = distill.with_worker_identity(request, organizer.identity(dict(MODEL_ENV)))
        worker["policy_identity"] = organizer.identity({"ANTHROPIC_MODEL": "changed-mid-flight"})
        worker["sanitized_inputs"] = request["sanitized_inputs"]
        with patch.object(organizer, "native_environment",
                          return_value=dict(MODEL_ENV)), \
             patch.object(organizer.bounded, "run",
                          side_effect=AssertionError("policy drift must not invoke")):
            value = organizer.run(worker)
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["error"], "configuration")
        self.assertEqual(value["error_reason"], "worker_policy_changed")
        self.assertEqual(value["model_calls"], 0)
        self.assertEqual(value["billing_status"], "not_called")
        summarizer.validate_outcome(value, worker)

    def test_send_without_terminal_evidence_is_outcome_unknown(self):
        request = make_request()
        value = self.execute(request, exc=RuntimeError("command failed (7)"),
                             started=True)
        self.assertEqual(value["status"], "outcome_unknown")
        self.assertEqual(value["error_reason"], "native_exit_nonzero")
        self.assertEqual(value["model_calls"], 1)
        self.assertEqual(value["billing_status"], "unknown")
        self.assertIsNone(value["result"])
        self.assert_worker_outcome(value, request)

    def test_native_error_terminal_keeps_usage_and_fails(self):
        request = make_request()
        value = self.execute(request, native_envelope(None, is_error=True))
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["error"], "native")
        self.assertEqual(value["error_reason"], "native_turn_failed")
        self.assertEqual(value["usage"], KNOWN_USAGE)
        self.assertTrue(value["usage_known"])
        self.assertEqual(value["billing_status"], "reported")
        self.assertEqual(value["model_calls"], 1)
        self.assertIsNone(value["result"])
        self.assertEqual(json.loads(value["raw_result"])["schema"],
                         "mindie-cc-native-failure/1")
        self.assert_worker_outcome(value, request)

    def test_missing_usage_counter_stays_unknown(self):
        request = make_request()
        value = self.execute(request, native_envelope(
            decision_for(request), usage={"input_tokens": 10}))
        self.assertEqual(value["status"], "returned")
        self.assertIsNone(value["usage"])
        self.assertFalse(value["usage_known"])
        self.assertEqual(value["billing_status"], "unknown")
        self.assertEqual(value["model_calls"], 1)
        self.assert_worker_outcome(value, request)

    def test_invalid_decision_after_a_paid_call_keeps_usage_and_one_call(self):
        request = make_request()
        captured = []
        value = self.execute(request, native_envelope(
            {"schema_version": "mindie-distill-decision/1"}), captured=captured)
        self.assertEqual(len(captured), 1)
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["error"], "invalid_result")
        self.assertEqual(value["error_reason"], "native_output_unreadable")
        self.assertEqual(value["usage"], KNOWN_USAGE)
        self.assertTrue(value["usage_known"])
        self.assertEqual(value["billing_status"], "reported")
        self.assertEqual(value["model_calls"], 1)
        self.assertIsNone(value["result"])
        self.assert_worker_outcome(value, request)

    def test_forged_provenance_is_rejected_without_replaying_the_call(self):
        request = make_request()
        decision = decision_for(request)
        unit = decision["units"][0]
        unit["frontmatter"]["provenance"]["source_digest"] = "0" * 64
        unit["frontmatter"]["provenance"]["evidence_refs"] = ["invented-run"]
        captured = []
        value = self.execute(request, native_envelope(decision), captured=captured)
        self.assertEqual(len(captured), 1)
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["error_reason"], "native_output_unreadable")
        self.assertEqual(value["model_calls"], 1)
        self.assertTrue(value["usage_known"])
        self.assertEqual(value["billing_status"], "reported")
        self.assert_worker_outcome(value, request)

    def test_local_cleanup_failure_does_not_reverse_the_returned_decision(self):
        request = make_request()
        decision = decision_for(request)
        captured = []
        try:
            with patch.object(organizer.shutil, "rmtree",
                              side_effect=PermissionError("fixture")):
                value = self.execute(request, native_envelope(decision), captured=captured)
            self.assertEqual(value["status"], "returned")
            self.assertEqual(value["result"], decision)
            self.assertTrue(value["usage_known"])
            self.assertEqual(value["billing_status"], "reported")
            self.assertTrue(value["cleanup_failed"])
            self.assertEqual(value["model_calls"], 1)
            self.assert_worker_outcome(value, request)
        finally:
            for call in captured:
                if call.get("cwd"):
                    organizer.shutil.rmtree(call["cwd"], ignore_errors=True)

    def test_selected_sanitized_inputs_are_the_only_material_read(self):
        request = make_request()
        decision = decision_for(request)
        captured = []
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "experiment-ledger.jsonl").write_text(
                "FILESYSTEM_CANARY must never be read", encoding="utf-8")
            previous = os.getcwd()
            os.chdir(tmp)
            try:
                value = self.execute(request, native_envelope(decision), captured=captured)
            finally:
                os.chdir(previous)
        self.assertEqual(value["status"], "returned")
        self.assertNotIn("FILESYSTEM_CANARY", captured[0]["stdin"])
        self.assertEqual(captured[0]["stdin"], distill.build_prompt(request))

    def test_unsupported_schema_is_explicit_and_does_not_choose_summary(self):
        with patch.object(organizer.bounded, "run",
                          side_effect=AssertionError("unsupported schema must not invoke")):
            with self.assertRaises(organizer.UnsupportedSchemaError) as raised:
                organizer.run({"schema_version": "mindie-distill-request/2"})
        self.assertEqual(raised.exception.reason, "unsupported_schema")

    def test_request_without_an_explicit_task_state_is_rejected_before_paid_work(self):
        request = make_request()
        request["task_state"] = "finished-by-stop"
        base = {key: value for key, value in request.items()
                if key in distill.BASE_REQUEST_KEYS and key != "request_digest"}
        request["request_digest"] = summarizer.digest(base)
        with patch.object(organizer.bounded, "run",
                          side_effect=AssertionError("invalid state must not invoke")):
            with self.assertRaises(organizer_distill.RequestInvalid) as raised:
                organizer.run(request)
        self.assertEqual(raised.exception.reason, "request_invalid")

    def test_distill_request_fails_closed_when_shared_api_disappears(self):
        request = make_request()
        with missing_distill_api(), \
             patch.object(organizer.bounded, "run",
                          side_effect=AssertionError("missing API must not invoke")):
            with self.assertRaises(organizer_distill.DistillUnavailable) as raised:
                organizer.run(request)
        self.assertEqual(raised.exception.reason, "dependency_unavailable")

    def test_summary_branch_still_runs_without_the_distill_companion(self):
        from tests.test_organizer import make_payload, metadata_for

        with patch.dict(sys.modules, {"organizer_distill": None}):
            payload = make_payload()
            with patch.object(organizer, "native_environment",
                              return_value=dict(MODEL_ENV)), \
                 patch.object(organizer.bounded, "run",
                              fake_run(native_envelope(metadata_for(payload)))):
                value = organizer.run(payload)
        self.assertEqual(value["status"], "returned")
        self.assertEqual(value["result"], metadata_for(payload))
        summarizer.validate_outcome(value, payload)


class DistillProcessTests(unittest.TestCase):
    def test_unknown_schema_exits_with_a_named_protocol_failure(self):
        proc = subprocess.run(
            [sys.executable, str(ORGANIZER)],
            input=json.dumps({"schema_version": "mindie-distill-request/2"}).encode(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
        self.assertEqual(proc.returncode, AGENT_ERROR_EXIT_CODES["invalid_result"])
        self.assertEqual(proc.stdout, b"")
        self.assertIn(b"unsupported_schema", proc.stderr)

    def test_c6_request_round_trips_through_the_worker_process(self):
        request = make_request()
        decision = decision_for(request)
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "claude-profile"
            profile.mkdir()
            (profile / "settings.json").write_text(json.dumps({
                "env": {"ANTHROPIC_MODEL": "fixture-model"},
            }), encoding="utf-8")
            adapter = Path(tmp) / "adapter.json"
            adapter.write_text(json.dumps({
                "claude_config_dir": str(profile),
            }), encoding="utf-8")
            fake = Path(tmp) / "fake-claude"
            fake.write_text(
                "#!" + sys.executable + "\n"
                "import sys\n"
                "sys.stdin.buffer.read()\n"
                "print(" + repr(native_envelope(decision)) + ", flush=True)\n",
                encoding="utf-8",
            )
            fake.chmod(0o700)
            env = dict(os.environ)
            env["MINDIE_CC_CONFIG"] = str(adapter)
            env["MINDIE_CC_BIN"] = str(fake)
            proc = subprocess.run(
                [sys.executable, str(ORGANIZER)],
                input=summarizer.canonical(request).encode(),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=env, timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:400])
            value = json.loads(proc.stdout)
            self.assertEqual(value["status"], "returned")
            self.assertEqual(value["result"], decision)
            self.assertEqual(value["usage"], KNOWN_USAGE)
            self.assertTrue(value["usage_known"])
            self.assertEqual(value["billing_status"], "reported")
            self.assertEqual(distill.validate_decision(value["result"], request), decision)
            with patch.dict(os.environ, {"MINDIE_CC_BIN": str(fake)}):
                worker = distill.with_worker_identity(
                    request, organizer.identity(dict(MODEL_ENV)))
                worker["sanitized_inputs"] = request["sanitized_inputs"]
                summarizer.validate_outcome(value, worker)

    def test_identity_advertises_c6_without_a_model_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "claude-profile"
            profile.mkdir()
            (profile / "settings.json").write_text(json.dumps({
                "env": {"ANTHROPIC_MODEL": "fixture-model"},
            }), encoding="utf-8")
            env = dict(os.environ)
            env["MINDIE_CC_CONFIG"] = str(Path(tmp) / "adapter.json")
            Path(env["MINDIE_CC_CONFIG"]).write_text(json.dumps({
                "claude_config_dir": str(profile),
            }), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(ORGANIZER), "--identity"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=env, timeout=30,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:400])
        value = json.loads(proc.stdout)
        self.assertEqual(value["capabilities"], [distill.REQUEST_SCHEMA])
        self.assertIs(summarizer.validate_identity(value), value)


if __name__ == "__main__":
    unittest.main()
