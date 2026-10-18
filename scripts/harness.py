"""Claude interfaces and their declared support interval have one registry.

[Role]
Validates `harness-compatibility.json` against the installed Claude Code CLI
version and verifies that all declared script symbols and test IDs exist.

[Invariants]
- Single authoritative declaration (`harness-compatibility.json`) for supported
  Claude Code versions and required contract tests.

[Timeouts & Locks]
- Uses `bounded.run` with a 15-second timeout when probing `claude --version`.

[Side-Effect Policy]
- Read-only AST and version inspection; mutates no files.
"""

import argparse
import ast
import json
from pathlib import Path
import re

from bounded import run
from product_contract import read_json

HARNESS = "claude-code"
REPOSITORY = "mindie-agent/mindie-agent-cc"
DECLARATION = "harness-compatibility.json"


def compatibility(source=None):
    from mindie_knowledge.releases import validate_compatibility

    if source is None:
        source = Path(__file__).resolve().parent / "resources"
        if not (source / DECLARATION).is_file():
            source = Path(__file__).resolve().parent.parent
    value, _ = read_json(Path(source) / DECLARATION)
    return validate_compatibility(value, harness_name=HARNESS)


def native_version(native):
    output = run([str(native), "--version"], timeout=10).strip()
    match = re.fullmatch(r"(\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?) \(Claude Code\)", output)
    if match is None:
        raise ValueError("unsupported Claude version response")
    return match[1]


def require_supported(native, *, source=None):
    from mindie_knowledge.releases import supports_harness

    version = native_version(native)
    if not supports_harness(compatibility(source), harness_name=HARNESS,
                            harness_version=version):
        raise ValueError("installed Claude version is outside this release's declared support")
    return version


def require_engine_support(engine, *, source=None):
    native = engine.get("service_launch_context", {}).get("paths", {}).get("MINDIE_CC_BIN")
    if not isinstance(native, str) or not Path(native).is_absolute():
        raise ValueError("native Claude executable is not bound to the installation")
    return require_supported(native, source=source)


def require_binding_support(binding):
    from mindie_knowledge.loop.cli import config_at

    scripts = Path(binding["runtime_scripts"])
    resources = scripts / "resources"
    source = resources if (resources / DECLARATION).is_file() else scripts.parent
    return require_engine_support(config_at(binding["engine_config"]), source=source)


def metadata(source, revision):
    """Build the GitHub asset after the source commit exists; no self-reference."""
    from mindie_knowledge.releases import validate_release_metadata
    from product_contract import native_version as product_version

    value = dict(
        schema="mindie-plugin-release/1", version=product_version(source),
        source=dict(repository=REPOSITORY, sha=revision),
        compatibility=compatibility(source),
    )
    return validate_release_metadata(value, repository=REPOSITORY, harness_name=HARNESS)


def _has_symbol(path, symbol):
    nodes = ast.parse(path.read_text(encoding="utf-8")).body
    for segment in symbol.replace("::", ".").split("."):
        selected = next((node for node in nodes
                         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                         and node.name == segment), None)
        if selected is None:
            return False
        nodes = selected.body
    return True


def check_registry(source, tests):
    """Check every registered production symbol, test and local evidence file."""
    source, tests = Path(source).resolve(), Path(tests).resolve()
    value = compatibility(source)
    for interface in value["used_interfaces"]:
        row = value["interface_registry"][interface]
        for reference in row["code_paths"]:
            filename, symbol = reference.split(":", 1)
            path = (source / filename).resolve()
            if not path.is_relative_to(source) or not _has_symbol(path, symbol):
                raise ValueError("registered Harness code boundary is missing: " + interface)
        for reference in row["test_ids"]:
            filename, symbol = reference.split("::", 1)
            path = (tests / Path(filename).relative_to("tests")).resolve()
            if not path.is_relative_to(tests) or not _has_symbol(path, symbol):
                raise ValueError("registered Harness contract test is missing: " + interface)
        for reference in row["evidence_refs"]:
            if reference.startswith(("https://", "http://")):
                continue
            path = (source / reference.split("#", 1)[0]).resolve()
            if not path.is_relative_to(source) or not path.is_file():
                raise ValueError("registered Harness evidence is missing: " + interface)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--tests", type=Path, required=True)
    args = parser.parse_args()
    check_registry(args.source, args.tests)
    print(json.dumps(metadata(args.source, args.revision), sort_keys=True))


if __name__ == "__main__":
    main()
