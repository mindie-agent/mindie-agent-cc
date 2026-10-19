"""Summary-worker contract checks: synthetic native doubles, zero provider calls.

The worker protocol (--identity plus one outcome envelope per admitted
request) is checked against the installed shared core. The native `claude`
call is doubled by a controlled fake or a patched runner; real model/host
acceptance is separate.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bounded  # noqa: E402
import organizer  # noqa: E402
from mindie_knowledge.loop.process import AGENT_ERROR_EXIT_CODES, bounded_run  # noqa: E402
from mindie_knowledge.materials import summarizer  # noqa: E402

MODEL_ENV = {"ANTHROPIC_MODEL": "fixture-model"}
ORGANIZER = Path(organizer.__file__).resolve()


def make_payload(env=None, case="CC-01", previous=None):
    """A valid admitted request for this worker; synthetic text only."""
    text = {
        "CC-01": "Initial adapter hypothesis; no independent confirmation.",
        "CC-02": "A middle correction changes the earlier interpretation.",
    }[case]
    block = dict(block_id=summarizer.digest([case, "new-block"]), text=text,
                 source_range=dict(case=case, synthetic=True))
    return summarizer.make_request(
        task_id=summarizer.digest(case), body_version=summarizer.digest([case, "current"]),
        blocks=[block], prior_navigation=previous, identity=organizer.identity(env or MODEL_ENV))


def metadata_for(payload, title="CC-case", summary="Synthetic public observations."):
    return dict(blocks=[dict(block_id=block["block_id"], title=title, summary=summary)
                        for block in payload["blocks"]],
                navigation=dict(title=title, summary=summary))


def native_envelope(metadata, *, is_error=False, num_turns=1,
                    usage=dict(input_tokens=10, cache_creation_input_tokens=3,
                               cache_read_input_tokens=2, output_tokens=5),
                    result_text=None):
    """One native terminal result object, also a one-line event stream."""
    return json.dumps(dict(
        type="result", subtype="error_during_execution" if is_error else "success",
        is_error=is_error, num_turns=num_turns,
        result="" if result_text is None else result_text,
        structured_output=metadata,
        usage=usage, session_id="native-session",
    ))


def validation_events(identifier, message, submission=None):
    return [
        {"type": "assistant", "message": {"content": [{
            "type": "tool_use", "id": identifier, "name": "StructuredOutput",
            "input": submission,
        }]}},
        {"type": "user", "message": {"content": [{
            "type": "tool_result", "tool_use_id": identifier,
            "is_error": True, "content": message,
        }]}},
    ]


def event_stream(events, terminal):
    return "\n".join([*(json.dumps(event) for event in events), terminal]) + "\n"


def fake_run(output=None, exc=None, started=True, captured=None):
    def run(argv, data, *, timeout=None, env=None, cwd=None, max_output, receipt=None, **kwargs):
        if captured is not None:
            captured.append(dict(argv=list(argv), stdin=data, timeout=timeout, env=env,
                                 cwd=cwd, max_output=max_output,
                                 allow_service=kwargs.get("allow_service", False),
                                 core_managed=kwargs.get("core_managed", False)))
        if receipt is not None:
            receipt["started"] = started
        if exc is not None:
            raise exc
        return output
    return run


class WorkerIdentityTests(unittest.TestCase):
    def test_identity_is_valid_and_scoped_to_the_native_model(self):
        identity = organizer.identity(dict(MODEL_ENV))
        self.assertIs(summarizer.validate_identity(identity), identity)
        self.assertEqual(identity["model"], "fixture-model")
        self.assertEqual(identity["effort"], "low")
        implementation = identity["implementation"]
        for name in ("organizer.py", "bounded.py", "summarizer.py", "paths.py"):
            self.assertEqual(len(implementation[name]), 64, name)
        self.assertEqual(implementation["native_command"], "claude")
        changed = organizer.identity({"ANTHROPIC_MODEL": "other-model"})
        self.assertNotEqual(identity["fingerprint"], changed["fingerprint"])

    def test_missing_model_is_a_configuration_failure(self):
        with self.assertRaises(organizer.MissingModelError):
            organizer.identity({})
        payload = make_payload()
        with patch.object(organizer, "native_environment", return_value={}):
            value = organizer.run(payload)
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["error"], "configuration")
        self.assertEqual(value["error_reason"], "configuration_invalid")
        self.assertEqual(value["model_calls"], 0)
        self.assertEqual(value["billing_status"], "not_called")
        summarizer.validate_outcome(value, payload)

    def test_no_shipped_host_model_default(self):
        self.assertFalse(hasattr(organizer, "DEFAULT_MODEL"))
        source = Path(organizer.__file__).read_text(encoding="utf-8")
        self.assertNotIn("deepseek-flash", source)
        self.assertNotIn("dsv4-flash", source)
        self.assertNotIn("DEFAULT_MODEL", source)

    def test_selected_profile_only_materializes_provider_and_native_runtime_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "settings.json").write_text(json.dumps({
                "env": {"ANTHROPIC_AUTH_TOKEN": "fixture-secret",
                        "ANTHROPIC_MODEL": "selected-model",
                        "CLAUDE_CODE_EFFORT_LEVEL": "high",
                        "CLAUDE_CODE_GIT_BASH_PATH": str(home / "selected-bash"),
                        "UNRELATED_SETTING": "must-not-import"},
                "model": "settings-model-fallback",
                "effortLevel": "settings-effort-fallback",
                "hooks": {"Stop": "must-not-run"},
            }), encoding="utf-8")
            with patch.dict(organizer.os.environ, {
                "ANTHROPIC_AUTH_TOKEN": "caller-secret",
                "ANTHROPIC_MODEL": "caller-model",
                "CLAUDE_CODE_EFFORT_LEVEL": "caller-effort",
                "CLAUDE_CODE_GIT_BASH_PATH": str(home / "caller-bash"),
            }, clear=True), patch(
                "paths.load_adapter_config", return_value={"claude_config_dir": tmp}
            ):
                env = organizer.native_environment()
            self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "fixture-secret")
            self.assertEqual(organizer.native_model(env), "selected-model")
            self.assertEqual(env["CLAUDE_CODE_EFFORT_LEVEL"], "high")
            self.assertEqual(env["CLAUDE_CODE_GIT_BASH_PATH"], str(home / "selected-bash"))
            self.assertEqual(organizer.SUMMARY_EFFORT, "low")
            self.assertNotIn("UNRELATED_SETTING", env)
            self.assertNotIn("hooks", env)

    def test_absent_native_runtime_setting_does_not_require_or_invent_git_bash(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "settings.json").write_text('{"env":{}}', encoding="utf-8")
            with patch.dict(organizer.os.environ, {}, clear=True), \
                    patch("paths.load_adapter_config", return_value={"claude_config_dir": tmp}), \
                    patch.object(organizer.shutil, "which",
                                 side_effect=AssertionError("runtime configuration must not find or install Git")):
                env = organizer.native_environment()
            self.assertNotIn("CLAUDE_CODE_GIT_BASH_PATH", env)

    def test_unreadable_profile_is_configuration_not_unknown(self):
        with patch.dict(organizer.os.environ, {}, clear=True), patch(
            "paths.load_adapter_config", side_effect=FileNotFoundError("no config")
        ):
            with self.assertRaises(organizer.ProfileUnavailableError):
                organizer.native_environment()


class WorkerEnvelopeTests(unittest.TestCase):
    def setUp(self):
        native = patch.object(organizer, "claude_bin", return_value="fixture-claude")
        native.start()
        self.addCleanup(native.stop)

    def execute(self, payload, output=None, exc=None, started=True, env=None, captured=None):
        with patch.object(organizer, "native_environment",
                          return_value=dict(env or MODEL_ENV)), \
             patch.object(organizer.bounded, "run",
                          fake_run(output, exc, started, captured)):
            return organizer.run(payload)

    def test_returned_result_usage_and_billing(self):
        payload = make_payload()
        metadata = metadata_for(payload)
        value = self.execute(payload, native_envelope(metadata))
        self.assertEqual(value["status"], "returned")
        self.assertEqual(value["result"], metadata)
        self.assertEqual(json.loads(value["raw_result"]), metadata)
        self.assertEqual(value["usage"],
                         dict(input_tokens=13, cached_input_tokens=2, output_tokens=5))
        self.assertTrue(value["usage_known"])
        self.assertEqual(value["billing_status"], "reported")
        self.assertEqual(value["model_calls"], 1)
        self.assertFalse(value["cleanup_failed"])
        summarizer.validate_outcome(value, payload)

    def test_summary_worker_remains_owned_by_its_caller(self):
        payload = make_payload()
        captured = []
        value = self.execute(payload, native_envelope(metadata_for(payload)), captured=captured)
        self.assertEqual(value["status"], "returned")
        self.assertEqual(len(captured), 1)
        self.assertIs(captured[0]["allow_service"], False)

    def test_structured_output_is_authoritative_over_prose(self):
        payload = make_payload()
        metadata = metadata_for(payload)
        value = self.execute(payload, native_envelope(metadata, result_text="Unstructured prose."))
        self.assertEqual(value["status"], "returned")
        self.assertEqual(value["result"], metadata)

    def test_missing_structured_output_does_not_repair_fenced_prose(self):
        payload = make_payload()
        blocks = metadata_for(payload)["blocks"][0]
        fenced = "```json\n" + json.dumps(blocks) + "\n```\n```json\n{\"navigation\":\"N\"}\n```"
        value = self.execute(payload, native_envelope(None, result_text=fenced))
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["error_reason"], "native_output_unreadable")
        self.assertTrue(value["usage_known"])
        self.assertEqual(value["model_calls"], 1)
        summarizer.validate_outcome(value, payload)

    def test_structured_output_still_requires_exact_shared_contract(self):
        payload = make_payload()
        metadata = metadata_for(payload)
        metadata["navigation"] = "not the required title and summary object"
        value = self.execute(payload, native_envelope(metadata))
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["error"], "invalid_result")
        self.assertTrue(value["usage_known"])
        self.assertEqual(value["model_calls"], 1)
        summarizer.validate_outcome(value, payload)

    def test_error_result_is_failed_with_retained_usage(self):
        payload = make_payload()
        value = self.execute(payload, native_envelope(metadata_for(payload), is_error=True))
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["error"], "native")
        self.assertEqual(value["error_reason"], "native_turn_failed")
        self.assertIsNone(value["result"])
        self.assertEqual(value["usage"]["input_tokens"], 13)
        self.assertTrue(value["usage_known"])
        self.assertEqual(value["billing_status"], "reported")
        summarizer.validate_outcome(value, payload)

    def test_workdir_cleanup_failure_preserves_result_and_reported_usage(self):
        payload = make_payload()
        captured = []
        try:
            with patch.object(organizer.shutil, "rmtree", side_effect=PermissionError("fixture")):
                value = self.execute(payload, native_envelope(metadata_for(payload)), captured=captured)
            self.assertEqual(value["status"], "returned")
            self.assertEqual(value["result"], metadata_for(payload))
            self.assertTrue(value["usage_known"])
            self.assertEqual(value["billing_status"], "reported")
            self.assertTrue(value["cleanup_failed"])
            self.assertEqual(value["model_calls"], 1)
            summarizer.validate_outcome(value, payload)
        finally:
            for call in captured:
                organizer.shutil.rmtree(call["cwd"], ignore_errors=True)

    def test_direct_worker_removes_its_profile_after_known_result(self):
        payload = make_payload()
        captured = []
        value = self.execute(payload, native_envelope(metadata_for(payload)), captured=captured)
        self.assertEqual(value["status"], "returned")
        self.assertTrue(value["usage_known"])
        self.assertFalse(value["cleanup_failed"])
        self.assertIs(captured[0]["core_managed"], False)
        self.assertFalse(Path(captured[0]["cwd"]).exists())

    def test_managed_worker_leaves_profile_for_its_tree_owner(self):
        payload = make_payload()
        captured = []
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "profile"
            profile.mkdir()
            with patch.object(organizer, "create_profile", return_value=(str(profile), True)), \
                    patch.object(organizer.shutil, "rmtree") as remove:
                value = self.execute(payload, native_envelope(metadata_for(payload)), captured=captured)
            remove.assert_not_called()
            self.assertIs(captured[0]["core_managed"], True)
            self.assertEqual(Path(captured[0]["cwd"]), profile)
            self.assertTrue((profile / "claude-config").is_dir())
            self.assertTrue((profile / "empty-mcp.json").is_file())
        self.assertEqual(value["status"], "returned")
        self.assertEqual(value["result"], metadata_for(payload))
        self.assertTrue(value["usage_known"])
        self.assertEqual(value["model_calls"], 1)
        self.assertFalse(value["cleanup_failed"])
        summarizer.validate_outcome(value, payload)

    def test_invalid_scratch_ownership_fails_before_native_start(self):
        payload = make_payload()
        captured = []
        with patch.object(organizer, "create_profile",
                          side_effect=organizer.ScratchOwnershipError("fixture ownership")):
            value = self.execute(payload, native_envelope(metadata_for(payload)), captured=captured)
        self.assertEqual(captured, [])
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["error"], "configuration")
        self.assertEqual(value["error_reason"], "configuration_invalid")
        self.assertEqual(value["model_calls"], 0)
        self.assertEqual(value["billing_status"], "not_called")
        summarizer.validate_outcome(value, payload)

    def test_nonzero_native_exit_is_uncertain_and_not_retried(self):
        payload = make_payload()
        value = self.execute(payload, exc=RuntimeError("command failed (7)"))
        self.assertEqual(value["status"], "outcome_unknown")
        self.assertEqual(value["error_reason"], "native_exit_nonzero")
        self.assertEqual(value["model_calls"], 1)
        self.assertEqual(value["billing_status"], "unknown")
        self.assertIsNone(value["result"])
        summarizer.validate_outcome(value, payload)

    def test_spawn_failure_is_known_uncalled(self):
        payload = make_payload()
        value = self.execute(payload, exc=OSError("no such binary"), started=False)
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["error"], "configuration")
        self.assertEqual(value["error_reason"], "native_start_failed")
        self.assertEqual(value["model_calls"], 0)
        self.assertEqual(value["billing_status"], "not_called")
        summarizer.validate_outcome(value, payload)

    def test_garbage_output_has_no_terminal_evidence(self):
        payload = make_payload()
        value = self.execute(payload, "this is not a native envelope")
        self.assertEqual(value["status"], "outcome_unknown")
        self.assertEqual(value["error"], "invalid_result")
        self.assertEqual(value["error_reason"], "result_contract_invalid")
        self.assertIsNone(value["usage"])
        summarizer.validate_outcome(value, payload)

    def test_completed_envelope_without_answer_is_a_known_failure(self):
        payload = make_payload()
        empty = json.dumps(dict(type="result", subtype="success", is_error=False,
                                num_turns=1, result="",
                                usage=dict(input_tokens=4, cache_creation_input_tokens=0,
                                           cache_read_input_tokens=0, output_tokens=1)))
        value = self.execute(payload, empty)
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["error"], "invalid_result")
        self.assertEqual(value["error_reason"], "native_output_unreadable")
        self.assertTrue(value["usage_known"])
        summarizer.validate_outcome(value, payload)

    def test_oversized_completed_metadata_keeps_known_usage(self):
        payload = make_payload()
        metadata = metadata_for(payload)
        metadata["navigation"]["summary"] = "x" * (summarizer.MAX_RESPONSE_BYTES + 1)
        value = self.execute(payload, native_envelope(metadata))
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["error"], "output_limit")
        self.assertEqual(value["model_calls"], 1)
        self.assertTrue(value["usage_known"])
        summarizer.validate_outcome(value, payload)

    def test_missing_usage_counter_stays_unknown(self):
        payload = make_payload()
        partial = dict(input_tokens=10, output_tokens=5)
        value = self.execute(payload, native_envelope(metadata_for(payload), usage=partial))
        self.assertEqual(value["status"], "returned")
        self.assertIsNone(value["usage"])
        self.assertFalse(value["usage_known"])
        self.assertEqual(value["billing_status"], "unknown")
        summarizer.validate_outcome(value, payload)

    def test_structured_tool_result_event_is_not_an_extra_model_call(self):
        payload = make_payload()
        value = self.execute(payload, native_envelope(metadata_for(payload), num_turns=2))
        self.assertEqual(value["status"], "returned")
        self.assertEqual(value["model_calls"], 1)
        self.assertTrue(value["usage_known"])
        summarizer.validate_outcome(value, payload)

    def test_native_structured_correction_preserves_accumulated_usage_in_one_invocation(self):
        payload = make_payload()
        captured = []
        usage = dict(input_tokens=40, cache_creation_input_tokens=6,
                     cache_read_input_tokens=4, output_tokens=40)
        value = self.execute(
            payload, native_envelope(metadata_for(payload), num_turns=5, usage=usage),
            captured=captured,
        )
        self.assertEqual(len(captured), 1)
        self.assertEqual(value["status"], "returned")
        self.assertEqual(value["model_calls"], 1)
        self.assertEqual(value["usage"],
                         dict(input_tokens=46, cached_input_tokens=4, output_tokens=40))
        self.assertTrue(value["usage_known"])
        self.assertEqual(value["billing_status"], "reported")
        summarizer.validate_outcome(value, payload)

    def test_native_max_turns_error_is_failed_with_known_usage(self):
        payload = make_payload()
        native = json.loads(native_envelope(None, is_error=True, num_turns=2))
        native["subtype"] = "error_max_turns"
        value = self.execute(payload, json.dumps(native))
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["error_reason"], "native_max_turns")
        self.assertEqual(value["model_calls"], 1)
        self.assertTrue(value["usage_known"])
        self.assertEqual(value["billing_status"], "reported")
        summarizer.validate_outcome(value, payload)

    def test_terminal_limits_keep_distinct_safe_diagnostics_and_one_call(self):
        payload = make_payload()
        for subtype, reason in organizer.NATIVE_FAILURE_REASONS.items():
            with self.subTest(subtype=subtype):
                native = json.loads(native_envelope(None, is_error=True, num_turns=2))
                native.update(subtype=subtype, terminal_reason="max_turns",
                              errors=["PRIVATE_PROMPT sk-secret", {"token": "SECRET_VALUE"}],
                              result="PRIVATE_RESULT", session_id="PRIVATE_SESSION",
                              api_error_status=429)
                calls = []
                value = self.execute(payload, json.dumps(native), captured=calls)
                self.assertEqual(len(calls), 1)
                self.assertEqual(value["status"], "failed")
                self.assertEqual(value["error_reason"], reason)
                self.assertIsNone(value["result"])
                self.assertTrue(value["usage_known"])
                self.assertEqual(value["billing_status"], "reported")
                self.assertEqual(value["usage"]["input_tokens"], 13)
                detail = json.loads(value["raw_result"])
                self.assertEqual(detail["schema"], "mindie-cc-native-failure/1")
                self.assertEqual(detail["subtype"], subtype)
                self.assertEqual(detail["terminal_reason"], "max_turns")
                self.assertEqual(detail["num_turns"], 2)
                self.assertEqual(detail["api_error_status"], 429)
                self.assertEqual(detail["errors"], {"type": "array", "count": 2})
                for secret in ("PRIVATE_PROMPT", "sk-secret", "SECRET_VALUE",
                               "PRIVATE_RESULT", "PRIVATE_SESSION"):
                    self.assertNotIn(secret, json.dumps(value))
                self.assertEqual(summarizer.failure_detail(value)["reason"], reason)
                summarizer.validate_outcome(value, payload)

    def test_unknown_or_malformed_failure_fields_never_become_text(self):
        payload = make_payload()
        for subtype in ("PRIVATE_SUBTYPE", ["PRIVATE_SUBTYPE"]):
            with self.subTest(subtype=subtype):
                native = json.loads(native_envelope(None, is_error=True))
                native.update(subtype=subtype, terminal_reason={"token": "SECRET"},
                              num_turns=True, api_error_status="SECRET",
                              errors={"SECRET": "PRIVATE_BODY"}, result=["PRIVATE_BODY"])
                value = self.execute(payload, json.dumps(native))
                self.assertEqual(value["status"], "failed")
                self.assertEqual(value["error_reason"], "native_turn_failed")
                detail = json.loads(value["raw_result"])
                self.assertEqual(detail["subtype"], "unknown")
                self.assertEqual(detail["terminal_reason"], "unknown")
                self.assertIsNone(detail["num_turns"])
                self.assertIsNone(detail["api_error_status"])
                self.assertNotIn("PRIVATE", json.dumps(value))
                self.assertNotIn("SECRET", json.dumps(value))
                self.assertLess(len(value["raw_result"]), 1024)
                summarizer.validate_outcome(value, payload)

    def test_native_validation_stream_retains_only_schema_owned_error_labels(self):
        payload = make_payload()
        prefix = "Output does not match required schema: "
        samples = [
            ("root: must have required property 'blocks'", "root", "required"),
            ('/blocks/0/block_id: must be equal to one of the allowed values: ["PRIVATE_ID"]',
             "/blocks/0/block_id", "enum"),
            ("/blocks/0/summary: must NOT have more than 1000 characters (got 2000)",
             "/blocks/0/summary", "maxLength"),
        ]
        events = []
        for index, (message, _, _) in enumerate(samples):
            events.extend(validation_events("PRIVATE_ID_" + str(index), prefix + message,
                                            {"PRIVATE_FIELD": "PRIVATE_VALUE"}))
        native = json.loads(native_envelope(None, is_error=True, num_turns=6))
        native.update(subtype="error_max_structured_output_retries",
                      terminal_reason="structured_output_retry_exhausted")
        value = self.execute(payload, event_stream(events, json.dumps(native)))
        self.assertEqual(value["error_reason"], "native_structured_output_exhausted")
        self.assertTrue(value["usage_known"])
        self.assertEqual(value["model_calls"], 1)
        detail = json.loads(value["raw_result"])["validation"]
        self.assertEqual(detail["submissions"], 3)
        self.assertEqual(detail["rejected"], 3)
        self.assertEqual(
            {(item["path"], item["keyword"], item["count"]) for item in detail["errors"]},
            {(path, keyword, 1) for _, path, keyword in samples},
        )
        self.assertNotIn("PRIVATE", json.dumps(value))
        summarizer.validate_outcome(value, payload)

    def test_unknown_validation_text_and_paths_are_not_diagnostic_values(self):
        payload = make_payload()
        samples = [
            "Output does not match required schema: /PRIVATE_FIELD: must be string",
            "Output does not match required schema: /blocks/0/summary: PRIVATE_VALUE",
            "PRIVATE_VALUE",
        ]
        events = []
        for index, message in enumerate(samples):
            events.extend(validation_events(str(index), message, "PRIVATE_BODY"))
        value = self.execute(payload, event_stream(events, native_envelope(None, is_error=True)))
        errors = json.loads(value["raw_result"])["validation"]["errors"]
        self.assertEqual(errors, [
            {"path": "/blocks/0/summary", "keyword": "unknown", "count": 1},
            {"path": "unknown", "keyword": "unknown", "count": 2},
        ])
        self.assertNotIn("PRIVATE", json.dumps(value))

    def test_stream_transport_budget_is_separate_from_final_metadata_budget(self):
        payload = make_payload()
        events = []
        for index in range(5):
            events.extend(validation_events(
                str(index), "Output does not match required schema: root: must have required property 'blocks'",
                {"private_value": "PRIVATE_CANARY" * 1000},
            ))
        output = event_stream(events, native_envelope(metadata_for(payload), num_turns=6))
        self.assertGreater(len(output.encode()), summarizer.MAX_RESPONSE_BYTES + 16 * 1024)
        self.assertLess(len(output.encode()), organizer.MAX_NATIVE_STREAM_BYTES)
        calls = []
        value = self.execute(payload, output, captured=calls)
        self.assertEqual(value["status"], "returned")
        self.assertEqual(value["model_calls"], 1)
        self.assertTrue(value["usage_known"])
        self.assertEqual(calls[0]["max_output"], 1024 * 1024)
        self.assertNotIn("PRIVATE_CANARY", json.dumps(value))

    def test_duplicate_or_trailing_native_terminal_is_rejected(self):
        payload = make_payload()
        terminal = native_envelope(metadata_for(payload))
        for suffix in (terminal, '{"type":"assistant","message":{}}', "not-json"):
            with self.subTest(suffix_type=suffix[:20]):
                value = self.execute(payload, terminal + "\n" + suffix + "\n")
                self.assertEqual(value["status"], "outcome_unknown")
                self.assertEqual(value["error_reason"], "result_contract_invalid")
                self.assertIsNone(value["result"])
                self.assertTrue(value["usage_known"])
                self.assertEqual(value["billing_status"], "reported")
                self.assertEqual(value["usage"],
                                 dict(input_tokens=13, cached_input_tokens=2, output_tokens=5))
                summarizer.validate_outcome(value, payload)

    def test_protocol_failure_keeps_first_terminal_usage_without_inventing_counters(self):
        payload = make_payload()
        terminal = native_envelope(metadata_for(payload))
        later = native_envelope(None, usage=dict(input_tokens=999, cache_creation_input_tokens=0,
                                               cache_read_input_tokens=0, output_tokens=999))
        value = self.execute(payload, terminal + "\n" + later + "\n")
        self.assertEqual(value["usage"]["input_tokens"], 13)
        self.assertEqual(value["usage"]["output_tokens"], 5)
        incomplete = native_envelope(metadata_for(payload), usage={"input_tokens": 10})
        value = self.execute(payload, incomplete + "\n" + later + "\n")
        self.assertFalse(value["usage_known"])
        self.assertEqual(value["billing_status"], "unknown")
        self.assertIsNone(value["result"])

    def test_only_matching_structured_output_tool_results_enter_diagnostics(self):
        payload = make_payload()
        events = validation_events("same", "PRIVATE_UNMATCHED")
        events[0]["message"]["content"][0]["name"] = "SomeOtherTool"
        value = self.execute(payload, event_stream(events, native_envelope(None, is_error=True)))
        validation = json.loads(value["raw_result"])["validation"]
        self.assertEqual(validation, {"submissions": 0, "rejected": 0, "errors": []})
        self.assertNotIn("PRIVATE", json.dumps(value))

    def test_terminal_predicate_requires_a_complete_result_line(self):
        self.assertFalse(organizer._terminal_result(b'{"type":"result"'))
        self.assertFalse(organizer._terminal_result(b'{"type":"assistant"}\n'))
        self.assertTrue(organizer._terminal_result(b'{"type":"assistant"}\n{"type":"result"}\n'))

    def test_policy_mismatch_never_calls_the_model(self):
        payload = make_payload()
        captured = []
        with patch.object(organizer, "native_environment",
                          return_value={"ANTHROPIC_MODEL": "changed-mid-flight"}), \
             patch.object(organizer.bounded, "run", fake_run(captured=captured)):
            value = organizer.run(payload)
        self.assertEqual(captured, [])
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["error"], "configuration")
        self.assertEqual(value["error_reason"], "worker_policy_changed")
        self.assertEqual(value["model_calls"], 0)
        summarizer.validate_outcome(value, payload)

    def test_one_batch_one_call_and_prompt_carries_only_public_blocks(self):
        payload = make_payload()
        captured = []
        secret = "fixture-secret-value"
        env = dict(MODEL_ENV, ANTHROPIC_AUTH_TOKEN=secret,
                   MINDIE_MAINTENANCE_GROUP="1", MINDIE_MAINTENANCE_OWNER="424242")
        env[organizer.SUMMARY_SCRATCH_ENV] = "internal-owner-declaration"
        with patch.dict(os.environ, {"MINDIE_SUMMARY_WORKER_DEADLINE": str(time.monotonic() + 35)}):
            value = self.execute(payload, native_envelope(metadata_for(payload)),
                                 env=env, captured=captured)
        self.assertEqual(value["status"], "returned")
        self.assertEqual(len(captured), 1)
        call = captured[0]
        self.assertGreater(call["timeout"], 25)
        self.assertLessEqual(call["timeout"], 30)
        self.assertEqual(call["max_output"], organizer.MAX_NATIVE_STREAM_BYTES)
        argv = call["argv"]
        self.assertIn("--print", argv)
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        self.assertIn("--verbose", argv)
        self.assertEqual(json.loads(argv[argv.index("--json-schema") + 1]),
                         summarizer.output_schema([block["block_id"] for block in payload["blocks"]]))
        self.assertNotIn("--max-turns", argv)
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "fixture-model")
        self.assertEqual(argv[argv.index("--effort") + 1], "low")
        self.assertIn("dontAsk", argv)
        self.assertNotIn(secret, call["stdin"])
        self.assertNotIn(secret, json.dumps(argv))
        self.assertIn("Initial adapter hypothesis", call["stdin"])
        self.assertIn("fallible reference index", call["stdin"])
        child_env = call["env"]
        self.assertEqual(child_env.get("ANTHROPIC_AUTH_TOKEN"), secret)
        self.assertEqual(child_env.get("CLAUDE_CODE_EFFORT_LEVEL"), "low")
        self.assertEqual(child_env.get("MAX_THINKING_TOKENS"), "0")
        self.assertNotIn("MAX_STRUCTURED_OUTPUT_RETRIES", child_env)
        self.assertNotIn("MINDIE_MAINTENANCE_GROUP", child_env)
        self.assertNotIn("MINDIE_MAINTENANCE_OWNER", child_env)
        self.assertNotIn(organizer.SUMMARY_SCRATCH_ENV, child_env)
        self.assertNotEqual(child_env.get("CLAUDE_CONFIG_DIR"), "")

    def test_expired_parent_budget_never_starts_a_paid_native_call(self):
        payload = make_payload()
        captured = []
        with patch.dict(os.environ, {"MINDIE_SUMMARY_WORKER_DEADLINE": str(time.monotonic() - 1)}):
            value = self.execute(payload, native_envelope(metadata_for(payload)), captured=captured)
        self.assertEqual(captured, [])
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["model_calls"], 0)
        self.assertEqual(value["billing_status"], "not_called")
        self.assertEqual(value["error"], "deadline")

    def test_second_model_call_is_rejected(self):
        model = organizer.NativeClaudeModel(dict(MODEL_ENV), "fixture-model", summarizer.output_schema())
        with patch.object(organizer.bounded, "run",
                          fake_run(native_envelope(dict(blocks=[], navigation={})))):
            first = model.invoke([])
            self.assertEqual(json.loads(first.content), dict(blocks=[], navigation={}))
            with self.assertRaises(organizer.NativeFailure):
                model.invoke([])


class WorkerProcessTests(unittest.TestCase):
    def run_worker(self, payload=None, env=None, args=()):
        return subprocess.run(
            [sys.executable, str(ORGANIZER), *args],
            input=json.dumps(payload).encode() if payload is not None else b"",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, timeout=30,
        )

    def test_invalid_wire_input_exits_invalid_result(self):
        proc = self.run_worker(payload={"not": "a request"})
        self.assertEqual(proc.returncode, AGENT_ERROR_EXIT_CODES["invalid_result"])
        self.assertIn("invalid_result", proc.stderr.decode())
        self.assertEqual(proc.stdout, b"")

    def test_identity_requires_a_configured_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env["MINDIE_CC_CONFIG"] = str(Path(tmp) / "missing.json")
            proc = self.run_worker(env=env, args=["--identity"])
            self.assertEqual(proc.returncode, AGENT_ERROR_EXIT_CODES["configuration"])
            self.assertIn("configuration", proc.stderr.decode())
            self.assertEqual(proc.stdout, b"")


def run_worker(payload=None, env=None, args=()):
    return subprocess.run(
        [sys.executable, str(ORGANIZER), *args],
        input=json.dumps(payload).encode() if payload is not None else b"",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, timeout=30,
    )


@unittest.skipUnless(os.name == "posix", "owned native POSIX process tree")
class WorkerNativeProcessTests(unittest.TestCase):
    """The real worker subprocess against a synthetic executable `claude`."""

    def _profile(self, root):
        import support

        config = support.make_config(Path(root) / "cfg", sharing=False)
        claude_home = Path(root) / "claude-profile"
        claude_home.mkdir()
        (claude_home / "settings.json").write_text(json.dumps({
            "env": {"ANTHROPIC_MODEL": "fixture-model"},
        }), encoding="utf-8")
        adapter = json.loads(Path(config).read_text(encoding="utf-8"))
        adapter["claude_config_dir"] = str(claude_home)
        Path(config).write_text(json.dumps(adapter, indent=2) + "\n", encoding="utf-8")
        return config

    def _fake_claude(self, root, tail="", before_result=""):
        fake = Path(root) / "fake-claude"
        fake.write_text(
            "#!" + sys.executable + "\n"
            "import json, re, sys, time\n"
            "prompt = sys.stdin.buffer.read().decode()\n"
            "ids = list(dict.fromkeys(re.findall(r'[0-9a-f]{64}', prompt)))\n"
            "metadata = dict(blocks=[dict(block_id=i, title='T', summary='S') for i in ids],\n"
            "                navigation=dict(title='N', summary='NS'))\n"
            + before_result
            + "print(json.dumps(dict(type='result', subtype='success', is_error=False, num_turns=1,\n"
            "      result='', structured_output=metadata,\n"
            "      usage=dict(input_tokens=10, cache_creation_input_tokens=3,\n"
            "                 cache_read_input_tokens=2, output_tokens=5))), flush=True)\n"
            + tail,
            encoding="utf-8",
        )
        fake.chmod(0o700)
        return fake

    def test_managed_profile_outlives_result_until_descendant_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._profile(tmp)
            info = Path(tmp) / "writer-info.json"
            inner = (
                "import os, pathlib, time\n"
                "profile = pathlib.Path.cwd()\n"
                "(profile / 'writer-ready').write_text(str(os.getpid()))\n"
                "while True:\n"
                "    (profile / 'writer-active').write_text(str(time.monotonic()))\n"
                "    if (profile / 'result-saved').exists():\n"
                "        (profile / 'writer-ack').write_text('observed saved result')\n"
                "    time.sleep(0.01)\n"
            )
            before_result = (
                "import os, pathlib, subprocess\n"
                "writer = subprocess.Popen([sys.executable, '-c', " + repr(inner) + "])\n"
                "profile = pathlib.Path.cwd()\n"
                "deadline = time.monotonic() + 5\n"
                "while not (profile / 'writer-ready').exists():\n"
                "    if writer.poll() is not None or time.monotonic() > deadline:\n"
                "        raise RuntimeError('fixture writer did not become ready')\n"
                "    time.sleep(0.01)\n"
                "pathlib.Path(" + repr(str(info)) + ").write_text(json.dumps(dict(\n"
                "    pid=writer.pid, profile=str(profile), group=os.getpgrp())))\n"
            )
            fake = self._fake_claude(tmp, "time.sleep(30)\n", before_result)
            env = dict(MINDIE_CC_CONFIG=str(config), MINDIE_CC_BIN=str(fake))
            with patch.dict(os.environ, env):
                payload = make_payload()
            saved = []
            cleanup = {}

            def save(line):
                saved.append(json.loads(line))
                writer = json.loads(info.read_text(encoding="utf-8"))
                profile = Path(writer["profile"])
                self.assertTrue((profile / "claude-config").is_dir())
                self.assertEqual(os.getpgid(writer["pid"]), writer["group"])
                (profile / "result-saved").touch()
                deadline = time.monotonic() + 3
                while not (profile / "writer-ack").exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue((profile / "writer-ack").exists(),
                                "descendant must still write when the result is saved")

            started = time.monotonic()
            with patch.dict(os.environ, env):
                raw = bounded_run([sys.executable, str(ORGANIZER)], json.dumps(payload),
                                  timeout=None, max_output=summarizer.MAX_RESPONSE_BYTES * 3,
                                  on_result=save,
                                  cleanup_receipt=cleanup)
            value = json.loads(raw)
            self.assertLess(time.monotonic() - started, 10)
            self.assertEqual(value["status"], "returned")
            self.assertEqual(saved[0]["status"], "returned")
            self.assertEqual(value["usage"],
                             dict(input_tokens=13, cached_input_tokens=2, output_tokens=5))
            self.assertTrue(value["usage_known"])
            self.assertFalse(value["cleanup_failed"])
            self.assertFalse(cleanup.get("cleanup_failed", False))
            summarizer.validate_outcome(value, payload)
            writer = json.loads(info.read_text(encoding="utf-8"))
            self.assertFalse(Path(writer["profile"]).exists())
            self.assertFalse(Path(writer["profile"]).parent.exists())
            state = subprocess.run(
                ["ps", "-o", "stat=", "-p", str(writer["pid"])],
                capture_output=True, text=True, check=False,
            ).stdout.strip()
            self.assertTrue(not state or state.startswith("Z"), state)

    def test_incomplete_managed_ownership_prevents_the_native_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._profile(tmp)
            marker = Path(tmp) / "native-started"
            fake = self._fake_claude(tmp, "open(%r, 'w').write('called')\n" % str(marker))
            env = dict(
                MINDIE_CC_CONFIG=str(config),
                MINDIE_CC_BIN=str(fake),
                # A maintenance declaration alone cannot transfer profile
                # cleanup to an owner without the complete shared protocol.
                MINDIE_MAINTENANCE_GROUP="1",
                MINDIE_MAINTENANCE_OWNER="1",
            )
            with patch.dict(os.environ, env):
                payload = make_payload()
                proc = run_worker(payload)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:400])
            value = json.loads(proc.stdout.decode())
            self.assertEqual(value["status"], "failed")
            self.assertEqual(value["error"], "configuration")
            self.assertEqual(value["error_reason"], "configuration_invalid")
            self.assertEqual(value["model_calls"], 0)
            self.assertFalse(marker.exists())
            summarizer.validate_outcome(value, payload)

    def test_terminal_failure_precedes_nonzero_native_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._profile(tmp)
            native = json.loads(native_envelope(None, is_error=True, num_turns=2))
            native.update(subtype="error_max_turns", terminal_reason="max_turns",
                          errors=["PRIVATE_NATIVE_ERROR"])
            fake = Path(tmp) / "fake-claude"
            fake.write_text(
                "#!" + sys.executable + "\nimport sys\n"
                "sys.stdin.buffer.read()\n"
                "print(" + repr(json.dumps(native)) + ", flush=True)\n"
                "sys.exit(7)\n", encoding="utf-8")
            fake.chmod(0o700)
            env = dict(MINDIE_CC_CONFIG=str(config), MINDIE_CC_BIN=str(fake))
            with patch.dict(os.environ, env):
                payload = make_payload()
                proc = run_worker(payload)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:400])
            value = json.loads(proc.stdout)
            self.assertEqual(value["status"], "failed")
            self.assertEqual(value["error_reason"], "native_max_turns")
            self.assertEqual(value["model_calls"], 1)
            self.assertTrue(value["usage_known"])
            self.assertEqual(value["usage"]["input_tokens"], 13)
            self.assertEqual(json.loads(value["raw_result"])["subtype"], "error_max_turns")
            self.assertNotIn("PRIVATE_NATIVE_ERROR", json.dumps(value))
            summarizer.validate_outcome(value, payload)


if __name__ == "__main__":
    unittest.main()
