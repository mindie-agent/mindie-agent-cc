"""Native handle-backed evidence of selective service transfer and tree cleanup."""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def kernel():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetCurrentProcess.restype = wintypes.HANDLE
    api.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    return api


def job_state(api):
    value = wintypes.BOOL()
    if not api.IsProcessInJob(api.GetCurrentProcess(), None, ctypes.byref(value)):
        raise ctypes.WinError(ctypes.get_last_error())
    return bool(value.value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--scripts", type=Path, required=True)
    parser.add_argument("--mode", default="driver")
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    api = kernel()
    if args.mode == "service":
        (root / "started.json").write_text(json.dumps(dict(pid=os.getpid(), in_job=job_state(api))))
        deadline = time.monotonic() + 12
        while not (root / "release").exists() and time.monotonic() < deadline:
            time.sleep(.02)
        (root / "completed.json").write_text(json.dumps(dict(released=(root / "release").exists())))
        return
    if args.mode in ("explicit", "ordinary"):
        command = [sys.executable, str(Path(__file__).resolve()), "--mode", "service",
                   "--root", str(root), "--scripts", str(args.scripts)]
        if args.mode == "explicit":
            from mindie_knowledge.loop.process import spawn_service
            try:
                process = spawn_service(command)
            except TimeoutError:
                raise
            except OSError as exc:
                result = dict(spawned=False, error=type(exc).__name__,
                              winerror=getattr(exc, "winerror", None))
                (root / "spawn-error.json").write_text(json.dumps(result))
                print(json.dumps(result), flush=True)
                return
        else:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 5
        while not (root / "started.json").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        print(json.dumps(dict(pid=os.getpid(), in_job=job_state(api), popen_pid=process.pid,
                              started=(root / "started.json").exists())), flush=True)
        return
    sys.path.insert(0, str(args.scripts))
    import bounded
    original = bounded._kill_tree
    result = dict(source=str(bounded.__file__), cases={})
    for label, allow, mode in (("default-explicit", False, "explicit"),
                               ("service-explicit", True, "explicit"),
                               ("service-ordinary", True, "ordinary")):
        case_root = root / label
        evidence = dict(allow_service=allow, mode=mode, sequence=[])
        retained = []

        def cleanup(process, pgid=None):
            rejected = case_root / "spawn-error.json"
            if rejected.exists():
                evidence["spawn_error"] = json.loads(rejected.read_text())
                original(process, pgid)
                return
            started = json.loads((case_root / "started.json").read_text())
            handle = api.OpenProcess(0x100000 | 0x1000, False, started["pid"])
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            retained.append(handle)
            evidence.update(service=started, bounded_popen_pid=process.pid)
            evidence["sequence"].append(dict(stage="before_owned_cleanup",
                                             wait=api.WaitForSingleObject(handle, 0)))
            original(process, pgid)
            evidence["sequence"].append(dict(stage="after_owned_cleanup",
                                             wait=api.WaitForSingleObject(handle, 500)))

        bounded._kill_tree = cleanup
        try:
            command = [sys.executable, str(Path(__file__).resolve()), "--mode", mode,
                       "--root", str(case_root), "--scripts", str(args.scripts)]
            output = bounded.run(command, timeout=8, allow_service=allow)
            evidence["child"] = json.loads(output)
            (case_root / "release").touch()
            for handle in retained:
                wait = api.WaitForSingleObject(handle, 5000)
                code = wintypes.DWORD()
                if not api.GetExitCodeProcess(handle, ctypes.byref(code)):
                    raise ctypes.WinError(ctypes.get_last_error())
                evidence["sequence"].append(dict(stage="after_fixture_release", wait=wait,
                                                 exit_code=code.value))
            complete = case_root / "completed.json"
            evidence["completed"] = json.loads(complete.read_text()) if complete.exists() else None
        finally:
            bounded._kill_tree = original
            (case_root / "release").touch()
            for handle in retained:
                api.CloseHandle(handle)
        result["cases"][label] = evidence
    with (root / "result.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
