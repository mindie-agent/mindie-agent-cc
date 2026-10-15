"""Reuse shared core config, transcript loader, and service startup."""

from __future__ import annotations

from pathlib import Path

from paths import engine_config_path


def newer_binding(first_path, second_path):
    """Compare two explicit compatible bindings, never discover a release."""
    from packaging.version import Version
    from mindie_knowledge.loop.cli import config_at
    from mindie_knowledge.loop.client_contract import require_compatible

    first_path, second_path = Path(first_path).resolve(), Path(second_path).resolve()
    if first_path == second_path:
        return first_path
    first, second = config_at(first_path), config_at(second_path)
    require_compatible(first, second)
    a, b = first["product_validation"], second["product_validation"]
    av, bv = Version(a["native_version"]), Version(b["native_version"])
    if av == bv and a["source_sha256"] != b["source_sha256"]:
        raise ValueError("one native release cannot bind different source bytes")
    return second_path if bv > av else first_path


def client_engine_path(adapter=None):
    """Follow the fixed anchor's receipts when a pending older entry is late."""
    import json
    from paths import load_adapter_config
    from mindie_knowledge.loop.cli import config_at
    from mindie_knowledge.loop.client_contract import resolve_config

    adapter = adapter if adapter is not None else load_adapter_config()
    own = engine_config_path(adapter)
    config = config_at(own)
    if config.get("client_contract") is None:
        return own
    base = Path(adapter["base_config"]).resolve()
    if config["client_contract"]["adapter_profile"] != str(base):
        raise ValueError("service binding names a different base profile")
    original = json.loads(base.read_text(encoding="utf-8"))
    own_target, _ = resolve_config(own)
    anchor_target, _ = resolve_config(engine_config_path(original))
    # The host selects the release, but only SessionStart advances its owner.
    # A prepared newer entry must not bypass a still-busy anchor on cold start.
    newer_binding(own_target, anchor_target)
    return anchor_target


def engine_config(engine_file=None):
    from mindie_knowledge.loop.cli import config_at

    path = str(Path(engine_file or client_engine_path()).resolve())
    return config_at(path)


def load_transcript(engine_file=None):
    from mindie_knowledge.loop.cli import load_transcript_adapter

    return load_transcript_adapter(engine_config(engine_file))


def ensure_service(engine_file=None):
    from mindie_knowledge.loop.cli import ensure_client_service as core_ensure

    path = str(Path(engine_file or client_engine_path()).resolve())
    return core_ensure(path)


def prepare_service(engine_file=None):
    """Request the shared service owner without claiming readiness."""
    from mindie_knowledge.loop.cli import prepare_client_service as core_prepare

    path = str(Path(engine_file or client_engine_path()).resolve())
    return core_prepare(path)
