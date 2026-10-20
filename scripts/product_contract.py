"""Stdlib product identity and validation receipts; uv.lock owns dependency pins.

[Role]
Computes deterministic source/product/dependency digests (`product-contract.json`,
`uv.lock`, `package_files`) and validates candidate receipts.

[Invariants]
- `uv.lock` is the sole dependency authority, requiring exact 40-hex commit pins
  for `mindie-knowledge`, `remote-dev`, and `mindie-diagnostics`; a declared
  `project.dependencies` block in `pyproject.toml` must agree with those pins.
- Exit status stays authoritative in `failure_receipt`.

[Timeouts & Locks]
- Pure stdlib file hashing and bounded `candidate_validate.py` subprocess probing.

[Side-Effect Policy]
- Read-only validation and digest computation.
"""

from __future__ import annotations

import hashlib
import json
from functools import cache
from importlib import metadata
from pathlib import Path
import re
import tomllib

PRODUCT_SCHEMA = "mindie-product/1"
RECEIPT_SCHEMA = "mindie-candidate-check/1"
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
PUBLICATION_FIELDS = {"repository", "ref", "domain", "verified_commit"}
OFFICIAL_FEEDS = (
    dict(domain="vllm-ascend", repository="mindie-agent/knowledge-vllm-ascend",
         ref="main", interval_seconds=300),
    dict(domain="operators", repository="mindie-agent/knowledge-operators",
         ref="main", interval_seconds=300),
)
SOURCE_LIMIT = 16 * 1024 * 1024


@cache
def installed_version():
    """The installed distribution is the runtime's release identity."""
    return metadata.version("mindie-agent-cc")

FAILURE_CODES = {
    "source": {"source_invalid", "source_changed"},
    "adapter": {"adapter_incompatible", "release_state_undeclared", "release_version_mismatch"},
    "runtime_pins": {
        "package_missing", "receipt_missing", "receipt_invalid",
        "revision_mismatch", "metadata_unavailable",
    },
    "runtime_api": {"api_incompatible"},
    "publication_fetch": {"init_failed", "fetch_failed", "revision_read_failed", "revision_mismatch"},
    "publication_contract": {"contract_mismatch", "read_failed", "validator_mismatch"},
    "receipt": {"receipt_mismatch"},
    "validation": {"internal_error"},
    "process": {"nonzero_exit"},
}
FAILURE_COMPONENTS = {"mindie-knowledge", "remote-dev", "mindie-diagnostics"}
PUBLICATION_PROCESS_CODES = {"init_failed", "fetch_failed", "revision_read_failed"}


def _valid_process_failure(value):
    if (not isinstance(value, dict)
            or set(value) != {"execution", "reason", "returncode", "cleanup_failed"}
            or type(value["execution"]) is not str
            or type(value["reason"]) is not str
            or value["execution"] not in {"not_started", "completed", "unknown"}
            or type(value["cleanup_failed"]) is not bool):
        return False
    execution, reason, code = value["execution"], value["reason"], value["returncode"]
    if code is not None and (type(code) is not int or not -(2 ** 31) <= code < 2 ** 32):
        return False
    if execution == "not_started" and code is not None:
        return False
    if execution == "completed" and code is None:
        return False
    if reason == "nonzero_exit":
        return execution == "completed" and code != 0
    if reason == "cleanup_failed":
        return execution == "completed" and code == 0 and value["cleanup_failed"]
    if reason == "spawn_failed":
        return execution != "completed"
    if reason in {"timeout", "output_limit"}:
        return execution == "unknown"
    if reason == "owner_exited":
        return execution != "completed"
    if reason == "cancelled":
        return True
    if reason == "process_failed":
        return execution != "completed" or code == 0
    return False


class CandidateValidationError(RuntimeError):
    """Only protocol-owned fields and validated process facts reach callers."""

    def __init__(self, failure):
        self.stage = failure["stage"]
        self.code = failure["code"]
        self.component = failure.get("component")
        self.process = failure.get("process")
        suffix = " (" + self.component + ")" if self.component else ""
        if self.process is not None:
            suffix += (
                f" (execution={self.process['execution']}, reason={self.process['reason']},"
                f" returncode={self.process['returncode']},"
                f" cleanup_failed={self.process['cleanup_failed']})"
            )
        super().__init__(
            f"Candidate validation failed at {self.stage}: {self.code}{suffix}; not retried"
        )


def failure_receipt(output, expected, returncode):
    """Exit status stays authoritative. A success receipt from a failing process is a failure."""
    fallback = CandidateValidationError(dict(stage="process", code="nonzero_exit"))
    try:
        value = json.loads(output, object_pairs_hook=_object)
        failure = value["failure"]
        if (not isinstance(failure, dict)
                or set(failure) not in (
                    {"stage", "code"}, {"stage", "code", "component"},
                    {"stage", "code", "process"},
                )
                or failure.get("stage") not in FAILURE_CODES
                or failure.get("code") not in FAILURE_CODES[failure["stage"]]
                or ("component" in failure and failure["component"] not in FAILURE_COMPONENTS)
                or ("process" in failure and (
                    failure["stage"] != "publication_fetch"
                    or failure["code"] not in PUBLICATION_PROCESS_CODES
                    or not _valid_process_failure(failure["process"])
                ))
                or value != dict(expected, status="failed", failure=failure)
                or returncode == 0):
            return fallback
        return CandidateValidationError(failure)
    except (ValueError, KeyError, TypeError):
        return fallback


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate contract field: " + key)
        result[key] = value
    return result


def read_json(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError("required contract is not a regular file: " + str(path))
    with path.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("contract exceeds the metadata byte bound")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_object), raw


def requirements(source):
    """Read exact runtime pins from uv.lock and reject pyproject.toml drift."""
    path = Path(source) / "uv.lock"
    if not path.is_file():
        raise ValueError("uv.lock must be a regular file")
    raw = path.read_bytes()
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("uv.lock exceeds the metadata byte bound")
    pins = {}
    expected = {"mindie-knowledge": "knowledge", "remote-dev": "remote-dev",
                "mindie-diagnostics": "diagnostics"}
    for package in tomllib.loads(raw.decode("utf-8")).get("package", []):
        name = package.get("name")
        if name not in expected:
            continue
        source_url = package.get("source", {}).get("git", "")
        match = re.fullmatch(
            r"https://github\.com/mindie-agent/" + re.escape(expected[name])
            + r"\?rev=([0-9a-f]{40})#\1", source_url,
        )
        if match is None or name in pins:
            raise ValueError("runtime dependencies require exact official commit pins")
        pins[name] = match[1]
    if set(pins) != set(expected):
        raise ValueError("invalid runtime package combination")
    project_path = Path(source) / "pyproject.toml"
    if project_path.is_file():
        project = tomllib.loads(project_path.read_text(encoding="utf-8")).get("project")
        dependencies = project.get("dependencies") if isinstance(project, dict) else None
        if dependencies is not None:
            if not isinstance(dependencies, list):
                raise ValueError("pyproject.toml and uv.lock runtime pins differ")
            declared = set()
            for dependency in dependencies:
                if not isinstance(dependency, str):
                    raise ValueError("pyproject.toml and uv.lock runtime pins differ")
                reference = re.fullmatch(
                    r"(?P<name>[A-Za-z0-9_.-]+)(?:\[[^\]]*\])?\s*@\s*"
                    r"(?P<url>git\+\S+)", dependency,
                )
                if reference is None:
                    continue
                name = reference["name"]
                if name not in expected:
                    continue
                match = re.fullmatch(
                    r"git\+https://github\.com/mindie-agent/"
                    r"(?P<repo>[A-Za-z0-9_.-]+)@(?P<rev>[0-9a-f]{40})",
                    reference["url"],
                )
                if (match is None or match["repo"] != expected[name]
                        or pins[name] != match["rev"] or name in declared):
                    raise ValueError("pyproject.toml and uv.lock runtime pins differ")
                declared.add(name)
    return pins, hashlib.sha256(raw).hexdigest()


def _publication(value, label):
    if not isinstance(value, dict) or set(value) != PUBLICATION_FIELDS:
        raise ValueError("invalid " + label + " declaration")
    if not all(isinstance(value[field], str) for field in PUBLICATION_FIELDS):
        raise ValueError(label + " fields must be text")
    if (not REPOSITORY.fullmatch(value["repository"])
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", value["ref"])
            or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value["domain"])
            or not HEX40.fullmatch(value["verified_commit"])):
        raise ValueError("invalid " + label + " identity")
    return value


def product(source):
    value, raw = read_json(Path(source) / "product-contract.json")
    if not isinstance(value, dict) or set(value) != {"schema", "validation", "publication"}:
        raise ValueError("invalid product declaration")
    if value["schema"] != PRODUCT_SCHEMA or value["validation"] != RECEIPT_SCHEMA:
        raise ValueError("unsupported product or candidate receipt protocol")
    _publication(value["publication"], "product publication")
    return value, hashlib.sha256(raw).hexdigest()


def official_feeds():
    """The two official, moving public feeds; no content digest gating."""
    return [dict(row) for row in OFFICIAL_FEEDS]


def publication_feed(declaration, *, domain=None):
    row = next((item for item in OFFICIAL_FEEDS
                if domain is None or item["domain"] == domain), None)
    return dict(row) if row is not None else None


def source_root(scripts):
    scripts = Path(scripts).resolve()
    # A source checkout and a copied plugin both carry the same declarations.
    for root in (scripts.parent, scripts.parents[2] if len(scripts.parents) > 2 else None):
        if root is not None and (root / "product-contract.json").is_file():
            return root
    raise ValueError("candidate product declaration is missing")


def _plugin_root(source):
    source = Path(source)
    nested = source / "plugins" / "mindie-agent"
    if nested.is_dir():
        return nested
    return source


def package_files(source):
    """Select executable/plugin inputs, excluding checkout and runtime state."""
    plugin = _plugin_root(source)
    result = set()
    for name in (".claude-plugin", ".mcp.json", "hooks", "scripts", "skills",
                 "product-contract.json", "harness-compatibility.json", "pyproject.toml", "uv.lock"):
        path = plugin / name
        entries = [path]
        if path.is_dir():
            entries.extend(path.rglob("*"))
        for item in entries:
            if "__pycache__" in item.parts or item.suffix == ".pyc":
                continue
            if item.exists() and not item.is_dir():
                if not item.is_file():
                    raise ValueError("candidate package must contain regular files")
                result.add(item)
    return result


def source_identity(source):
    """Bind preparation to source bytes, including the candidate validator."""
    source = Path(source)
    plugin = _plugin_root(source)
    files = []
    for name in ("product-contract.json", "pyproject.toml", "uv.lock"):
        path = source / name
        if path.is_file():
            files.append(path)
    files.extend(package_files(source))
    digest = hashlib.sha256()
    size = 0
    for path in sorted(set(files)):
        if not path.is_file():
            raise ValueError("candidate source must contain regular files")
        with path.open("rb") as stream:
            raw = stream.read(SOURCE_LIMIT - size + 1)
        size += len(raw)
        if size > SOURCE_LIMIT:
            raise ValueError("candidate package exceeds 16 MiB")
        name = path.relative_to(source).as_posix().encode("utf-8")
        digest.update(len(name).to_bytes(8, "big") + name)
        digest.update(len(raw).to_bytes(8, "big") + raw)
    return digest.hexdigest()


def identity(source, revision=None):
    declaration, product_digest = product(source)
    pins, requirement_digest = requirements(source)
    source_digest = source_identity(source)
    if revision is not None and not HEX40.fullmatch(revision):
        raise ValueError("candidate revision must be an immutable commit")
    return dict(
        schema=RECEIPT_SCHEMA, candidate_revision=revision,
        native_version=native_version(source),
        source_sha256=source_digest, product_sha256=product_digest,
        requirements_sha256=requirement_digest, runtime=pins,
        publication=declaration["publication"],
    )


def native_version(source):
    plugin = _plugin_root(source)
    version = read_json(plugin / ".claude-plugin" / "plugin.json")[0]["version"]
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("native plugin version must be a release version")
    project = tomllib.loads((plugin / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if project.get("name") != "mindie-agent-cc" or project.get("version") != version:
        raise ValueError("native plugin and Python project versions differ")
    return version


def validate_receipt(output, expected):
    value = json.loads(output, object_pairs_hook=_object)
    if not isinstance(value, dict) or value != dict(expected, status="validated"):
        raise ValueError("candidate validation receipt does not match the requested source")
    return value


def _completed(command, argv):
    """Accept this adapter's stdout-only runner and a Codex-style completed result."""
    result = command(argv, timeout=None, max_output=65536)
    if isinstance(result, str):
        if not result.strip():
            raise CandidateValidationError(dict(stage="process", code="nonzero_exit"))
        return result, 0
    output = getattr(result, "stdout", None)
    code = getattr(result, "returncode", None)
    if not isinstance(output, str) or type(code) is not int:
        raise CandidateValidationError(dict(stage="process", code="nonzero_exit"))
    return output, code


def probe(python, scripts, command, *, revision=None, verified_receipt=None):
    scripts = Path(scripts).resolve()
    source = source_root(scripts)
    expected = identity(source, revision)
    argv = [
        str(python), "-I", "-B", str(scripts / "candidate_validate.py"),
        "--source", str(source), "--identity", json.dumps(expected, sort_keys=True),
    ]
    if verified_receipt is not None:
        validate_receipt(json.dumps(verified_receipt), expected)
        argv.extend(["--verified-receipt", json.dumps(verified_receipt, sort_keys=True)])

    def finish(output, code):
        if code != 0:
            raise failure_receipt(output, expected, code)
        receipt = validate_receipt(output, expected)
        if identity(source, revision) != expected:
            raise ValueError("candidate source changed during validation")
        return receipt

    try:
        output, code = _completed(command, argv)
    except CandidateValidationError:
        raise
    except TypeError:
        # A runner that requires an on_failure callback uses that path.
        # A stdout-only runner reports failure by raising, with any captured
        # validator JSON attached as mindie_output.
        try:
            output = command(
                argv, timeout=None, max_output=65536,
                on_failure=lambda text, status: failure_receipt(text, expected, status),
            )
        except CandidateValidationError:
            raise
        except Exception as exc:
            captured = getattr(exc, "mindie_output", "")
            code = getattr(exc, "returncode", 1)
            if not isinstance(captured, str):
                captured = ""
            if type(code) is not int or code == 0:
                code = 1
            raise failure_receipt(captured, expected, code) from exc
        return finish(output, 0)
    except Exception as exc:
        captured = getattr(exc, "mindie_output", None)
        code = getattr(exc, "returncode", 1)
        if isinstance(captured, str):
            if type(code) is not int or code == 0:
                code = 1
            raise failure_receipt(captured, expected, code) from exc
        raise
    return finish(output, code)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    declaration, digest = product(args.source)
    pins, requirements_digest = requirements(args.source)
    print(json.dumps(dict(
        product=declaration, product_sha256=digest,
        runtime=pins, requirements_sha256=requirements_digest,
    )))
