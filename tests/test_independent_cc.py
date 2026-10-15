"""Independent CC oracles for the 2026-09-27 contracts.

These tests do not copy production branch structure. A handwritten adapter
config is only a localization fixture for Stop and entry behavior. It is not
an install-path pass; install identity is the setup subprocess below.

Profiles, diagnostics, and HOME for these tests stay under this lane's state
directory. They do not start the Claude host.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
import uuid
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
LANE_STATE = Path(__file__).resolve().parents[2] / "state" / "component-tests"


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
            capture_output=True, text=True, timeout=15, check=True,
            env=dict(os.environ, MINDIE_TEST_SERVICE_ROOT=needle),
        )
        return [int(line) for line in listing.stdout.splitlines()
                if line.strip().isdigit() and int(line) not in own]
    found = []
    listing = subprocess.run(
        ["ps", "-ax", "-o", "pid=,command="],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    for raw in listing.stdout.splitlines():
        line = raw.strip()
        if needle not in line or "mindie_knowledge.loop.cli" not in line:
            continue
        pid_text, _, _command = line.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid not in own:
            found.append(pid)
    return found


def reap_services(root: Path) -> None:
    for pid in _service_pids(root):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


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


class SetupIdentityTests(LaneCase):
    def test_production_runtime_obeys_official_lock_pins(self):
        from candidate_validate import installed_revisions
        from product_contract import requirements
        installed_revisions(requirements(ROOT)[0])

    def _read_direct(self, python, dist_name):
        code = (
            "import json,sys\n"
            "from importlib.metadata import distribution\n"
            "raw = distribution(sys.argv[1]).read_text('direct_url.json') or ''\n"
            "data = json.loads(raw) if raw else {}\n"
            "vcs = data.get('vcs_info') or {}\n"
            "print(json.dumps({'raw': raw, 'url': data.get('url') or '',"
            " 'vcs': vcs.get('vcs'), 'commit': vcs.get('commit_id')}))\n"
        )
        proc = subprocess.run(
            [str(python), "-c", code, dist_name],
            capture_output=True,
            text=True,
            timeout=30,
            env=self._env(self.tmp / "unset-cc.json"),
        )
        if proc.returncode != 0:
            return proc, None
        return proc, json.loads(proc.stdout)

    def _direct_url(self, python, dist_name):
        proc, info = self._read_direct(python, dist_name)
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        return info

    def _require_reused_runtime(self, pins):
        """The interpreter under test already has the official pins.

        The local-candidate venv reuses those installed distributions. It
        does not reinstall them and does not download a build backend.
        """
        python = sys.executable
        for name, pin in pins.items():
            proc, info = self._read_direct(python, name)
            if proc.returncode != 0 or info is None:
                lines = (proc.stderr or proc.stdout or "").strip().splitlines()
                detail = lines[-1] if lines else "no output"
                raise IdentityPrecondition(
                    f"{python} cannot read {name} metadata ({detail[:300]}). "
                    "Install uv.lock in this interpreter. "
                    "This test does not download runtime dependencies."
                )
            url = str(info.get("url") or "").rstrip("/")
            official = url in {pin["url"], pin["url"] + ".git"}
            if info.get("vcs") != "git" or info.get("commit") != pin["commit"] or not official:
                raise IdentityPrecondition(
                    f"{python} {name} is {info.get('commit')} at {str(info.get('url'))[:160]}, "
                    f"not {pin['url']}@{pin['commit']}. "
                    "Install the declared runtime pins in this interpreter. "
                    "This test does not reinstall them."
                )
        hatch = subprocess.run(
            [python, "-c", "import hatchling"],
            capture_output=True,
            text=True,
            timeout=30,
            env=self._env(self.tmp / "unset-cc.json"),
        )
        if hatch.returncode != 0:
            raise IdentityPrecondition(
                f"hatchling is not importable in {python}. The pinned knowledge "
                "tree builds with hatchling. Install hatchling in this interpreter "
                "before the local-candidate test. This test does not download a "
                "build backend."
            )

    def _link_official_runtime(self, venv_python):
        """Make the isolated venv import the interpreter's already-installed deps.

        A venv created from this interpreter does not see that interpreter's
        site-packages. Process its .pth files as well as adding the directory:
        Windows pywin32 needs its bootstrap and extension-module paths.
        """
        code = (
            "import sysconfig\n"
            "print(sysconfig.get_path('purelib'))\n"
            "print(sysconfig.get_path('platlib'))\n"
        )
        parent = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=30,
            env=self._env(self.tmp / "unset-cc.json"),
        )
        if parent.returncode != 0:
            raise IdentityPrecondition(
                "cannot resolve the official interpreter site-packages: "
                + (parent.stderr or "")[-300:]
            )
        child = subprocess.run(
            [str(venv_python), "-c", code],
            capture_output=True,
            text=True,
            timeout=30,
            env=_git_env(),
        )
        if child.returncode != 0:
            raise IdentityPrecondition(
                "cannot resolve the isolated venv site-packages: "
                + (child.stderr or "")[-300:]
            )
        sources = []
        for line in parent.stdout.splitlines():
            candidate = Path(line.strip())
            if line.strip() and candidate not in sources:
                if not candidate.is_dir():
                    raise IdentityPrecondition(
                        f"official site-packages is not a directory: {candidate}"
                    )
                sources.append(candidate)
        destination = Path(child.stdout.splitlines()[0].strip())
        destination.mkdir(parents=True, exist_ok=True)
        payload = "import site; " + "; ".join(
            f"site.addsitedir({str(path)!r})" for path in sources
        ) + "\n"
        (destination / "official-runtime.pth").write_text(payload, encoding="utf-8")

    def _genuine_local_git_python(self, commit, remote_commit):
        """Real git+file install of an explicitly supplied local checkout.

        Runtime dependencies stay the official installs of this interpreter.
        pip writes the candidate's direct_url.json; this test does not.
        """
        import setup

        checkout = knowledge_checkout(commit)
        self._require_reused_runtime(_runtime_pins())
        parent_knowledge = self._direct_url(sys.executable, "mindie-knowledge")["raw"]
        parent_remote = self._direct_url(sys.executable, "remote-dev")["raw"]
        work = self.tmp / "local-git-runtime"
        repo = work / "knowledge"
        work.mkdir(parents=True)
        cloned = subprocess.run(
            ["git", "clone", "--local", "--no-hardlinks", str(checkout), str(repo)],
            capture_output=True,
            text=True,
            timeout=60,
            env=_git_env(),
        )
        if cloned.returncode != 0:
            raise IdentityPrecondition(
                "local clone of the supplied knowledge checkout failed: "
                + (cloned.stderr or cloned.stdout)[-500:]
            )
        _git(repo, "remote", "remove", "origin")
        checked = _git(repo, "checkout", "--detach", commit, timeout=60)
        if checked.returncode != 0:
            raise IdentityPrecondition(
                "the local clone does not contain the declared commit: "
                + (checked.stderr or checked.stdout)[-400:]
            )
        head = _git(repo, "rev-parse", "HEAD")
        if head.returncode != 0 or head.stdout.strip() != commit:
            raise IdentityPrecondition(
                f"local clone HEAD is {head.stdout.strip()!r}, not {commit}."
            )
        venv = work / "venv"
        created = subprocess.run(
            [sys.executable, "-m", "venv", str(venv)],
            capture_output=True,
            text=True,
            timeout=60,
            env=_git_env(),
        )
        if created.returncode != 0:
            raise IdentityPrecondition(
                "could not create the isolated candidate venv: "
                + (created.stderr or created.stdout)[-400:]
            )
        scripts = venv / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        pip = scripts / ("pip.exe" if os.name == "nt" else "pip")
        self._link_official_runtime(python)
        visible = subprocess.run(
            [str(python), "-c", "import hatchling, yaml, remote_dev, mindie_diagnostics"],
            capture_output=True,
            text=True,
            timeout=30,
            env=_git_env(),
        )
        if visible.returncode != 0:
            raise IdentityPrecondition(
                "the isolated venv cannot see hatchling and the official runtime "
                f"installed in {sys.executable}: "
                + (visible.stderr or visible.stdout)[-500:]
                + " Refusing to download dependencies or a build backend."
            )
        spec = f"git+{_file_url(repo)}@{commit}"
        from bounded import run

        # Own the complete pip/Git tree, including descendants that outlive pip.
        run(
            [
                str(pip), "install", "--disable-pip-version-check", "--no-cache-dir",
                "--no-index", "--no-deps", "--no-build-isolation", "--force-reinstall", spec,
            ],
            timeout=120,
            env=_git_env(),
        )
        knowledge = self._direct_url(python, "mindie-knowledge")
        self.assertEqual(knowledge["vcs"], "git", knowledge)
        self.assertTrue(str(knowledge["url"]).startswith("file://"), knowledge["url"][:160])
        self.assertEqual(knowledge["commit"], commit)
        remote = self._direct_url(python, "remote-dev")
        self.assertEqual(remote["vcs"], "git", remote)
        self.assertEqual(remote["commit"], remote_commit)
        self.assertTrue(
            str(remote["url"]).startswith("https://github.com/mindie-agent/remote-dev"),
            remote["url"][:160],
        )
        self.assertEqual(
            self._direct_url(sys.executable, "mindie-knowledge")["raw"],
            parent_knowledge,
            "candidate install rewrote the official interpreter direct_url.json",
        )
        self.assertEqual(
            self._direct_url(sys.executable, "remote-dev")["raw"],
            parent_remote,
        )
        return python, knowledge["raw"]

    def test_knowledge_checkout_preconditions_fail_before_any_install(self):
        """Missing or inexact checkouts stop before git clone or pip."""
        import setup

        commit = _runtime_pins()["mindie-knowledge"]["commit"]
        saved = os.environ.pop(KNOWLEDGE_CHECKOUT_ENV, None)
        try:
            with self.assertRaises(IdentityPrecondition) as missing:
                knowledge_checkout(commit)
            self.assertIn(KNOWLEDGE_CHECKOUT_ENV, str(missing.exception))
            self.assertIn("does not fetch", str(missing.exception))
            self.assertIn("does not search sibling", str(missing.exception))

            os.environ[KNOWLEDGE_CHECKOUT_ENV] = "relative/knowledge"
            with self.assertRaises(IdentityPrecondition) as relative:
                knowledge_checkout(commit)
            self.assertIn("absolute", str(relative.exception))

            absent = self.tmp / "no-such-checkout"
            os.environ[KNOWLEDGE_CHECKOUT_ENV] = str(absent)
            with self.assertRaises(IdentityPrecondition) as missing_dir:
                knowledge_checkout(commit)
            self.assertIn("not a directory", str(missing_dir.exception))

            plain = self.tmp / "not-a-repo"
            plain.mkdir()
            os.environ[KNOWLEDGE_CHECKOUT_ENV] = str(plain)
            with self.assertRaises(IdentityPrecondition) as unusable:
                knowledge_checkout(commit)
            self.assertIn("not a usable git checkout", str(unusable.exception))
            self.assertIn("does not clone from the network", str(unusable.exception))

            other = self.tmp / "other-commit"
            other.mkdir()
            author = _git_env()
            author.update(
                GIT_AUTHOR_NAME="mindie-test",
                GIT_AUTHOR_EMAIL="mindie-test@example.com",
                GIT_COMMITTER_NAME="mindie-test",
                GIT_COMMITTER_EMAIL="mindie-test@example.com",
            )
            for args in (
                ["init"],
                ["commit", "--allow-empty", "-m", "not the declared pin"],
            ):
                proc = subprocess.run(
                    ["git", "-C", str(other), *args],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env=author,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
            os.environ[KNOWLEDGE_CHECKOUT_ENV] = str(other)
            with self.assertRaises(IdentityPrecondition) as wrong:
                knowledge_checkout(commit)
            self.assertIn(commit, str(wrong.exception))
            self.assertIn("does not fetch", str(wrong.exception))
            self.assertNotIn("github.com", str(wrong.exception))
            shallow = self.tmp / "shallow"
            cloned = subprocess.run(
                ["git", "clone", "--depth=1", _file_url(other), str(shallow)],
                capture_output=True, text=True, timeout=30, env=_git_env(),
            )
            self.assertEqual(cloned.returncode, 0, cloned.stderr)
            os.environ[KNOWLEDGE_CHECKOUT_ENV] = str(shallow)
            with self.assertRaisesRegex(IdentityPrecondition, "complete checkout"):
                knowledge_checkout(_git(other, "rev-parse", "HEAD").stdout.strip())
        finally:
            if saved is None:
                os.environ.pop(KNOWLEDGE_CHECKOUT_ENV, None)
            else:
                os.environ[KNOWLEDGE_CHECKOUT_ENV] = saved

    def test_local_git_source_is_rejected_even_when_commit_matches(self):
        pins = _runtime_pins()
        commit = pins["mindie-knowledge"]["commit"]
        try:
            python, before = self._genuine_local_git_python(commit, pins["remote-dev"]["commit"])
        except IdentityPrecondition as exc:
            raise self.failureException(str(exc)) from None
        code = ("import sys;sys.path.insert(0,sys.argv[1]);"
                "from candidate_validate import installed_revisions;"
                "from product_contract import requirements;"
                "installed_revisions(requirements(sys.argv[2])[0])")
        result = subprocess.run([str(python), "-c", code, str(SCRIPTS), str(ROOT)],
                                capture_output=True, text=True, env=self._env(self.tmp / "unset.json"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("revision_mismatch", result.stderr)
        self.assertEqual(self._direct_url(python, "mindie-knowledge")["raw"], before)


class ConsentFileTests(LaneCase):
    def _config(self, case: Path):
        import support

        case.mkdir(parents=True, exist_ok=True)
        config = support.make_config(case, sharing=False)
        self._use(config)
        return config

    def test_install_writer_records_consent_authority(self):
        import setup

        community = self.tmp / "mindie-community.json"
        setup.write_community(community, None)
        written = json.loads(community.read_text(encoding="utf-8"))
        expected = str((community.parent / "mindie-consent.json").resolve())
        self.assertEqual(written.get("consent_config"), expected)
        self.assertIs(written.get("enabled"), False)

        project = self.tmp / "project"
        project.mkdir()
        parser = argparse.ArgumentParser()
        payload = setup.community_settings(
            argparse.Namespace(
                community_repository="owner/repo",
                community_project_root=[str(project)],
                community_account=None,
                community_fork=None,
                community_visibility="public",
                community_branch=None,
            ),
            parser,
        )
        enabled = self.tmp / "enabled-community.json"
        setup.write_community(enabled, payload)
        enabled_data = json.loads(enabled.read_text(encoding="utf-8"))
        self.assertEqual(
            Path(enabled_data.get("consent_config", "")).resolve(),
            (enabled.parent / "mindie-consent.json").resolve(),
        )

    def test_status_does_not_migrate_or_rewrite(self):
        import entry

        case = self.tmp / "status"
        config = self._config(case)
        engine = Path(json.loads(config.read_text(encoding="utf-8"))["engine_config"])
        community = Path(json.loads(config.read_text(encoding="utf-8"))["community_config"])
        marker = case / "state" / "cc.first-use.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"choice": "read-only"}) + "\n", encoding="utf-8")
        before = {
            "adapter": config.read_bytes(),
            "engine": engine.read_bytes(),
            "community": community.read_bytes(),
            "marker": marker.read_bytes(),
        }
        payload = entry.status_payload()
        with self.subTest("does not re-ask"):
            self.assertEqual(payload.get("choices"), [])
        with self.subTest("does not create consent"):
            self.assertFalse((config.parent / "mindie-consent.json").exists())
        with self.subTest("does not copy community"):
            self.assertFalse((config.parent / "mindie-community.json").exists())
        with self.subTest("adapter bytes"):
            self.assertEqual(config.read_bytes(), before["adapter"])
        with self.subTest("engine bytes"):
            self.assertEqual(engine.read_bytes(), before["engine"])
        with self.subTest("community bytes"):
            self.assertEqual(community.read_bytes(), before["community"])
        with self.subTest("marker bytes"):
            self.assertEqual(marker.read_bytes(), before["marker"])

    def test_field_update_does_not_clear_a_damaged_consent_file(self):
        import consent

        case = self.tmp / "damaged"
        config = self._config(case)
        path = config.parent / "mindie-consent.json"
        original = b"{not-json-consent\n"
        path.write_bytes(original)
        rejected = []
        for call in (
            lambda: consent.record_choice("later"),
            lambda: consent.record_reporting("enabled"),
        ):
            try:
                call()
            except Exception as exc:
                rejected.append(type(exc).__name__)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(len(rejected), 2, rejected)
        self.assertNotEqual(consent.load()["state"], "ok")

    def test_unreadable_consent_is_not_replaced(self):
        import consent

        case = self.tmp / "unreadable"
        config = self._config(case)
        path = config.parent / "mindie-consent.json"
        original = b'{"schema":"mindie-consent/1","choice":"later","reporting":"disabled"}\n'
        path.write_bytes(original)
        path.chmod(0)
        try:
            try:
                path.read_bytes()
            except OSError:
                blocked = True
            else:
                blocked = False
            if not blocked:
                self.skipTest("mode 000 is still readable by the owner on this OS")
            rejected = False
            try:
                consent.record_choice("read-only")
            except Exception:
                rejected = True
            path.chmod(0o600)
            self.assertEqual(path.read_bytes(), original)
            self.assertTrue(rejected)
        finally:
            path.chmod(0o600)

    def test_choice_and_reporting_do_not_clobber_each_other(self):
        import consent

        case = self.tmp / "fields"
        config = self._config(case)
        self.assertEqual(consent.record_choice("read-only"), "read-only")
        self.assertEqual(consent.record_reporting("later"), "later")
        saved = consent.load()
        self.assertEqual(saved["choice"], "read-only")
        self.assertEqual(saved["reporting"], "later")
        self.assertEqual(consent.record_choice("disabled"), "disabled")
        saved = consent.load()
        self.assertEqual(saved["choice"], "disabled")
        self.assertEqual(saved["reporting"], "later")

    def test_cross_process_updates_keep_both_fields(self):
        import consent
        import multiprocessing

        case = self.tmp / "race"
        config = self._config(case)
        path = consent.consent_path()
        path.write_text(
            json.dumps(
                {
                    "schema": "mindie-consent/1",
                    "choice": "read-only",
                    "reporting": "later",
                }
            )
            + "\n"
        , encoding="utf-8")
        ctx = multiprocessing.get_context("spawn")
        barrier = ctx.Barrier(2)
        rounds = 20
        home = os.environ["HOME"]
        proc_choice = ctx.Process(
            target=_race_worker,
            args=(str(config), home, str(SCRIPTS), "choice", "disabled", rounds, barrier),
        )
        proc_report = ctx.Process(
            target=_race_worker,
            args=(str(config), home, str(SCRIPTS), "reporting", "enabled", rounds, barrier),
        )
        proc_choice.start()
        proc_report.start()
        proc_choice.join(30)
        proc_report.join(30)
        self.assertFalse(proc_choice.is_alive())
        self.assertFalse(proc_report.is_alive())
        details = []
        for label, proc in (("choice", proc_choice), ("reporting", proc_report)):
            if proc.exitcode != 0:
                details.append(f"{label} exit {proc.exitcode}")
        self.assertEqual(
            details,
            [],
            "cross-process consent update failed: " + "; ".join(details),
        )
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved.get("choice"), "disabled")
        self.assertEqual(saved.get("reporting"), "enabled")


def _race_worker(config, home, scripts, kind, value, rounds, barrier):
    os.environ["HOME"] = home
    os.environ["MINDIE_CC_CONFIG"] = config
    os.environ["XDG_CONFIG_HOME"] = str(Path(home) / "xdg")
    os.environ["XDG_STATE_HOME"] = str(Path(home) / "state")
    os.environ["MINDIE_DIAGNOSTICS_CONFIG"] = str(Path(home) / "diagnostics.json")
    os.environ["MINDIE_DIAGNOSTICS_ROOT"] = str(Path(home) / "diag")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.path.insert(0, scripts)
    import consent

    for _ in range(rounds):
        barrier.wait(20)
        if kind == "choice":
            consent.record_choice(value)
        else:
            consent.record_reporting(value)


class StopGateTests(LaneCase):
    def _arm(self, case: Path, *, sharing: bool, choice=None, corrupt=False, authority=None):
        import support
        from mindie_knowledge.loop.handoff import prepare_schema
        from paths import engine_config_path

        case.mkdir(parents=True, exist_ok=True)
        project = case / "project"
        outside = case / "outside"
        project.mkdir(exist_ok=True)
        outside.mkdir(exist_ok=True)
        config = support.make_config(case, sharing=sharing, roots=[str(project)])
        self._use(config)
        consent_path = config.parent / "mindie-consent.json"
        if corrupt:
            consent_path.write_bytes(b"{not-json-consent\n")
        elif choice is not None:
            consent_path.write_text(
                json.dumps(
                    {
                        "schema": "mindie-consent/1",
                        "choice": choice,
                        "reporting": "later",
                    }
                )
                + "\n"
            , encoding="utf-8")
        if authority is not None:
            target = case / "authority" / "mindie-consent.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(authority) + "\n", encoding="utf-8")
            community = Path(json.loads(config.read_text(encoding="utf-8"))["community_config"])
            data = json.loads(community.read_text(encoding="utf-8"))
            data["consent_config"] = str(target.resolve())
            community.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        prepare_schema(str(engine_config_path()))
        return config, project, outside

    def _stop(self, case: Path, project: Path, *, session=None, prompt=None,
              sentinel="SENTINEL", hook_cwd=None):
        from datetime import datetime, timezone

        session = session or str(uuid.uuid4())
        prompt = prompt or str(uuid.uuid4())
        # Ordinary Stop must associate an authorized native task without an
        # entry invocation. Only the named profile artifact supplies scope.
        transcript = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "fixture" / f"{session}.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        if not transcript.exists():
            transcript.write_text(json.dumps(dict(
                type="assistant", sessionId=session, cwd=str(project),
                timestamp=datetime.now(timezone.utc).isoformat(),
                message=dict(role="assistant", content=sentinel),
            )) + "\n", encoding="utf-8")
        payload = {
            "hook_event_name": "Stop",
            "session_id": session,
            "prompt_id": prompt,
            "cwd": str(hook_cwd or project),
            "transcript_path": str(transcript),
            "last_assistant_message": sentinel,
            "stop_hook_active": False,
        }
        config = Path(os.environ["MINDIE_CC_CONFIG"])
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "mindie_launch.py"), "hook", "stop"],
            input=json.dumps(payload).encode(),
            capture_output=True,
            env=self._env(config),
            timeout=8,
        )
        return proc, _capture_rows(case), _store_path(case).with_name("wake.json").exists()


    def test_authorized_contribute_stop_is_received_once(self):
        case = self.tmp / "allow"
        _config, project, _outside = self._arm(case, sharing=True, choice="contribute")
        sentinel = "SENTINEL-contribute-once"
        try:
            session, prompt = str(uuid.uuid4()), str(uuid.uuid4())
            first, rows, _wake = self._stop(
                case, project, session=session, prompt=prompt, sentinel=sentinel
            )
            self.assertEqual(first.returncode, 0, first.stderr.decode()[:500])
            self.assertEqual(json.loads(first.stdout.decode() or "{}"), {})
            self.assertEqual([row[0] for row in rows], [""])  # notification carries no duplicate body
            second, rows, _wake = self._stop(
                case, project, session=session, prompt=prompt, sentinel=sentinel
            )
            self.assertEqual(second.returncode, 0, second.stderr.decode()[:500])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][2], "queued")
        finally:
            reap_services(case)

    def test_disabled_community_blocks_contribute_consent(self):
        case = self.tmp / "off"
        _config, project, _outside = self._arm(case, sharing=False, choice="contribute")
        try:
            proc, rows, wake = self._stop(case, project, sentinel="SENTINEL-disabled-community")
            self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:500])
            self.assertEqual(rows, [])
            self.assertFalse(wake)
            self.assertEqual(_service_pids(case), [])
        finally:
            reap_services(case)

    def test_out_of_scope_stop_does_not_persist(self):
        case = self.tmp / "scope"
        _config, _project, outside = self._arm(case, sharing=True, choice="contribute")
        try:
            proc, rows, wake = self._stop(case, outside, sentinel="SENTINEL-out-of-scope")
            self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:500])
            self.assertEqual(rows, [])
            self.assertFalse(wake)
        finally:
            reap_services(case)

    def test_hook_cwd_cannot_override_native_artifact_scope(self):
        case = self.tmp / "artifact-scope"
        _config, project, outside = self._arm(case, sharing=True, choice="contribute")
        proc, rows, wake = self._stop(case, outside, hook_cwd=project)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:500])
        self.assertEqual(rows, [])
        self.assertFalse(wake)

    def test_ordinary_stop_preserves_explicit_deactivation(self):
        import admission

        case = self.tmp / "revoked-task"
        _config, project, _outside = self._arm(case, sharing=True, choice="contribute")
        session = str(uuid.uuid4())
        admission.activate(session, project_root=str(project))
        admission.deactivate(session)
        proc, rows, wake = self._stop(case, project, session=session)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:500])
        self.assertEqual(rows, [])
        self.assertFalse(wake)
        self.assertEqual(admission.gate().inspect(session)["status"], "inactive")

    def test_disallowed_or_damaged_consent_does_not_persist_stop(self):
        """The declared consent authority must refuse disallowed capture."""
        scenarios = [
            ("later", dict(choice="later")),
            ("read-only", dict(choice="read-only")),
            ("disabled", dict(choice="disabled")),
            ("corrupt", dict(corrupt=True)),
        ]
        for name, kwargs in scenarios:
            with self.subTest(name=name):
                case = self.tmp / name
                _config, project, _outside = self._arm(case, sharing=True, **kwargs)
                sentinel = "SENTINEL-" + name
                try:
                    proc, rows, wake = self._stop(case, project, sentinel=sentinel)
                    self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:500])
                    self.assertEqual(json.loads(proc.stdout.decode() or "{}"), {})
                    self.assertEqual(rows, [], rows)
                    self.assertFalse(wake)
                    self.assertEqual(_service_pids(case), [])
                finally:
                    reap_services(case)


    def test_field_target_wins_over_sibling_contribute(self):
        case = self.tmp / "field-target"
        config, project, _outside = self._arm(case, sharing=True, choice="contribute")
        community = Path(json.loads(config.read_text(encoding="utf-8"))["community_config"])
        authority = case / "disabled-authority.json"
        authority.write_text(
            json.dumps(
                {
                    "schema": "mindie-consent/1",
                    "choice": "disabled",
                    "reporting": "later",
                }
            )
            + "\n"
        , encoding="utf-8")
        data = json.loads(community.read_text(encoding="utf-8"))
        data["consent_config"] = str(authority.resolve())
        community.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        pointer = json.loads(config.read_text(encoding="utf-8"))["community_config"]
        try:
            proc, rows, wake = self._stop(case, project, sentinel="SENTINEL-field-target")
            self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:400])
            self.assertEqual(rows, [])
            self.assertFalse(wake)
        finally:
            reap_services(case)
        self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["community_config"], pointer)

    def test_corrupt_shared_authority_does_not_fall_back(self):
        case = self.tmp / "corrupt-shared"
        config, project, _outside = self._arm(case, sharing=True, choice="contribute")
        legacy = Path(json.loads(config.read_text(encoding="utf-8"))["community_config"])
        legacy_bytes = legacy.read_bytes()
        shared = config.parent / "mindie-community.json"
        shared.write_bytes(b"{not-a-community-document\n")
        engine = Path(json.loads(config.read_text(encoding="utf-8"))["engine_config"])
        for path in (config, engine):
            document = json.loads(path.read_text(encoding="utf-8"))
            document["community_config"] = str(shared.resolve())
            path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        try:
            proc, rows, wake = self._stop(case, project, sentinel="SENTINEL-corrupt-shared")
            self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:400])
            self.assertEqual(rows, [])
            self.assertFalse(wake)
        finally:
            reap_services(case)
        self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["community_config"], str(shared.resolve()))
        self.assertEqual(legacy.read_bytes(), legacy_bytes)
        self.assertEqual(shared.read_bytes(), b"{not-a-community-document\n")

    def test_stop_does_not_rewrite_install_files(self):
        case = self.tmp / "rewrite"
        config, project, _outside = self._arm(case, sharing=True, choice="contribute")
        engine = Path(json.loads(config.read_text(encoding="utf-8"))["engine_config"])
        community = Path(json.loads(config.read_text(encoding="utf-8"))["community_config"])
        before = (config.read_bytes(), engine.read_bytes(), community.read_bytes())
        try:
            proc, _rows, _wake = self._stop(case, project, sentinel="SENTINEL-rewrite")
            self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:400])
        finally:
            reap_services(case)
        self.assertEqual(config.read_bytes(), before[0])
        self.assertEqual(engine.read_bytes(), before[1])
        self.assertEqual(community.read_bytes(), before[2])
        self.assertFalse((config.parent / "mindie-community.json").exists())


class EntryContractTests(LaneCase):
    def _config(self, sharing=False):
        import support

        case = self.tmp / "entry"
        case.mkdir(parents=True, exist_ok=True)
        project = case / "project"
        project.mkdir(exist_ok=True)
        config = support.make_config(
            case, sharing=sharing, roots=[str(project)] if sharing else None
        )
        self._use(config)
        return config, project

    def _event(self, command, args="", *, session, prompt, cwd, **extra):
        event = {
            "hook_event_name": "UserPromptExpansion",
            "expansion_type": "slash_command",
            "command_name": command,
            "command_source": "plugin",
            "command_args": args,
            "session_id": session,
            "prompt_id": prompt,
            "cwd": str(cwd),
            "transcript_path": str(Path(cwd) / "transcript.jsonl"),
        }
        event.update(extra)
        return event

    def test_saved_choice_is_reused_for_a_new_task_without_another_prompt(self):
        import entry

        _config, project = self._config(False)
        first_session = str(uuid.uuid4())
        payload = entry.dispatch_event(
            self._event(
                "mindie-agent",
                "disabled",
                session=first_session,
                prompt=str(uuid.uuid4()),
                cwd=project,
            )
        )
        self.assertEqual(payload.get("first_use"), "disabled")
        self.assertEqual(payload.get("choices"), [])
        second = entry.dispatch_event(
            self._event(
                "mindie-agent",
                "",
                session=str(uuid.uuid4()),
                prompt=str(uuid.uuid4()),
                cwd=project,
            )
        )
        self.assertEqual(second.get("first_use"), "disabled")
        self.assertEqual(second.get("choices"), [])
        self.assertNotIn("sharing-enable", json.dumps(second))
        outside = self.tmp / "entry" / "outside"
        outside.mkdir()
        third = entry.dispatch_event(
            self._event(
                "mindie-agent:mindie-agent",
                "",
                session=str(uuid.uuid4()),
                prompt=str(uuid.uuid4()),
                cwd=outside,
            )
        )
        self.assertEqual(third.get("choices"), [])
        self.assertEqual(third.get("first_use"), "disabled")


    def test_subagent_does_not_inherit_the_parent_binding(self):
        import admission
        import entry

        _config, project = self._config(False)
        parent = str(uuid.uuid4())
        entry.dispatch_event(
            self._event(
                "mindie-agent",
                "disabled",
                session=parent,
                prompt=str(uuid.uuid4()),
                cwd=project,
            )
        )
        child = str(uuid.uuid4())
        with self.assertRaises(ValueError):
            entry.dispatch_event(
                self._event(
                    "mindie-agent",
                    "",
                    session=child,
                    prompt=str(uuid.uuid4()),
                    cwd=project,
                    agent_id="fork-1",
                )
            )
        self.assertIsNotNone(admission.gate().active_lease(parent))
        self.assertIsNone(admission.gate().active_lease(child))

    def test_ordinary_mention_does_not_choose_or_bind(self):
        config, project = self._config(False)
        transcript = project / "transcript.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")
        base = {
            "hook_event_name": "UserPromptExpansion",
            "command_name": "mindie-agent",
            "command_args": "read-only",
            "session_id": str(uuid.uuid4()),
            "prompt_id": str(uuid.uuid4()),
            "cwd": str(project),
            "transcript_path": str(transcript),
        }
        for extra in (
            {"expansion_type": "slash_command", "command_source": "user"},
            {"expansion_type": "prompt", "command_source": "plugin"},
        ):
            with self.subTest(extra=extra):
                proc = subprocess.run(
                    [sys.executable, str(SCRIPTS / "bridge.py"), "expansion"],
                    input=json.dumps({**base, **extra}).encode(),
                    capture_output=True,
                    env=self._env(config),
                    timeout=8,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr.decode()[:400])
                self.assertFalse((config.parent / "mindie-consent.json").exists())
                adapter = json.loads(config.read_text(encoding="utf-8"))
                engine = json.loads(Path(adapter["engine_config"]).read_text(encoding="utf-8"))
                self.assertFalse(Path(engine["admission_path"]).exists())

    def test_first_entry_offers_reporting_with_the_knowledge_choice(self):
        import entry

        _config, project = self._config(False)
        payload = entry.dispatch_event(
            self._event(
                "mindie-agent",
                "",
                session=str(uuid.uuid4()),
                prompt=str(uuid.uuid4()),
                cwd=project,
            )
        )
        self.assertEqual(payload["choices"], [])
        self.assertEqual(payload["experience"], "needs-configuration")
        reporting = payload.get("reporting_choice")
        self.assertIsInstance(reporting, dict, json.dumps(payload.get("reporting"), default=str)[:400])
        self.assertTrue(reporting.get("independent_of_knowledge_contribution"))

    def test_contribute_completes_on_the_unified_entry(self):
        import consent
        import entry

        _config, project = self._config(False)
        cold = entry.dispatch_event(
            self._event(
                "mindie-agent",
                "",
                session=str(uuid.uuid4()),
                prompt=str(uuid.uuid4()),
                cwd=project,
            )
        )
        self.assertEqual(cold["experience"], "needs-configuration")
        contribute = cold
        with self.subTest("user instruction"):
            self.assertNotIn("sharing-enable", contribute.get("next", ""))
        with self.subTest("same command records the choice"):
            payload = entry.dispatch_event(
                self._event(
                    "mindie-agent",
                    "contribute --repository owner/repo --account user "
                    f"--project-root {project} --visibility public",
                    session=str(uuid.uuid4()),
                    prompt=str(uuid.uuid4()),
                    cwd=project,
                )
            )
            saved = consent.load()
            self.assertEqual(saved.get("choice"), "contribute")
            self.assertEqual(payload.get("choices"), [])
            again = entry.dispatch_event(
                self._event(
                    "mindie-agent",
                    "",
                    session=str(uuid.uuid4()),
                    prompt=str(uuid.uuid4()),
                    cwd=project,
                )
            )
            self.assertEqual(again.get("choices"), [])
            self.assertEqual(again.get("first_use"), "contribute")

    def test_entry_choice_does_not_replace_damaged_consent(self):
        import entry

        config, project = self._config(False)
        path = config.parent / "mindie-consent.json"
        original = b"{not-json-consent\n"
        path.write_bytes(original)
        entry.dispatch_event(
            self._event(
                "mindie-agent",
                "disabled",
                session=str(uuid.uuid4()),
                prompt=str(uuid.uuid4()),
                cwd=project,
            )
        )
        self.assertEqual(path.read_bytes(), original)

    def test_skill_text_does_not_require_sharing_enable(self):
        text = (ROOT / "skills" / "mindie-agent" / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("sharing-enable", text)


class SharedConsentTests(LaneCase):



    def test_kimi_choice_is_the_same_profile_authority(self):
        """Uses the committed kimi adapter scripts, not a second CC config."""
        import consent
        import support

        raw = os.environ.get("MINDIE_TEST_KIMI_SCRIPTS")
        self.assertTrue(raw, "set MINDIE_TEST_KIMI_SCRIPTS to the kimi adapter scripts")
        kimi_scripts = Path(raw)
        self.assertTrue((kimi_scripts / "consent.py").is_file(), kimi_scripts)
        case = self.tmp / "both"
        case.mkdir()
        (case / "project").mkdir()
        config = support.make_config(case, sharing=False)
        kimi_config = case / "kimi.json"
        kimi_config.write_text("{}\n", encoding="utf-8")
        env = self._env(config)
        env.pop("MINDIE_CC_CONFIG", None)
        env["MINDIE_KIMI_CONFIG"] = str(kimi_config)
        env["HOME"] = os.environ["HOME"]
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import os,sys; sys.path.insert(0, sys.argv[1]);"
                "os.environ['MINDIE_KIMI_CONFIG']=sys.argv[2];"
                "import consent; consent.record_choice('later')",
                str(kimi_scripts),
                str(kimi_config),
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        self._use(config)
        saved = consent.load()
        self.assertEqual(saved.get("choice"), "later")
        self.assertEqual(saved.get("state"), "ok")
        other = self.tmp / "isolated"
        other.mkdir()
        other_config = other / "cc.json"
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json,os,sys; sys.path.insert(0, sys.argv[1]);"
                "os.environ['MINDIE_CC_CONFIG']=sys.argv[2];"
                "import consent; print(json.dumps(consent.load()))",
                str(SCRIPTS),
                str(other_config),
            ],
            capture_output=True,
            text=True,
            env=self._env(other_config),
            timeout=20,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr[-800:])
        self.assertEqual(json.loads(probe.stdout)["state"], "missing")


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






class DeclaredAuthorityWriteTests(LaneCase):
    """Final core context: one declared lock, no write-then-replay, corrupt bytes stay."""

    def _authority(self, name):
        import support

        case = self.tmp / name
        project = case / "project"
        project.mkdir(parents=True)
        config = support.make_config(case, sharing=True, roots=[str(project)])
        self._use(config)
        legacy = config.parent / "cc.community.json"
        canonical = config.parent / "mindie-community.json"
        return config, legacy, canonical

    def test_write_disabled_preserves_corrupt_community_bytes(self):
        import sharing

        config, legacy, canonical = self._authority("corrupt-disable")
        raw = b"{broken-community\n"
        legacy.write_bytes(raw)
        with self.assertRaises(ValueError):
            sharing.write_disabled()
        self.assertEqual(legacy.read_bytes(), raw)
        self.assertFalse(canonical.exists())
        self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["community_config"], str(legacy))


    def _assert_blocks_on_declared_lock(self, kind):
        import multiprocessing
        import time

        config, legacy, canonical = self._authority("lock-" + kind)
        ctx = multiprocessing.get_context("spawn")
        ready = ctx.Event()
        release = ctx.Event()
        entered = ctx.Event()
        holder = ctx.Process(
            target=_hold_canonical_lock,
            args=(str(legacy), ready, release),
        )
        worker = ctx.Process(
            target=_profile_mutation,
            args=(str(config), os.environ["HOME"], str(SCRIPTS), kind, entered),
        )
        holder.start()
        try:
            self.assertTrue(ready.wait(5), "canonical lock was not acquired")
            worker.start()
            self.assertTrue(entered.wait(8), kind + " did not reach the write")
            time.sleep(0.4)
            self.assertTrue(
                worker.is_alive(),
                kind + " finished while the canonical community lock was held",
            )
            self.assertTrue((legacy.parent / (legacy.name + ".lock")).exists())
            release.set()
            worker.join(6)
            detail = ""
            error_path = config.with_suffix(".worker-error")
            if error_path.is_file():
                detail = error_path.read_text(encoding="utf-8")[-800:]
            self.assertFalse(worker.is_alive(), detail)
            self.assertEqual(worker.exitcode, 0, detail)
        finally:
            release.set()
            for proc in (worker, holder):
                if proc.is_alive():
                    proc.terminate()
                    proc.join(2)
        self.assertFalse((canonical.parent / (canonical.name + ".lock")).exists())
        self.assertTrue((legacy.parent / (legacy.name + ".lock")).exists())
        return config, legacy, canonical


    def test_disable_waits_on_the_declared_lock(self):
        _config, legacy, canonical = self._assert_blocks_on_declared_lock("disable")
        self.assertIs(json.loads(legacy.read_text(encoding="utf-8")).get("enabled"), False)
        self.assertFalse(canonical.is_file())


class BootstrapCommunityWriterTests(LaneCase):
    """setup.write_community uses the installed shared authority.

    The invalid-file case matches integration-20260928/cc-bootstrap-corrupt-repro.json:
    explicit settings must not replace an existing damaged authority.
    """

    def _explicit(self, root: Path):
        import setup

        root.mkdir(parents=True, exist_ok=True)
        return setup.community_settings(
            argparse.Namespace(
                community_repository="owner/repo",
                community_project_root=[str(root)],
                community_account=None,
                community_fork=None,
                community_visibility="public",
                community_branch=None,
            ),
            argparse.ArgumentParser(),
        )

    def test_invalid_existing_file_is_preserved(self):
        import setup

        payload = self._explicit(self.tmp / "project")
        samples = {
            "repro": b"{broken-community",
            "foreign-schema": b'{"schema": "foreign-format/1", "enabled": true}\n',
            "non-object": b"[1]\n",
        }
        for name, raw in samples.items():
            with self.subTest(name=name):
                path = self.tmp / name / "mindie-community.json"
                path.parent.mkdir()
                path.write_bytes(raw)
                with self.assertRaises(SystemExit) as caught:
                    setup.write_community(path, payload)
                self.assertIn("nothing was written", str(caught.exception.code))
                self.assertEqual(path.read_bytes(), raw)
        path = self.tmp / "unreadable" / "mindie-community.json"
        path.parent.mkdir()
        raw = b"{broken-community"
        path.write_bytes(raw)
        path.chmod(0)
        try:
            with self.assertRaises(SystemExit) as caught:
                setup.write_community(path, payload)
            self.assertIn("nothing was written", str(caught.exception.code))
        finally:
            path.chmod(0o600)
        self.assertEqual(path.read_bytes(), raw)

    def test_missing_file_is_a_first_setup(self):
        import setup

        explicit = self.tmp / "first-explicit.json"
        self.assertFalse(explicit.exists())
        self.assertEqual(
            setup.write_community(explicit, self._explicit(self.tmp / "explicit-root")),
            "enabled",
        )
        written = json.loads(explicit.read_text(encoding="utf-8"))
        self.assertEqual(written.get("schema"), "mindie-community-config/1")
        self.assertIs(written.get("enabled"), True)
        self.assertEqual(
            Path(written["consent_config"]).resolve(),
            (explicit.parent / "mindie-consent.json").resolve(),
        )
        default_off = self.tmp / "first-off.json"
        self.assertEqual(setup.write_community(default_off, None), "off")
        off = json.loads(default_off.read_text(encoding="utf-8"))
        self.assertEqual(off.get("schema"), "mindie-community-config/1")
        self.assertIs(off.get("enabled"), False)

    def test_current_schema_is_repaired_and_extensions_survive(self):
        import setup

        path = self.tmp / "repair.json"
        path.write_text(
            json.dumps(
                {
                    "schema": "mindie-community-config/1",
                    "enabled": "not-a-bool",
                    "generation": "stale-generation",
                    "repository": "local/unconfigured",
                    "branch": "main",
                    "project_roots": "not-a-list",
                    "idle_seconds": 300,
                    "adapter_note": "keep",
                }
            )
            + "\n"
        , encoding="utf-8")
        self.assertEqual(
            setup.write_community(path, self._explicit(self.tmp / "repair-root")),
            "enabled",
        )
        written = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(written.get("schema"), "mindie-community-config/1")
        self.assertIs(written.get("enabled"), True)
        self.assertNotEqual(written.get("generation"), "stale-generation")
        self.assertIsInstance(written.get("project_roots"), list)
        self.assertEqual(written.get("adapter_note"), "keep")
        self.assertEqual(
            Path(written["consent_config"]).resolve(),
            (path.parent / "mindie-consent.json").resolve(),
        )

    def test_explicit_publication_leaves_held_reader_on_old_bytes(self):
        """A reader using the product delete-sharing open must keep the previous bytes.

        Reopening the path sees the new enabled document. An in-place
        truncate fails this; atomic replace does not.
        """
        from mindie_knowledge import consent_store
        import setup

        path = self.tmp / "held" / "mindie-community.json"
        path.parent.mkdir()
        old = (
            json.dumps(
                {
                    "schema": "mindie-community-config/1",
                    "enabled": False,
                    "generation": "old-generation",
                    "enabled_at": None,
                    "repository": "local/unconfigured",
                    "branch": "main",
                    "project_roots": [],
                    "idle_seconds": 300,
                }
            )
            + "\n"
        ).encode()
        path.write_bytes(old)
        held = consent_store._open_for_read(path)
        try:
            self.assertEqual(
                setup.write_community(path, self._explicit(self.tmp / "held-root")),
                "enabled",
            )
            held.seek(0)
            self.assertEqual(held.read(), old)
        finally:
            held.close()
        published = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(published.get("schema"), "mindie-community-config/1")
        self.assertIs(published.get("enabled"), True)
        self.assertNotEqual(published.get("generation"), "old-generation")
        self.assertEqual(published.get("repository"), "owner/repo")
        self.assertIsInstance(published.get("project_roots"), list)
        self.assertEqual(
            Path(published["consent_config"]).resolve(),
            (path.parent / "mindie-consent.json").resolve(),
        )


class HookManifestTests(LaneCase):
    def test_expansion_matchers_cover_every_accepted_command(self):
        import identity

        accepted = set(identity.COMMANDS)
        committed = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        committed_matchers = {
            item.get("matcher")
            for item in committed["hooks"]["UserPromptExpansion"]
            if isinstance(item, dict)
        }
        with self.subTest("committed hooks.json"):
            self.assertEqual(sorted(accepted - committed_matchers), [])


if __name__ == "__main__":
    unittest.main()
