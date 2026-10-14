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
    """One `claude --print --output-format json` object."""
    return json.dumps(dict(
        type="result", subtype="error_during_execution" if is_error else "success",
        is_error=is_error, num_turns=num_turns,
        result=json.dumps(metadata) if result_text is None else result_text,
        usage=usage, session_id="native-session",
    ))


def fake_run(output=None, exc=None, started=True, captured=None):
    def run(argv, data, *, timeout=None, env=None, cwd=None, max_output, receipt=None, **kwargs):
        if captured is not None:
            captured.append(dict(argv=list(argv), stdin=data, timeout=timeout, env=env,
                                 cwd=cwd, max_output=max_output))
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
        for name in ("organizer.py", "bounded.py", "summarizer.py"):
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

    def test_selected_profile_only_materializes_provider_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "settings.json").write_text(json.dumps({
                "env": {"ANTHROPIC_AUTH_TOKEN": "fixture-secret",
                        "ANTHROPIC_MODEL": "selected-model",
                        "CLAUDE_CODE_EFFORT_LEVEL": "high",
                        "UNRELATED_SETTING": "must-not-import"},
                "hooks": {"Stop": "must-not-run"},
            }), encoding="utf-8")
            with patch.dict(organizer.os.environ, {}, clear=True), patch(
                "paths.load_adapter_config", return_value={"claude_config_dir": tmp}
            ):
                env = organizer.native_environment()
            self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "fixture-secret")
            self.assertEqual(organizer.native_model(env), "selected-model")
            self.assertEqual(organizer.SUMMARY_EFFORT, "low")
            self.assertNotIn("UNRELATED_SETTING", env)
            self.assertNotIn("hooks", env)

    def test_unreadable_profile_is_configuration_not_unknown(self):
        with patch.dict(organizer.os.environ, {}, clear=True), patch(
            "paths.load_adapter_config", side_effect=FileNotFoundError("no config")
        ):
            with self.assertRaises(organizer.ProfileUnavailableError):
                organizer.native_environment()


class WorkerEnvelopeTests(unittest.TestCase):
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

    def test_fenced_result_is_adapted_then_strictly_validated(self):
        payload = make_payload()
        metadata = metadata_for(payload)
        fenced = "```json\n" + json.dumps(metadata) + "\n```"
        value = self.execute(payload, native_envelope(metadata, result_text=fenced))
        self.assertEqual(value["status"], "returned")
        self.assertEqual(value["result"], metadata)
        prose = native_envelope(metadata, result_text="not json at all")
        value = self.execute(payload, prose)
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["error_reason"], "result_json_invalid")
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

    def test_extra_turn_is_rejected(self):
        payload = make_payload()
        value = self.execute(payload, native_envelope(metadata_for(payload), num_turns=2))
        self.assertEqual(value["status"], "outcome_unknown")
        self.assertEqual(value["error_reason"], "native_extra_turn")
        summarizer.validate_outcome(value, payload)

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
        value = self.execute(payload, native_envelope(metadata_for(payload)),
                             env=env, captured=captured)
        self.assertEqual(value["status"], "returned")
        self.assertEqual(len(captured), 1)
        call = captured[0]
        self.assertIsNone(call["timeout"])
        self.assertEqual(call["max_output"], organizer.MAX_ENVELOPE_BYTES)
        argv = call["argv"]
        self.assertIn("--print", argv)
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
        self.assertNotIn("MINDIE_MAINTENANCE_GROUP", child_env)
        self.assertNotIn("MINDIE_MAINTENANCE_OWNER", child_env)
        self.assertNotEqual(child_env.get("CLAUDE_CONFIG_DIR"), "")

    def test_second_model_call_is_rejected(self):
        model = organizer.NativeClaudeModel(dict(MODEL_ENV), "fixture-model")
        with patch.object(organizer.bounded, "run",
                          fake_run(native_envelope(dict(blocks=[], navigation={})))):
            first = model.invoke([])
            self.assertEqual(first.content, json.dumps(dict(blocks=[], navigation={})))
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
            env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
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

    def _fake_claude(self, root, tail=""):
        fake = Path(root) / "fake-claude"
        fake.write_text(
            "#!" + sys.executable + "\n"
            "import json, re, sys, time\n"
            "prompt = sys.stdin.buffer.read().decode()\n"
            "ids = list(dict.fromkeys(re.findall(r'[0-9a-f]{64}', prompt)))\n"
            "metadata = dict(blocks=[dict(block_id=i, title='T', summary='S') for i in ids],\n"
            "                navigation=dict(title='N', summary='NS'))\n"
            "print(json.dumps(dict(type='result', subtype='success', is_error=False, num_turns=1,\n"
            "      result=json.dumps(metadata),\n"
            "      usage=dict(input_tokens=10, cache_creation_input_tokens=3,\n"
            "                 cache_read_input_tokens=2, output_tokens=5))), flush=True)\n"
            + tail,
            encoding="utf-8",
        )
        fake.chmod(0o700)
        return fake

    def test_envelope_delivery_and_descendant_reaping(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._profile(tmp)
            late = Path(tmp) / "descendant-survived"
            inner = "import time;time.sleep(3);open(" + repr(str(late)) + ", 'w').write('late')"
            tail = (
                "import subprocess\n"
                "subprocess.Popen([sys.executable, '-c', " + repr(inner) + "])\n"
                "time.sleep(30)\n"
            )
            fake = self._fake_claude(tmp, tail)
            env = dict(MINDIE_CC_CONFIG=str(config), MINDIE_CC_BIN=str(fake))
            with patch.dict(os.environ, env):
                payload = make_payload()
            saved = []
            cleanup = {}
            started = time.monotonic()
            with patch.dict(os.environ, env):
                raw = bounded_run([sys.executable, str(ORGANIZER)], json.dumps(payload),
                                  timeout=None, max_output=summarizer.MAX_RESPONSE_BYTES * 3,
                                  on_result=lambda line: saved.append(json.loads(line)),
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
            time.sleep(3.5)
            self.assertFalse(late.exists())

    def test_owner_death_prevents_the_native_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._profile(tmp)
            marker = Path(tmp) / "native-started"
            fake = self._fake_claude(tmp, "open(%r, 'w').write('called')\n" % str(marker))
            env = dict(
                MINDIE_CC_CONFIG=str(config),
                MINDIE_CC_BIN=str(fake),
                # A surviving owner pid different from the worker's parent: the
                # worker must refuse to start the native call.
                MINDIE_MAINTENANCE_GROUP="1",
                MINDIE_MAINTENANCE_OWNER="1",
            )
            with patch.dict(os.environ, env):
                payload = make_payload()
                proc = run_worker(payload)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:400])
            value = json.loads(proc.stdout.decode())
            self.assertEqual(value["status"], "failed")
            self.assertEqual(value["error_reason"], "native_owner_exited")
            self.assertEqual(value["model_calls"], 0)
            self.assertFalse(marker.exists())
            summarizer.validate_outcome(value, payload)


if __name__ == "__main__":
    unittest.main()
