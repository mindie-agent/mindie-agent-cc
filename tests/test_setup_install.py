import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SetupTests(unittest.TestCase):
    def test_setup_no_native_no_schedule(self):
        tmp = Path(tempfile.mkdtemp())
        config = tmp / "cc.json"
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "setup.py"),
                    "--config",
                    str(config),
                    "--root",
                    str(tmp / "data"),
                    "--knowledge-python",
                    sys.executable,
                    "--no-schedule",
                    "--no-native-install",
                    "--no-public-feed",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:1000] + proc.stdout.decode()[:1000])
            report = json.loads(proc.stdout.decode())
            self.assertTrue(Path(report["config"]).is_file())
            engine = json.loads(Path(report["engine_config"]).read_text())
            self.assertIn("admission_path", engine)
            self.assertNotIn("session_activation", engine)
            community = json.loads(Path(report["community_config"]).read_text())
            self.assertFalse(community["enabled"])
            self.assertFalse(Path(report["admission_path"]).exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_setup_rejects_wrong_commit_before_writing_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "candidate"
            (source / "scripts").mkdir(parents=True)
            for name in ("setup.py", "paths.py", "bounded.py"):
                shutil.copy2(ROOT / "scripts" / name, source / "scripts" / name)
            # Valid official requirement shape, deliberately wrong exact commit.
            import re
            requirements = (ROOT / "runtime-requirements.txt").read_text()
            requirements = re.sub(r"@[0-9a-f]{40}", "@" + "0" * 40, requirements, count=1)
            (source / "runtime-requirements.txt").write_text(requirements)
            config = root / "installation" / "cc.json"
            data = root / "data"
            proc = subprocess.run(
                [sys.executable, str(source / "scripts" / "setup.py"),
                 "--config", str(config), "--root", str(data),
                 "--knowledge-python", sys.executable,
                 "--no-schedule", "--no-native-install", "--no-public-feed"],
                text=True, capture_output=True, timeout=30,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("does not have the exact required commits", proc.stderr)
            self.assertFalse(config.parent.exists())
            self.assertFalse(data.exists())


if __name__ == "__main__":
    unittest.main()
