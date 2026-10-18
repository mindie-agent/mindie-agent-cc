"""Checks for the installed, locked adapter runtime.

Import success is not worker compatibility. The probe checks the current
block index, result, usage, and identity contract. It does not require the
retired SQLite FTS5 contentless_delete capability.
"""

import os


PROBE_MODULES = (
    "mindie_knowledge.loop.cli",
    "mindie_knowledge.loop.documents",
    "mindie_knowledge.loop.activation",
    "mindie_knowledge.loop.summary_scratch",
    "mindie_knowledge.materials.reme_index",
    "langmem.short_term",
    "remote_dev.mcp.server",
)


def build_probe_script(transcript_adapter):
    """Return a side-effect-free probe for one configured interpreter."""
    adapter = os.fspath(transcript_adapter)
    return f'''import importlib.metadata, importlib.util, inspect, math
missing = []
missing_packages = [name for name in ("mindie_knowledge", "remote_dev")
                    if importlib.util.find_spec(name) is None]
if missing_packages:
    missing.append("missing packages: " + ", ".join(missing_packages))
else:
    try:
        from mindie_knowledge.loop.cli import ensure_service, prepare_service, load_transcript_adapter
        from mindie_knowledge.loop.service_context import validate as validate_service_context
        from mindie_knowledge.git_layout import validate_config_feed_layout
        from mindie_knowledge.loop.summary_scratch import (
            ENVIRONMENT_KEY as SUMMARY_SCRATCH_ENV, ScratchOwnershipError, create_profile,
        )
        from mindie_knowledge.loop.activation import Admission
        from mindie_knowledge.loop.lifecycle import retire_service, restore_service, inspect_retirement
        from mindie_knowledge.materials.summarizer import (
            SummaryLedger, MAX_PROMPT_BYTES, MAX_RESPONSE_BYTES, LANGMEM_VERSION,
            validate_identity, make_request, validate_outcome,
        )
        from mindie_knowledge.materials.reme_index import ReMeIndex
        from langmem.short_term import summarize_messages
        from mindie_knowledge.loop.process import bounded_run, spawn_service
        from mindie_knowledge.loop.engine import Engine
        from mindie_knowledge.loop.store import Store
        from mindie_knowledge.state_layout import state_root, RELEASE_VERSION
        from mindie_knowledge.publication_contract import read_git_contract, parse_contract
        from mindie_knowledge.loop.transcript_redaction import install_scanner
        from mindie_knowledge.loop.history_import import import_transcript
        from mindie_knowledge.loop.transport import Service
        from mindie_knowledge.loop import documents, locks
        from mindie_knowledge.releases import (
            validate_compatibility, supports_harness, select_release,
        )
        from mindie_knowledge.releases_io import fetch_releases, verify_release_source
        from mindie_knowledge.community import submit_batch, reconcile_batch
        from remote_dev.mcp.tools import call_tool
    except Exception as exc:
        missing.append(f"pinned runtime import ({{type(exc).__name__}}: {{exc}})")
    else:
        if not callable(state_root):
            missing.append("knowledge state compatibility API is incomplete")
        if not callable(validate_config_feed_layout):
            missing.append("knowledge Git layout preflight is unavailable")
        if RELEASE_VERSION is not None and (
                not isinstance(RELEASE_VERSION, str) or RELEASE_VERSION.count(".") != 2):
            missing.append("knowledge release state declaration is invalid")
        if set(inspect.signature(Store.explain).parameters) != {{"self", "ref"}}:
            missing.append("knowledge explain is not the ref-only block API")
        if "continuation" not in inspect.signature(Store.query).parameters:
            missing.append("knowledge query lacks related-result continuation")
        if not callable(read_git_contract) or not callable(parse_contract):
            missing.append("publication contract API is incomplete")
        if not callable(load_transcript_adapter):
            missing.append("load_transcript_adapter is unavailable")
        if not callable(import_transcript):
            missing.append("explicit history import is unavailable")
        positive_bounds = (("MAX_PROMPT_BYTES", MAX_PROMPT_BYTES), ("MAX_RESPONSE_BYTES", MAX_RESPONSE_BYTES))
        for name, value in positive_bounds:
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                missing.append(name + " must be finite and positive")
        valid_bounds = all(type(value) in (int, float) and math.isfinite(value) and value > 0
                           for _, value in positive_bounds)
        timeout_default = inspect.signature(bounded_run).parameters["timeout"].default
        if timeout_default is not None and (
            type(timeout_default) not in (int, float)
            or not math.isfinite(timeout_default)
            or timeout_default <= 0
        ):
            missing.append("summary invocation has an invalid execution deadline")
        if (not callable(create_profile)
                or "prefix" not in inspect.signature(create_profile).parameters
                or not isinstance(SUMMARY_SCRATCH_ENV, str) or not SUMMARY_SCRATCH_ENV):
            missing.append("summary scratch profile API is unavailable")
        if (not isinstance(ScratchOwnershipError, type)
                or not issubclass(ScratchOwnershipError, ValueError)
                or getattr(ScratchOwnershipError, "mindie_category", None) != "configuration"):
            missing.append("summary scratch ownership failure contract is unavailable")
        if not callable(ReMeIndex) or not callable(summarize_messages):
            missing.append("material indexing dependencies are unavailable")
        if importlib.metadata.version("langmem") != LANGMEM_VERSION:
            missing.append("LangMem version differs from the reviewed runtime")
        ledger = ("prepare", "claim", "record", "recover", "complete", "retry", "usage_totals")
        if not all(callable(getattr(SummaryLedger, name, None)) for name in ledger):
            missing.append("summary outcome ledger API is incomplete")
        if not all(callable(item) for item in (validate_identity, make_request, validate_outcome)):
            missing.append("block index result and usage protocol is incomplete")
        if valid_bounds and not MAX_RESPONSE_BYTES <= MAX_PROMPT_BYTES:
            missing.append("summary protocol byte bounds are inconsistent")
        if not callable(getattr(locks, "lock_held", None)):
            missing.append("core locks lacks lock_held")
        if not all(callable(getattr(locks, name, None))
                   for name in ("try_lock_file", "unlock_file")):
            missing.append("shared operation admission locks are unavailable")
        if not all(callable(fn) for fn in (
                validate_compatibility, supports_harness, select_release,
                fetch_releases, verify_release_source)):
            missing.append("compatible Release selection APIs are unavailable")
        if callable(retire_service) and "control_timeout" not in inspect.signature(retire_service).parameters:
            missing.append("service retirement lacks a bounded control observation")
        if callable(prepare_service) and "probe_timeout" not in inspect.signature(prepare_service).parameters:
            missing.append("service preparation lacks a bounded readiness probe")
        if not callable(call_tool):
            missing.append("remote_dev call_tool is unavailable")
        if not callable(getattr(Engine, "stop_if_idle", None)):
            missing.append("Engine.stop_if_idle is unavailable")
        if "capture_mode" not in inspect.signature(Engine).parameters:
            missing.append("core lacks deterministic public transcript capture")
        if not callable(getattr(Service, "_stop_if_idle", None)):
            missing.append("Service._stop_if_idle is unavailable")
        if not all(callable(getattr(documents, name, None)) for name in ("render_entry", "parse_entry", "revision_of")):
            missing.append("knowledge document API is incomplete")
        if not callable(submit_batch) or not callable(reconcile_batch):
            missing.append("community receipt API is incomplete")
        if "path" not in inspect.signature(Admission.__init__).parameters:
            missing.append("Admission does not take an explicit admission path")
        methods = ("activate", "associate", "inspect", "check", "resolve", "claim", "finish", "deactivate", "capture_lease", "active_lease", "scope_root", "allows_hash", "leases")
        if not all(callable(getattr(Admission, name, None)) for name in methods):
            missing.append("Admission API is incomplete")
        if "admission" not in inspect.signature(Service).parameters:
            missing.append("Service does not accept admission")
        if not all(callable(fn) for fn in (retire_service, restore_service, inspect_retirement)):
            missing.append("exact-configuration service retirement is unavailable")
        if not all(callable(fn) for fn in (ensure_service, prepare_service)):
            missing.append("service lifecycle is unavailable")
        if not callable(validate_service_context):
            missing.append("service launch context validation is unavailable")
        elif validate_service_context({{}}) != {{"paths": {{}}, "environment": []}}:
            missing.append("service launch context validation is incompatible")
        try:
            module = load_transcript_adapter({{"transcript_adapter": {adapter!r}}})
            if module is None or not all(hasattr(module, name) for name in ("FileIdentity", "identify", "read_material")):
                missing.append("transcript adapter API is incomplete")
        except Exception as exc:
            missing.append(f"transcript adapter ({{type(exc).__name__}}: {{exc}})")

print("MISSING: " + "; ".join(missing) if missing else "OK", flush=True)
'''
