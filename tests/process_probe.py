"""Portable process probes for POSIX lifecycle tests.

The tests run in sandboxed CI lanes where executing the system ``ps`` binary may
be denied. ``psutil`` reads the same facts without spawning a process; the
``ps`` path remains as a fallback for environments without psutil.
"""

from __future__ import annotations

import subprocess

try:  # pragma: no cover - the fallback is exercised only without psutil
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


def alive(pid: int) -> bool:
    """True only while ``pid`` exists and is not a zombie."""
    state = status(pid)
    return bool(state) and not state.startswith("Z")


def status(pid: int) -> str:
    """Return ``Z`` for a zombie, ``R`` for a live process, or ``""``."""
    if psutil is not None:
        try:
            process = psutil.Process(pid)
            return "Z" if process.status() == psutil.STATUS_ZOMBIE else "R"
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return ""
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "stat="],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def command_lines() -> list[tuple[int, str]]:
    """Return ``(pid, command line)`` for every visible process."""
    if psutil is not None:
        rows = []
        for process in psutil.process_iter(["pid", "cmdline"]):
            try:
                rows.append(
                    (process.info["pid"], " ".join(process.info["cmdline"] or []))
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return rows
    listing = subprocess.run(
        ["ps", "-ax", "-o", "pid=,command="],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    rows = []
    for raw in listing.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        pid_text, _, command = line.partition(" ")
        try:
            rows.append((int(pid_text), command))
        except ValueError:
            continue
    return rows
