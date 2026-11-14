"""[Role] Prepare independent component releases for Claude's existing binding.
[Invariants] A newer plugin inherits installed component versions. Published
adapter source stays unchanged; the effective lock belongs to a new runtime.
[Timeouts & Locks] Builder 420s plus candidate validation 180s; activation uses
the existing exclusive operation lock and 30s idle handoff.
[Side-Effect Policy] Software metadata/builds and native binding publication;
no knowledge activation, feed maintenance, model or consent changes.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import http.client
import json
from pathlib import Path
import re
import socket
import ssl
import sys
import urllib.error

# Isolated Python omits the script directory; helpers belong to this candidate.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from product_contract import identity, package_files

RETIRED_COMPONENTS = frozenset({"mindie-bench"})
_TRANSPORT_FETCH_CODES = frozenset({"release_http_error", "release_transport_unavailable"})
_LOCAL_HTTP_BUGS = (
    http.client.InvalidURL,
    http.client.ImproperConnectionState,
    http.client.NotConnected,
    http.client.UnimplementedFileMode,
)
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_HOST = frozenset({"FileNotFoundError", "CalledProcessError"})
_HOST_CODES = frozenset({"invalid_harness_version", "invalid_installed_version"})
_TIMEOUT = frozenset({"TimeoutExpired", "TimeoutError"})


class CatalogError(RuntimeError):
    """Expected catalog transport miss; static codes only, never raw feed text."""


def _copy_process_attrs(source, target):
    if (returncode := getattr(source, "returncode", None)) is not None:
        target.returncode = returncode
    if (stage := getattr(source, "mindie_stage", None)) is not None:
        target.mindie_stage = stage
    return target


def _finite_delay(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or number in (float("inf"), float("-inf")) or number < 0:
        return None
    return number


def _is_cert_verification_error(exc) -> bool:
    if isinstance(exc, ssl.SSLCertVerificationError):
        return True
    return isinstance(exc, ssl.SSLError) and getattr(exc, "reason", None) == "CERTIFICATE_VERIFY_FAILED"


def classify_transport_exception(exc):
    """Classify typed stdlib HTTP/TLS/socket transport errors without free-text parsing."""
    if exc is None or isinstance(exc, TimeoutError):
        return None
    if isinstance(exc, urllib.error.HTTPError):
        headers = getattr(exc, "headers", None)
        raw_retry = headers.get("Retry-After") if headers is not None and hasattr(headers, "get") else None
        retry_after = _finite_delay(raw_retry) if raw_retry is not None else None
        return ("rate_limited" if exc.code == 429 else "temporary_network", retry_after)
    if _is_cert_verification_error(exc):
        return ("certificate", None)
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if _is_cert_verification_error(reason) or _is_cert_verification_error(getattr(exc, "__cause__", None)):
            return ("certificate", None)
        return ("temporary_network", None)
    if isinstance(exc, ssl.SSLError):
        if _is_cert_verification_error(getattr(exc, "__cause__", None)):
            return ("certificate", None)
        return ("temporary_network", None)
    if isinstance(exc, _LOCAL_HTTP_BUGS):
        return None
    if isinstance(exc, (ConnectionError, http.client.HTTPException, socket.gaierror, socket.herror)):
        return ("temporary_network", None)
    return None


def _catalog_error(code, *, category="temporary_network", status=None, retry_after=None, source=None):
    error = CatalogError(code)
    error.code = code
    error.category = category
    if type(status) is int and 100 <= status <= 599:
        error.status = status
    if retry_after is not None:
        error.retry_after = retry_after
    return _copy_process_attrs(source, error) if source is not None else error


def emit_catalog_error(exc) -> None:
    """Print one static catalog error object; do not echo feed or host text."""
    payload = {"error": {"type": type(exc).__name__}}
    code = getattr(exc, "code", None)
    if isinstance(code, str) and _CODE.fullmatch(code):
        payload["error"]["code"] = code
    status = getattr(exc, "status", None)
    if type(status) is int and 100 <= status <= 599:
        payload["error"]["status"] = status
    print(json.dumps(payload, sort_keys=True))
    raise SystemExit(1)


def _error_payload(raw):
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        data = json.loads(raw.strip())
    except (ValueError, TypeError):
        return None
    error = data.get("error") if isinstance(data, dict) else None
    return error if isinstance(error, dict) else None


def typed_catalog_error(exc):
    """Rebuild typed transport catalog facts while keeping source-integrity faults reportable.

    Must work in stdlib-only bootstrap interpreters where ``mindie_knowledge`` is absent.
    """
    payload = _error_payload(getattr(exc, "mindie_output", None))
    if payload is not None:
        kind = payload.get("type")
        code = payload.get("code")
        code = code if isinstance(code, str) and _CODE.fullmatch(code) else None
        status = payload.get("status")
        status = status if type(status) is int and 100 <= status <= 599 else None
        if kind in _HOST or code in _HOST_CODES:
            error = FileNotFoundError("component helper is unavailable for catalog selection")
        elif kind in _TIMEOUT:
            error = TimeoutError("component catalog probe timed out")
        elif kind == "ReleaseResponseTooLarge":
            return exc
        elif kind == "ReleaseFetchError" and code not in _TRANSPORT_FETCH_CODES and status != 429:
            try:
                from mindie_knowledge.releases_io import ReleaseFetchError
            except ImportError:
                return exc
            error = ReleaseFetchError(code or "invalid_release_json", status=status)
        else:
            category = (
                "certificate" if code == "release_certificate_error"
                else "rate_limited" if status == 429
                else "temporary_network"
            )
            error = _catalog_error(
                code or "release_transport_unavailable",
                category=category,
                status=status,
            )
        return _copy_process_attrs(exc, error)
    try:
        from mindie_knowledge.release_http import ReleaseResponseTooLarge
        from mindie_knowledge.releases_io import ReleaseFetchError
    except ImportError:
        ReleaseResponseTooLarge = ()
        ReleaseFetchError = ()
    if isinstance(exc, ReleaseResponseTooLarge):
        return exc
    if isinstance(exc, ReleaseFetchError):
        if exc.code in _TRANSPORT_FETCH_CODES or exc.status == 429:
            return _catalog_error(
                exc.code,
                category="rate_limited" if exc.status == 429 else "temporary_network",
                status=exc.status,
                source=exc,
            )
        return exc
    if isinstance(exc, urllib.error.HTTPError):
        kind = classify_transport_exception(exc)
        return _catalog_error(
            "release_http_error",
            category=kind[0] if kind else ("rate_limited" if exc.code == 429 else "temporary_network"),
            status=exc.code,
            retry_after=kind[1] if kind else None,
            source=exc,
        )
    if isinstance(exc, json.JSONDecodeError):
        return _catalog_error("release_catalog_invalid", source=exc)
    if (kind := classify_transport_exception(exc)) is not None:
        category, retry_after = kind
        return _catalog_error(
            "release_certificate_error" if category == "certificate" else "release_transport_unavailable",
            category=category,
            retry_after=retry_after,
            source=exc,
        )
    return exc


def _discover_catalog(*args, **kwargs):
    from mindie_knowledge.component_runtime import discover

    try:
        return discover(*args, **kwargs)
    except TimeoutError:
        raise
    except Exception as exc:
        raise typed_catalog_error(exc) from None


@contextmanager
def _without_retired_inventory(runtime_module, retired_state: dict):
    original = getattr(runtime_module, "read_inventory", None)
    if original is None:
        yield
        return

    def filtered_inventory(python, *args, **kwargs):
        installed = dict(original(python, *args, **kwargs))
        removed = {
            name: installed.pop(name)
            for name in list(installed)
            if name in RETIRED_COMPONENTS
        }
        if removed:
            retired_state["removed"] = True
        return installed

    runtime_module.read_inventory = filtered_inventory
    try:
        yield
    finally:
        runtime_module.read_inventory = original


def prepare(source, python, root, *, current_python=None, force=False, current_source=None,
            plugin_upgrade=False):
    import mindie_knowledge.component_runtime as component_runtime
    from mindie_knowledge.component_bindings import managed_commands, required_components, rebind_commands
    from mindie_knowledge.component_runtime import build
    from bootstrap import readback
    from paths import config_path
    config = config_path()
    adapter = json.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
    if adapter.get("base_config"):
        adapter = json.loads(Path(adapter["base_config"]).read_text(encoding="utf-8"))
    managed = {
        name: cmd
        for name, cmd in managed_commands(adapter).items()
        if name not in RETIRED_COMPONENTS
    }
    if plugin_upgrade and not adapter.get("component_source") and not managed:
        return None
    baseline = identity(source)
    required = {
        name: version
        for name, version in required_components(adapter).items()
        if name not in RETIRED_COMPONENTS
    }
    retired_state = {"removed": False}
    with _without_retired_inventory(component_runtime, retired_state):
        selected = _discover_catalog(
            current_python or python, baseline["runtime"], force=force,
            required_components=required,
        )
    if isinstance(selected.get("runtime"), dict):
        for name in RETIRED_COMPONENTS:
            if name in selected["runtime"]:
                selected["runtime"] = {
                    k: v for k, v in selected["runtime"].items() if k not in RETIRED_COMPONENTS
                }
                retired_state["removed"] = True
                break
    needs_binding = bool(adapter.get("component_commands")) and (
        rebind_commands(adapter, python, selected["runtime"]) != adapter["component_commands"])
    changed = bool(selected.get("changed")) or retired_state["removed"]
    if not changed and current_source == baseline["source_sha256"] and not needs_binding:
        return None
    pins = {name: row["revision"] for name, row in selected.get("runtime", {}).items()}
    if (current_python is not None or not changed) and pins == baseline["runtime"] and not needs_binding:
        return None
    artifact = build(source, root, baseline, selected["runtime"], python=python,
                     adapter_distribution="mindie-agent-cc",
                     files=[str(path.relative_to(source)) for path in package_files(source)])
    scripts = readback(Path(artifact["python"]), Path(artifact["source"]))
    return dict(artifact, scripts=str(scripts))


def check(binding, source, *, force=False):
    from bounded import run
    try:
        artifact = prepare(source, binding["python"], Path(binding["state_dir"]) / "component-runtimes", force=force,
                           current_source=binding["product_validation"]["source_sha256"])
        if artifact is None:
            return dict(status="up_to_date")
        output = run([artifact["python"], str(Path(artifact["scripts"]) / "setup.py"),
                      "--source", artifact["source"], "--config", binding["base_config"]],
                     timeout=360, transport=True)
        prepared = json.loads(output)
        result = run([artifact["python"], str(Path(artifact["scripts"]) / "setup.py"),
                      "--activate", prepared["config"]],
                     timeout=40, transport=True)
        return dict(json.loads(result), runtime=artifact["components"])
    except BaseException as exc:
        if not getattr(exc, "mindie_stage", None):
            exc.mindie_stage = "component_update"
        raise


def main():
    from mindie_knowledge.release_http import ReleaseResponseTooLarge
    from mindie_knowledge.releases_io import ReleaseFetchError

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("python")
    parser.add_argument("root", type=Path)
    parser.add_argument("current_python")
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.source, args.python, args.root,
                                 current_python=args.current_python, plugin_upgrade=True), sort_keys=True))
    except _LOCAL_HTTP_BUGS:
        raise
    except (CatalogError, ReleaseFetchError, ReleaseResponseTooLarge,
            urllib.error.HTTPError, urllib.error.URLError, ssl.SSLError,
            ConnectionError, http.client.HTTPException, socket.gaierror,
            socket.herror, json.JSONDecodeError, FileNotFoundError, TimeoutError) as exc:
        emit_catalog_error(exc)


if __name__ == "__main__":
    main()
