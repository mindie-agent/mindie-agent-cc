"""Native slash-command argument parsing for the Claude Code entry.

[Role]
Parses contribution flags, command identity and explicit domain selection.

[Invariants]
- Native arguments never supply admission tokens or engine config mappings.
- Domain activation cannot be combined with installation-level consent choices.
- The entry module re-exports these helpers under their existing names.

[Timeouts & Locks]
- No locks or subprocesses; project paths use ordinary filesystem resolution.

[Side-Effect Policy]
- Returns validated arguments without writing configuration or activating leases.
"""
from __future__ import annotations

import os
from pathlib import Path
import shlex


def _parse_sharing(text):
    try:
        argv = shlex.split(text or "")
    except ValueError:
        raise ValueError("could not parse sharing-enable arguments")
    repository = None
    roots = []
    branch = "main"
    visibility = None
    account = None
    fork = None
    args = list(argv)
    while args:
        item = args.pop(0)
        if item in {"--repository", "--community-repository"} and args:
            repository = args.pop(0)
        elif item in {"--project-root", "--community-project-root"} and args:
            roots.append(str(Path(args.pop(0)).expanduser().resolve()))
        elif item in {"--branch", "--community-branch"} and args:
            branch = args.pop(0)
        elif item in {"--visibility", "--community-visibility"} and args:
            visibility = args.pop(0)
        elif item in {"--account", "--community-account"} and args:
            account = args.pop(0)
        elif item in {"--fork", "--community-fork"} and args:
            fork = args.pop(0)
        elif item.startswith("--"):
            raise ValueError("unknown sharing flag: " + item)
    if not repository or not roots or visibility != "public" or not account:
        raise ValueError(
            "contribution requires --repository owner/repo, "
            "--account USER, --project-root /absolute/path, and --visibility public"
        )
    return dict(
        repository=repository,
        project_roots=roots,
        branch=branch,
        visibility=visibility,
        account=account,
        fork=fork,
    )


def _prompt_id(event) -> str:
    value = event.get("prompt_id")
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError("native prompt_id is missing")
    return value


def _project_root(cwd):
    if isinstance(cwd, str) and os.path.isabs(cwd):
        return str(Path(cwd).resolve())
    raise ValueError("native cwd is required to activate; env session vars are not used")


def _init_choice_from_native(arguments):
    text = (arguments or "").strip()
    if not text:
        return None
    token = text.split()[0]
    if token in {"read-only", "later"}:
        raise ValueError("read-only/later product modes were removed; configure the destination and scope, or use disabled")
    if token in {"contribute", "disabled"}:
        return token
    return None


def _domain_from_native(arguments):
    """Read the optional native ``--domain`` selector.

    The value is only a canonical domain name; engine paths, mappings, session
    ids and tokens are never accepted from slash-command arguments. Plain
    whitespace tokenization preserves the existing no-domain init behavior.
    Mixed install-level choices are rejected only after a domain was selected;
    a consumed ``--domain`` value is never treated as a choice.
    """
    tokens = (arguments or "").split()
    selected = None
    choice = None
    index = 0
    while index < len(tokens):
        item = tokens[index]
        index += 1
        if item == "--domain":
            if index >= len(tokens):
                raise ValueError("--domain requires one canonical domain")
            if selected is not None:
                raise ValueError("--domain may be specified once")
            selected = tokens[index]
            index += 1
        elif item.startswith("--domain="):
            if selected is not None:
                raise ValueError("--domain may be specified once")
            selected = item.split("=", 1)[1]
        elif item in {
            "contribute", "disabled",
            "reporting-enable", "reporting-disable", "reporting-later",
        }:
            choice = item
    if selected is not None and choice is not None:
        raise ValueError(
            "--domain activates a selected domain and cannot be combined "
            "with contribute/disabled/reporting choices"
        )
    return selected


def _reporting_token_from_native(arguments):
    for token in (arguments or "").split():
        if token in {"reporting-enable", "reporting-disable", "reporting-later"}:
            return token
    return None
