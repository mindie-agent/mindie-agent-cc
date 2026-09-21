"""Real subprocess regression checks; these are not model acceptance."""
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time
import unittest

from mindie_knowledge.loop.process import MaintenanceCancelled, bounded_run
from tests.test_bounded import _alive


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@unittest.skipUnless(os.name == "posix", "POSIX process ownership")
class MaintenanceGroupTests(unittest.TestCase):
    def test_managed_result_does_not_kill_organizer(self):
        worker = """import json,os,sys
sys.path.insert(0,sys.argv[1])
from bounded import run
child=run([sys.executable,'-c',
    'import json,os;print(json.dumps(dict(pid=os.getpid(),pgid=os.getpgrp())))'],timeout=2)
print(json.dumps(dict(organizer=os.getpid(),pgid=os.getpgrp(),child=json.loads(child))))
"""
        result = json.loads(bounded_run(
            [sys.executable, "-c", worker, str(SCRIPTS)], "",
            timeout=5, max_output=8192))
        self.assertEqual(result["child"]["pgid"], result["pgid"])
        self.assertEqual(result["organizer"], result["pgid"])
        self.assertFalse(_alive(result["organizer"]))
        self.assertFalse(_alive(result["child"]["pid"]))

    def test_core_cancel_stops_native_and_descendant(self):
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = Path(tmp) / "pids.json"
            native = """import json,os,subprocess,sys,time
from pathlib import Path
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
Path(sys.argv[1]).write_text(json.dumps(dict(native=os.getpid(),child=child.pid)))
time.sleep(30)
"""
            worker = """import sys
sys.path.insert(0,sys.argv[1])
from bounded import run
run([sys.executable,'-c',sys.argv[2],sys.argv[3]],timeout=10)
"""
            cancel = threading.Event()
            done = threading.Event()

            def observe():
                until = time.monotonic()+4
                while time.monotonic() < until and not done.wait(.02):
                    if pidfile.is_file():
                        cancel.set()
                        return
                cancel.set()

            observer = threading.Thread(target=observe, daemon=True)
            observer.start()
            try:
                with self.assertRaises(MaintenanceCancelled):
                    bounded_run([sys.executable, "-c", worker, str(SCRIPTS),
                                 native, str(pidfile)], "",
                                timeout=6, max_output=8192, cancel=cancel)
                self.assertTrue(pidfile.is_file(), "native child was never started")
                pids = json.loads(pidfile.read_text())
                self.assertFalse(_alive(pids["native"]))
                self.assertFalse(_alive(pids["child"]))
            finally:
                done.set()
                observer.join(timeout=1)
                if pidfile.is_file():
                    for pid in json.loads(pidfile.read_text()).values():
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass


if __name__ == "__main__":
    unittest.main()
