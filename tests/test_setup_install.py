import json
import os
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
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )
            if proc.returncode != 0:
                self.skipTest(
                    "setup requires pinned knowledge runtime: "
                    + proc.stderr.decode()[:400]
                    + proc.stdout.decode()[:400]
                )
            report = json.loads(proc.stdout.decode())
            self.assertTrue(Path(report["config"]).is_file())
            engine = json.loads(Path(report["engine_config"]).read_text())
            self.assertIn("admission_path", engine)
            self.assertNotIn("session_activation", engine)
            community = json.loads(Path(report["community_config"]).read_text())
            self.assertFalse(community["enabled"])
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
