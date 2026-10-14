"""Candidate-owned, model-free validation in the candidate interpreter.

Only this candidate imports its private runtime probe. Publication bytes are
read from the fixed official Git commit and never select executable code.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import importlib.metadata
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from product_contract import identity, read_json, validate_receipt
from runtime_probe import build_probe_script


class CheckFailure(ValueError):
    def __init__(self, stage, code, component=None):
        self.failure = dict(stage=stage, code=code)
        if component is not None:
            self.failure["component"] = component
        super().__init__(stage + ": " + code)


def checked(stage, code, operation):
    try:
        return operation()
    except CheckFailure:
        raise
    except Exception as exc:
        raise CheckFailure(stage, code) from exc


def _plugin(source):
    source = Path(source)
    nested = source / "plugins" / "mindie-agent"
    if nested.is_dir():
        return nested
    return source


def adapter_check(source):
    """This candidate owns its Claude Code adapter layout and safety checks."""
    plugin = _plugin(source)
    if any(path.is_symlink() for path in plugin.rglob("*")):
        raise ValueError("plugin source must contain regular files")
    if sum(path.stat().st_size for path in plugin.rglob("*") if path.is_file()) > 16 * 1024 * 1024:
        raise ValueError("plugin package exceeds 16 MiB")
    hooks = read_json(plugin / "hooks" / "hooks.json")[0]["hooks"]
    if "Stop" not in hooks or len(hooks["Stop"]) != 1:
        raise ValueError("only one Stop hook is supported")
    entries = hooks["Stop"][0]["hooks"]
    if len(entries) != 1:
        raise ValueError("Stop must have one command")
    for name in (
        "transcript.py",
        "mindie_launch.py",
        "bounded.py",
        "mcp_server.py",
        "state_compatibility.py",
        "receipt_layout.py",
        "candidate_validate.py",
        "product_contract.py",
        "runtime_probe.py",
        "setup.py",
        "updater.py",
    ):
        if not (plugin / "scripts" / name).is_file():
            raise ValueError("missing bounded runtime entry: " + name)
    # The catalogue is source text. Executable selection stays on the Git pin.
    text = (plugin / "scripts" / "mcp_server.py").read_text(encoding="utf-8")
    for name in ("knowledge_query", "knowledge_explain", "knowledge_feedback"):
        if f'name="{name}"' not in text and f"name='{name}'" not in text:
            raise ValueError("adapter knowledge catalogue is incomplete")
    if "knowledge_use" in text or "knowledge_judge" in text:
        raise ValueError("retired knowledge tools are advertised")
    release_check(plugin)


def release_check(plugin):
    """A normal product version must declare both durable state boundaries."""
    version = read_json(plugin / ".claude-plugin" / "plugin.json")[0]["version"]
    base = version.split("+", 1)[0] if isinstance(version, str) else ""
    if re.fullmatch(r"\d+\.\d+\.\d+", base):
        from mindie_knowledge.state_layout import RELEASE_VERSION as knowledge_release
        from receipt_layout import RELEASE_VERSION as receipt_release
        if knowledge_release is None or receipt_release is None:
            raise CheckFailure("adapter", "release_state_undeclared")
        if knowledge_release != base or receipt_release != base:
            raise CheckFailure("adapter", "release_version_mismatch")


def installed_revisions(pins):
    for name, expected in pins.items():
        try:
            raw = importlib.metadata.distribution(name).read_text("direct_url.json")
        except importlib.metadata.PackageNotFoundError as exc:
            raise CheckFailure("runtime_pins", "package_missing", name) from exc
        except Exception as exc:
            raise CheckFailure("runtime_pins", "metadata_unavailable", name) from exc
        if not raw:
            raise CheckFailure("runtime_pins", "receipt_missing", name)
        try:
            actual = (json.loads(raw).get("vcs_info") or {}).get("commit_id")
        except (ValueError, TypeError, AttributeError) as exc:
            raise CheckFailure("runtime_pins", "receipt_invalid", name) from exc
        if actual != expected:
            raise CheckFailure("runtime_pins", "revision_mismatch", name)


def runtime_check(scripts):
    output = io.StringIO()
    with redirect_stdout(output):
        exec(build_probe_script(Path(scripts) / "transcript.py"), {})
    if output.getvalue().strip() != "OK":
        raise ValueError(output.getvalue().strip() or "candidate runtime probe returned no result")


def publication_contract(publication):
    """One read-only fetch. No checkout, Git hooks, or retries."""
    from bounded import run
    from mindie_knowledge.publication_contract import read_git_contract

    env = dict(
        os.environ, GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
    )
    with tempfile.TemporaryDirectory(prefix="mindie-publication-contract-") as directory:
        git = ["git", "-c", "core.hooksPath=" + os.devnull, "-C", directory]

        def once(args):
            return run(args, "", timeout=None, env=env)

        checked("publication_fetch", "fetch_failed", lambda: once(git + ["init", "--bare", "--quiet"]))
        checked("publication_fetch", "fetch_failed", lambda: once(git + [
            "fetch", "--no-auto-maintenance", "--depth=1", "--no-tags",
            "https://github.com/" + publication["repository"] + ".git",
            publication["verified_commit"],
        ]))
        observed = checked(
            "publication_fetch", "fetch_failed", lambda: once(git + ["rev-parse", "FETCH_HEAD"])
        ).strip()
        if observed != publication["verified_commit"]:
            raise CheckFailure("publication_fetch", "revision_mismatch")
        try:
            return read_git_contract(
                directory, observed, publication["domain"],
                expected_sha256=publication["contract_sha256"], env=env,
            )
        except Exception as exc:
            code = "contract_mismatch" if getattr(exc, "code", None) == "contract_mismatch" else "read_failed"
            raise CheckFailure("publication_contract", code) from exc


def validate(source, expected, verified_receipt=None):
    actual = checked("source", "source_invalid", lambda: identity(source, expected.get("candidate_revision")))
    if actual != expected:
        raise CheckFailure("source", "source_changed")
    checked("adapter", "adapter_incompatible", lambda: adapter_check(Path(source)))
    installed_revisions(actual["runtime"])
    checked("runtime_api", "api_incompatible", lambda: runtime_check(Path(__file__).resolve().parent))
    if verified_receipt is None:
        checked("publication_contract", "read_failed", lambda: publication_contract(actual["publication"]))
    else:
        checked("receipt", "receipt_mismatch", lambda: validate_receipt(json.dumps(verified_receipt), actual))
    if checked("source", "source_invalid", lambda: identity(source, actual["candidate_revision"])) != actual:
        raise CheckFailure("source", "source_changed")
    return dict(actual, status="validated")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--verified-receipt")
    args = parser.parse_args()
    expected = {}
    try:
        expected = json.loads(args.identity)
        previous = json.loads(args.verified_receipt) if args.verified_receipt else None
        result = validate(args.source, expected, previous)
    except Exception as exc:
        failure = exc.failure if isinstance(exc, CheckFailure) else dict(stage="validation", code="internal_error")
        result = dict(expected, status="failed", failure=failure) if isinstance(expected, dict) else dict(status="failed", failure=failure)
        sys.stdout.buffer.write((json.dumps(result, sort_keys=True) + "\n").encode("utf-8"))
        return 1
    sys.stdout.buffer.write((json.dumps(result, sort_keys=True) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
