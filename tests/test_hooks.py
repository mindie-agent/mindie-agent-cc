from contextlib import closing
from datetime import datetime, timezone
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from support import env_for, make_config, store_file

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "scripts" / "bridge.py"
LAUNCH = ROOT / "scripts" / "mindie_launch.py"

SESSION = "645fae2e-ef23-4e7d-9559-f4dd0cb9db6a"


class HookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, script, op, payload, env):
        proc = subprocess.run(
            [sys.executable, str(script), *op],
            input=json.dumps(payload).encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=5,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        return json.loads(proc.stdout.decode().splitlines()[0])

    def _min_stop_ms(self, cmd, payload_bytes, env, samples=5):
        durations_ms = []
        for _ in range(samples):
            t0 = time.perf_counter()
            proc = subprocess.run(
                cmd,
                input=payload_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                timeout=5,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self.assertEqual(proc.returncode, 0, proc.stderr.decode())
            self.assertEqual(json.loads(proc.stdout.decode() or "{}"), {})
            durations_ms.append(elapsed_ms)
        return min(durations_ms)

    def test_stop_fast_exit_under_100ms_and_zero_sqlite_when_unconfigured_disabled_or_out_of_scope(self):
        from mindie_knowledge import consent_store

        # 1. Cold unconfigured (cc.json absent)
        cold_dir = self.tmp / "cold"
        cold_dir.mkdir()
        cold_env = env_for(extra={"MINDIE_CC_CONFIG": str(cold_dir / "absent-cc.json")})
        stop_event = dict(
            hook_event_name="Stop",
            session_id=SESSION,
            prompt_id="p1",
            cwd=str(cold_dir),
            transcript_path=str(cold_dir / f"{SESSION}.jsonl"),
            stop_hook_active=False,
        )
        raw = json.dumps(stop_event).encode()
        self.assertLess(
            self._min_stop_ms([sys.executable, str(LAUNCH), "hook", "stop"], raw, cold_env),
            100.0,
        )
        self.assertEqual(list(cold_dir.rglob("*.sqlite3")), [])

        # 2. Sharing unconfigured (consent missing) & sharing explicitly disabled
        off_dir = self.tmp / "disabled"
        off_cfg = make_config(off_dir, sharing=False)
        off_env = env_for(off_cfg)
        self.assertLess(
            self._min_stop_ms([sys.executable, str(LAUNCH), "hook", "stop"], raw, off_env),
            100.0,
        )
        self.assertEqual(list(off_dir.rglob("*.sqlite3")), [])

        consent_missing_dir = self.tmp / "consent-missing"
        consent_missing_cfg = make_config(consent_missing_dir, sharing=True)
        consent_missing_env = env_for(consent_missing_cfg)
        self.assertLess(
            self._min_stop_ms([sys.executable, str(LAUNCH), "hook", "stop"], raw, consent_missing_env),
            100.0,
        )
        self.assertEqual(list(consent_missing_dir.rglob("*.sqlite3")), [])

        # 3. Sharing enabled (`contribute`) + cwd outside authorized `project_roots`
        scope_dir = self.tmp / "out-of-scope"
        scope_cfg = make_config(scope_dir, sharing=True)
        consent_store.record_choice(scope_dir / "mindie-consent.json", "contribute")
        outside = scope_dir / "outside"
        outside.mkdir()
        outside_event = dict(
            stop_event,
            cwd=str(outside),
            transcript_path=str(scope_dir / f"{SESSION}.jsonl"),
        )
        scope_env = env_for(scope_cfg)
        self.assertLess(
            self._min_stop_ms(
                [sys.executable, str(LAUNCH), "hook", "stop"],
                json.dumps(outside_event).encode(),
                scope_env,
            ),
            100.0,
        )
        # Also verify workspace_root-only out-of-scope event (cwd omitted) in both mindie_launch & bridge!
        ws_only_event = dict(
            hook_event_name="Stop",
            session_id=SESSION,
            prompt_id="p-ws-only",
            workspace_root=str(outside),
            transcript_path=str(scope_dir / f"{SESSION}.jsonl"),
            stop_hook_active=False,
        )
        self.assertLess(
            self._min_stop_ms(
                [sys.executable, str(LAUNCH), "hook", "stop"],
                json.dumps(ws_only_event).encode(),
                scope_env,
            ),
            100.0,
        )
        self.assertEqual(self._run(BRIDGE, ["stop"], ws_only_event, scope_env), {})
        self.assertEqual(list(scope_dir.rglob("*.sqlite3")), [])

    def test_first_opted_in_stop_auto_ensures_lease_refreshes_default_root_and_prepares_store(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import admission
        from mindie_knowledge import consent_store
        from mindie_knowledge.loop.lifecycle import retire_service

        opt_dir = self.tmp / "opted-in"
        claude_dir = opt_dir / "claude"
        cfg = make_config(
            opt_dir,
            sharing=True,
            summary_command=[sys.executable, "-c", "raise SystemExit(1)"],
        )
        adapter = json.loads(cfg.read_text(encoding="utf-8"))
        adapter["claude_config_dir"] = str(claude_dir)
        cfg.write_text(json.dumps(adapter, indent=2) + "\n", encoding="utf-8")
        consent_store.record_choice(opt_dir / "mindie-consent.json", "contribute")

        project = opt_dir / "project"
        stale_cwd = opt_dir / "stale-cwd"
        stale_cwd.mkdir(parents=True)
        env = env_for(cfg, {
            "CLAUDE_CONFIG_DIR": str(claude_dir),
            "MINDIE_DIAGNOSTICS_ROOT": str(opt_dir / "diag"),
            "MINDIE_DIAGNOSTICS_CONFIG": str(opt_dir / "diag.json"),
        })

        # Simulate an earlier unhooked MCP call that created a default lease with stale os.getcwd()
        with patch.dict(os.environ, env):
            stale_lease = admission.ensure_default_lease(
                SESSION, project_root=str(stale_cwd), not_before=0.0
            )
            self.assertEqual(stale_lease["activated_at"], 0.0)
            self.assertEqual(Path(stale_lease["project_root"]).resolve(), stale_cwd.resolve())

        # Note: state-v4.sqlite3 has NOT been created yet before this very first Stop!
        self.assertFalse(store_file(opt_dir).exists())
        transcript = claude_dir / "projects" / "synthetic" / f"{SESSION}.jsonl"
        transcript.parent.mkdir(parents=True)
        stamp = datetime.now(timezone.utc).isoformat()
        transcript.write_text(
            json.dumps({
                "type": "queue-operation",
                "operation": "enqueue",
                "sessionId": SESSION,
                "timestamp": stamp,
            }) + "\n" + json.dumps({
                "type": "user",
                "sessionId": SESSION,
                "cwd": str(project),
                "timestamp": stamp,
                "message": {"role": "user", "content": "Verify Ascend RMSNorm tiling"},
            }) + "\n",
            encoding="utf-8",
        )
        try:
            res = self._run(
                LAUNCH,
                ["hook", "stop"],
                dict(
                    hook_event_name="Stop",
                    session_id=SESSION,
                    prompt_id="turn-first-1",
                    cwd=str(project),
                    transcript_path=str(transcript),
                    stop_hook_active=False,
                ),
                env,
            )
            self.assertEqual(res, {})
            self.assertTrue(store_file(opt_dir).is_file())
            with closing(sqlite3.connect(store_file(opt_dir))) as db:
                rows = db.execute("SELECT turn, boundary, scope FROM captures").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], "turn-first-1")
            self.assertEqual(Path(rows[0][2]).resolve(), project.resolve())
            with patch.dict(os.environ, env):
                refreshed = admission.knowledge_lease(SESSION)
            self.assertEqual(Path(refreshed["project_root"]).resolve(), project.resolve())
        finally:
            retire_service(adapter["engine_config"])

    def test_pretool_binds_plugin_tool_and_cold_hooks_create_zero_sqlite(self):
        cold_dir = self.tmp / "cold-pretool"
        cold_dir.mkdir()
        cold_env = env_for(extra={"MINDIE_CC_CONFIG": str(cold_dir / "absent.json")})
        payload = dict(
            hook_event_name="PreToolUse",
            session_id=SESSION,
            prompt_id="p1",
            cwd=str(cold_dir),
            transcript_path=str(cold_dir / f"{SESSION}.jsonl"),
            tool_name="mcp__plugin_mindie-agent_knowledge__knowledge_query",
            tool_use_id="call_00_bindtest",
            tool_input={"query": "x"},
        )
        self.assertEqual(self._run(BRIDGE, ["pretool"], payload, cold_env), {})
        self.assertEqual(self._run(LAUNCH, ["hook", "pretool"], payload, cold_env), {})
        self.assertEqual(list(cold_dir.rglob("*.sqlite3")), [])

        config = make_config(self.tmp)
        env = env_for(config)
        result = self._run(BRIDGE, ["pretool"], payload, env)
        self.assertEqual(result, {})
        bind = self.tmp / "state" / "mcp-binds.sqlite3"
        self.assertTrue(bind.is_file())

    def test_pretool_bind_failure_fails_open_without_blocking(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import bridge

        config = make_config(self.tmp)
        payload = dict(
            hook_event_name="PreToolUse",
            session_id=SESSION,
            prompt_id="p1",
            cwd=str(self.tmp),
            transcript_path=str(self.tmp / f"{SESSION}.jsonl"),
            tool_name="mcp__plugin_mindie-agent_knowledge__knowledge_query",
            tool_use_id="call_00_bindfail",
            tool_input={"query": "x"},
        )
        stdout = StringIO()
        stderr = StringIO()
        with patch.dict(os.environ, {"MINDIE_CC_CONFIG": str(config)}), \
                patch.object(bridge, "_read_event", return_value=payload), \
                patch.object(bridge, "publish_bind", side_effect=OSError(28, "No space left on device")), \
                patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            code = bridge.handle_pretool()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue().splitlines()[0]), {})

    def test_expansion_and_prompt_are_passive_noop_without_sqlite(self):
        cold_cfg = self.tmp / "cfg"
        env = env_for(extra={
            "XDG_CONFIG_HOME": str(cold_cfg),
            "MINDIE_CC_CONFIG": str(cold_cfg / "mindie-agent" / "cc.json"),
        })
        payload = dict(
            hook_event_name="UserPromptSubmit",
            session_id=SESSION,
            prompt_id="143eccc4-86da-4095-bfa9-9b8f3416b31f",
            cwd=str(self.tmp),
            transcript_path=str(self.tmp / f"{SESSION}.jsonl"),
        )
        self.assertEqual(self._run(BRIDGE, ["expansion"], payload, env), {})
        self.assertEqual(self._run(BRIDGE, ["prompt"], payload, env), {})
        self.assertEqual(self._run(LAUNCH, ["hook", "expansion"], payload, env), {})
        self.assertEqual(list(self.tmp.rglob("*.sqlite3")), [])


if __name__ == "__main__":
    unittest.main()
