"""Shared fixtures and helpers for the independent CC oracles.

The lane state, process reaping, git pin and profile-mutation helpers are used
by both independent CC test modules. They stay here so each test module remains
below the 800-line redline without copying fixture logic.
"""

import argparse
import atexit
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import uuid
from pathlib import Path

import process_probe

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
_LANE_STATE_ROOT = tempfile.TemporaryDirectory(prefix="mindie-cc-independent-cc-")
atexit.register(_LANE_STATE_ROOT.cleanup)
LANE_STATE = Path(_LANE_STATE_ROOT.name)


_ENV_KEYS = (
    "HOME",
    "XDG_CONFIG_HOME",
    "XDG_STATE_HOME",
    "XDG_DATA_HOME",
    "MINDIE_CC_CONFIG",
    "MINDIE_DIAGNOSTICS_CONFIG",
    "MINDIE_DIAGNOSTICS_ROOT",
    "CLAUDE_CONFIG_DIR",
    "PYTHONDONTWRITEBYTECODE",
)

_SERVICE_EXIT_TIMEOUT = 60.0


def _service_pids(root: Path) -> list[int]:
    needle = str(root)
    own = {os.getpid(), os.getppid()}
    if os.name == "nt":
        command = (
            "$ErrorActionPreference='Stop'; "
            "Get-CimInstance Win32_Process -Filter \"Name LIKE 'python%'\" | "
            "Where-Object { $_.CommandLine -and "
            "$_.CommandLine.Contains($env:MINDIE_TEST_SERVICE_ROOT) -and "
            "$_.CommandLine.Contains('mindie_knowledge.loop.cli') } | "
            "ForEach-Object { $_.ProcessId }"
        )
        listing = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=30, check=True,
            stdin=subprocess.DEVNULL,
            env=dict(os.environ, MINDIE_TEST_SERVICE_ROOT=needle),
        )
        return [int(line) for line in listing.stdout.splitlines()
                if line.strip().isdigit() and int(line) not in own]
    found = []
    for pid, command in process_probe.command_lines():
        if pid in own:
            continue
        if needle in command and "mindie_knowledge.loop.cli" in command:
            found.append(pid)
    return found


def _service_pids_until(root: Path, deadline: float) -> list[int]:
    """Return service PIDs, retrying a slow process query until ``deadline``."""
    while True:
        try:
            return _service_pids(root)
        except subprocess.TimeoutExpired:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.2)


def _wait_for_service_exit(root: Path, timeout: float = _SERVICE_EXIT_TIMEOUT) -> None:
    """Wait until no service process for ``root`` remains.

    A cold CIM/WMI query can take tens of seconds on a loaded Windows runner.
    Retry a timed-out query until the deadline; only fail if the service is
    still present (or unobservable) once that deadline has passed.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            pids = _service_pids_until(root, deadline)
        except subprocess.TimeoutExpired as exc:
            raise AssertionError(
                f"process query for {root} timed out before the service exited"
            ) from exc
        if not pids:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"service did not exit for {root}: {pids}")
        time.sleep(min(0.2, remaining))


def reap_services(root: Path) -> None:
    pids = _service_pids_until(root, time.monotonic() + _SERVICE_EXIT_TIMEOUT)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except TimeoutError:
            raise
        except OSError:
            pass
    if pids:
        _wait_for_service_exit(root)


# Explicit local checkout of the mindie-knowledge pin in uv.lock.
# There is no default path and no sibling search.
KNOWLEDGE_CHECKOUT_ENV = "MINDIE_TEST_KNOWLEDGE_CHECKOUT"


class IdentityPrecondition(RuntimeError):
    """The local-candidate install cannot start; nothing was downloaded."""


def _git_env() -> dict:
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git(repo: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_git_env(),
    )


def knowledge_checkout(commit: str) -> Path:
    """Absolute checkout whose HEAD is exactly ``commit``. Local git only."""
    raw = os.environ.get(KNOWLEDGE_CHECKOUT_ENV)
    if raw is None or not str(raw).strip():
        raise IdentityPrecondition(
            f"{KNOWLEDGE_CHECKOUT_ENV} is not set. Pass the absolute path of a "
            f"local git checkout whose HEAD is mindie-knowledge {commit}. "
            "This test does not fetch GitHub and does not search sibling checkouts."
        )
    text = str(raw).strip()
    path = Path(text)
    if not path.is_absolute():
        raise IdentityPrecondition(
            f"{KNOWLEDGE_CHECKOUT_ENV} must be an absolute path, not {text!r}."
        )
    if not path.is_dir():
        raise IdentityPrecondition(
            f"{KNOWLEDGE_CHECKOUT_ENV} is not a directory: {path}"
        )
    head = _git(path, "rev-parse", "--verify", "HEAD")
    if head.returncode != 0:
        detail = (head.stderr or head.stdout or "git rev-parse failed").strip()
        raise IdentityPrecondition(
            f"{path} is not a usable git checkout ({detail[:300]}). "
            "This test does not clone from the network."
        )
    kind = _git(path, "cat-file", "-t", "HEAD")
    if kind.returncode != 0 or kind.stdout.strip() != "commit":
        raise IdentityPrecondition(f"{path} HEAD is not a commit object.")
    current = head.stdout.strip()
    if current != commit:
        raise IdentityPrecondition(
            f"{path} HEAD is {current}, not the declared mindie-knowledge pin {commit}. "
            "Check out that commit locally. This test does not fetch it."
        )
    shallow = _git(path, "rev-parse", "--is-shallow-repository")
    if shallow.returncode != 0 or shallow.stdout.strip() != "false":
        raise IdentityPrecondition(
            "The local git+file install requires a complete checkout; use "
            "fetch-depth: 0 in CI. A filtered pip clone of a shallow source can "
            "recursively fetch missing objects. This test does not fetch history."
        )
    return path.resolve()


def _file_url(path: Path) -> str:
    text = path.resolve().as_posix()
    if not text.startswith("/"):
        text = "/" + text
    return "file://" + text


class LaneCase(unittest.TestCase):
    # Subclasses that can start a knowledge service must opt into exact-root cleanup.
    may_start_knowledge_service = False

    def setUp(self):
        LANE_STATE.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="case-", dir=LANE_STATE))
        self._saved_env = {key: os.environ.get(key) for key in _ENV_KEYS}
        os.environ["HOME"] = str(self.tmp / "home")
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "xdg")
        os.environ["XDG_STATE_HOME"] = str(self.tmp / "xdg-state")
        os.environ["XDG_DATA_HOME"] = str(self.tmp / "xdg-data")
        os.environ["MINDIE_DIAGNOSTICS_CONFIG"] = str(self.tmp / "diagnostics.json")
        os.environ["MINDIE_DIAGNOSTICS_ROOT"] = str(self.tmp / "diag-root")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.tmp / "claude-config")
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        os.environ["MINDIE_CC_CONFIG"] = str(self.tmp / "unset-cc.json")
        for key in (
            "HOME",
            "XDG_CONFIG_HOME",
            "XDG_STATE_HOME",
            "XDG_DATA_HOME",
            "MINDIE_DIAGNOSTICS_ROOT",
            "CLAUDE_CONFIG_DIR",
        ):
            Path(os.environ[key]).mkdir(parents=True, exist_ok=True)
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        tests = str(ROOT / "tests")
        if tests not in sys.path:
            sys.path.insert(0, tests)

    def tearDown(self):
        try:
            if self.may_start_knowledge_service:
                reap_services(self.tmp)
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)
            for key, value in self._saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def _env(self, config: Path) -> dict:
        env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        env["MINDIE_CC_CONFIG"] = str(config)
        return env

    def _use(self, config: Path) -> None:
        os.environ["MINDIE_CC_CONFIG"] = str(config)


def _store_path(case: Path) -> Path:
    from support import store_file
    return store_file(case)


def _capture_rows(case: Path):
    store = _store_path(case)
    if not store.is_file():
        return []
    db = sqlite3.connect(store)
    try:
        return db.execute(
            "SELECT summary, transcript, status FROM captures"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        db.close()


def _runtime_pins():
    from product_contract import requirements
    repositories = {"mindie-knowledge": "knowledge", "remote-dev": "remote-dev",
                    "mindie-diagnostics": "diagnostics"}
    return {name: {"url": "https://github.com/mindie-agent/" + repositories[name],
                   "commit": pin} for name, pin in requirements(ROOT)[0].items()}


def _hold_canonical_lock(lock_key, ready, release):
    from mindie_knowledge.loop.settings import CommunityWriteContext

    with CommunityWriteContext(lock_key):
        ready.set()
        if not release.wait(4):
            raise SystemExit("canonical lock holder was not released")


def _profile_mutation(config, home, scripts, kind, entered):
    os.environ["HOME"] = home
    os.environ["MINDIE_CC_CONFIG"] = config
    os.environ["XDG_CONFIG_HOME"] = str(Path(home) / "xdg")
    os.environ["XDG_STATE_HOME"] = str(Path(home) / "state")
    os.environ["MINDIE_DIAGNOSTICS_CONFIG"] = str(Path(home) / "diagnostics.json")
    os.environ["MINDIE_DIAGNOSTICS_ROOT"] = str(Path(home) / "diag")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.path.insert(0, scripts)
    try:
        if kind == "disable":
            import sharing

            entered.set()
            sharing.write_disabled()
        else:
            raise SystemExit("unknown mutation")
    except Exception:
        import traceback

        Path(config).with_suffix(".worker-error").write_text(traceback.format_exc(), encoding="utf-8")
        raise
