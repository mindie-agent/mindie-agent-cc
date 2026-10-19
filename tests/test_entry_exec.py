"""Native entry forwarding uses existing ownership and actual child exit."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import bounded
import setup


def _wait_for_marker(marker, process):
    deadline = time.monotonic() + 15
    while not marker.exists():
        assert process.poll() is None, "entry exited before child readiness"
        assert time.monotonic() < deadline, "fixture readiness deadline"
        time.sleep(.01)


class EntryExecTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_published_entry_preserves_streams_exit_and_child_lifetime(self):
        tmp_path = self.root
        installed = tmp_path / "installed"
        installed.mkdir()
        shutil.copyfile(SCRIPTS / "bounded.py", installed / "bounded.py")
        marker = tmp_path / "ready"
        (installed / "mindie_launch.py").write_text(
            "import sys;from pathlib import Path;"
            f"Path({str(marker)!r}).touch();"
            "data=sys.stdin.readline();print('out:'+data.strip());"
            "print('err:retained',file=sys.stderr);raise SystemExit(17)",
            encoding="utf-8")
        entry = tmp_path / "entry.py"
        (tmp_path / "adapter.json").write_text(
            json.dumps({"base_config": str(tmp_path / "adapter.json")}))
        with patch.object(setup, "HERE", installed):
            setup.publish_entry(entry, tmp_path / "adapter.json")
        process = subprocess.Popen(
            [sys.executable, "-I", str(entry), "hook", "stop"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            _wait_for_marker(marker, process)
            assert process.poll() is None
            stdout, stderr = process.communicate(b"release\n", timeout=15)
            assert process.returncode == 17
            assert stdout.strip() == b"out:release"
            assert stderr.strip() == b"err:retained"
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    @unittest.skipUnless(os.name == "nt", "Windows exact owned Job lifetime")
    def test_entry_owner_death_stops_child_and_grandchild(self):
        tmp_path = self.root
        import ctypes
        from ctypes import wintypes

        import bounded

        marker = tmp_path / "tree.json"
        grandchild_marker = tmp_path / "grandchild"
        grandchild = (
            "import time;from pathlib import Path;"
            f"Path({str(grandchild_marker)!r}).touch();time.sleep(60)"
        )
        child = (
            "import os,sys,subprocess,json,time;from pathlib import Path;"
            f"child=subprocess.Popen([sys.executable,'-c',{grandchild!r}]);"
            f"Path({str(marker)!r}).write_text(json.dumps([os.getpid(),child.pid]));"
            "time.sleep(60)"
        )
        owner = (
            f"import sys,os;sys.path.insert(0,{str(SCRIPTS)!r});"
            "from bounded import exec_or_wait;"
            f"exec_or_wait([sys.executable,'-c',{child!r}],dict(os.environ))"
        )
        process = bounded._spawn(
            [sys.executable, "-I", "-c", owner], subprocess.DEVNULL, None, None)
        handles = []
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        try:
            _wait_for_marker(marker, process)
            _wait_for_marker(grandchild_marker, process)
            for pid in json.loads(marker.read_text(encoding="utf-8")):
                handle = kernel.OpenProcess(0x00100000, False, pid)
                assert handle
                handles.append(handle)
                assert kernel.WaitForSingleObject(handle, 0) == 258
            process.kill()  # Only kill the owner; its inner Job must close the tree.
            process.wait(timeout=5)
            for handle in handles:
                assert kernel.WaitForSingleObject(handle, 5000) == 0
        finally:
            for handle in handles:
                kernel.CloseHandle(handle)
            bounded._kill_tree(process)
            process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
