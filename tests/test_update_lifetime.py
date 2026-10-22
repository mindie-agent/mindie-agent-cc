"""Caller lifetime, exact native reconciliation, and final feed delivery."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

from support import SCRIPTS, make_config
import process_probe

sys.path.insert(0, str(SCRIPTS))
import bounded
import capture_config


def _alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return code.value == 259
    return process_probe.alive(pid)


def test_scoped_cancel_reaps_owned_child_and_grandchild(tmp_path):
    marker = tmp_path / "pids.json"
    program = (
        "import os,subprocess,sys,time,json;"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
        f"open({str(marker)!r},'w').write(json.dumps([os.getpid(),p.pid]));"
        "time.sleep(30)"
    )
    cancel = threading.Event()
    def stop():
        limit = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < limit:
            time.sleep(.02)
        cancel.set()
    thread = threading.Thread(target=stop)
    thread.start()
    with bounded.operation(cancel), pytest.raises(bounded.CommandCancelled):
        bounded.run([sys.executable, "-c", program])
    thread.join()
    pids = json.loads(marker.read_text())
    limit = time.monotonic() + 3
    while any(_alive(pid) for pid in pids) and time.monotonic() < limit:
        time.sleep(.02)
    assert not any(_alive(pid) for pid in pids)


@pytest.mark.skipif(os.name != "posix", reason="POSIX owner pipe; Windows uses owned Job")
def test_hard_owner_death_reaps_child_and_pipe_holder(tmp_path):
    marker = tmp_path / "pids.json"
    target = (
        "import os,subprocess,sys,time,json;"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
        f"open({str(marker)!r},'w').write(json.dumps([os.getpid(),p.pid]));time.sleep(30)"
    )
    parent = (
        f"import sys;sys.path.insert(0,{str(SCRIPTS)!r});import bounded;"
        "scope=bounded.operation();scope.__enter__();"
        f"bounded.run([sys.executable,'-c',{target!r}])"
    )
    process = subprocess.Popen([sys.executable, "-c", parent],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        limit = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < limit:
            time.sleep(.02)
        pids = json.loads(marker.read_text())
        assert all(_alive(pid) for pid in pids)
        process.kill()
        process.wait(timeout=3)
        limit = time.monotonic() + 3
        while any(_alive(pid) for pid in pids) and time.monotonic() < limit:
            time.sleep(.02)
        assert not any(_alive(pid) for pid in pids)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
