"""Old two-file launch packaging, exercised through real Python processes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
SHA = 'a' * 40


class UpgradeBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.generation = self.root / 'generations' / SHA
        self.scripts = self.generation / 'scripts'
        self.scripts.mkdir(parents=True)
        self.launch = self.root / 'launch' / SHA
        self.launch.mkdir(parents=True)
        for name in ('mindie_launch.py', 'bounded.py'):
            shutil.copy2(SCRIPTS / name, self.launch / name)
        for name in ('diagnostic_support.py', 'diagnostic_fallback.py'):
            shutil.copy2(SCRIPTS / name, self.scripts / name)
        (self.generation / '.mindie-generation-complete').write_text(SHA + '\n')
        manifest = self.generation / 'host-package/.claude-plugin/plugin.json'
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({'name': 'mindie-agent', 'version': '0.1.0+mindie.aaaaaaaaaaaa'}))
        self.trap = self.root / 'ambient'
        self.trap.mkdir()
        (self.trap / 'diagnostic_support.py').write_text("raise AssertionError('ambient module loaded')\n")
        self.env = dict(os.environ, PYTHONPATH=str(self.trap))

    def tearDown(self):
        self.tmp.cleanup()

    def metadata(self):
        code = "import json,runpy,sys;sys.path.insert(0,sys.argv[1]);m=runpy.run_path(sys.argv[1]+'/mindie_launch.py');print(json.dumps(m['diagnostic_support'].build_metadata()))"
        return subprocess.run([sys.executable, '-c', code, str(self.launch)],
                              capture_output=True, text=True, timeout=3, env=self.env)

    def test_old_package_uses_own_complete_generation_not_current_or_environment(self):
        (self.root / 'current.json').write_text(json.dumps({'sha': 'b' * 40}))
        result = self.metadata()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'revision': SHA, 'version': '0.1.0+mindie.aaaaaaaaaaaa'})
        self.assertEqual(sorted(p.name for p in self.launch.iterdir() if p.is_file()), ['bounded.py', 'mindie_launch.py'])
        self.assertFalse((self.scripts / 'diagnostic-build.json').exists())

    def test_missing_or_mismatched_marker_does_not_import_ambient_module(self):
        marker = self.generation / '.mindie-generation-complete'
        for value in (None, 'b' * 40):
            if value is None:
                marker.unlink()
            else:
                marker.write_text(value)
            result = self.metadata()
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('ambient module loaded', result.stderr)

    def test_metadata_does_not_invent_version_without_manifest(self):
        (self.generation / 'host-package/.claude-plugin/plugin.json').unlink()
        result = self.metadata()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'revision': SHA})

    def test_sharing_off_stop_needs_no_input_or_current_generation(self):
        config = self.root / 'cc.json'
        config.write_text('{}')
        process = subprocess.Popen([sys.executable, str(self.launch / 'mindie_launch.py'),
                                    '--config', str(config), 'hook', 'stop'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=self.env)
        try:
            process.wait(timeout=2)
            self.assertEqual(process.returncode, 0, process.stderr.read().decode())
            self.assertEqual(json.loads(process.stdout.read()), {})
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
