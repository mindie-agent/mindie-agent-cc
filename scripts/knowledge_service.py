"""Use the selected installation's exact shared service.

[Role]
Wraps `mindie_knowledge.loop.cli` (`config_at`, `load_transcript_adapter`,
`ensure_service`, `status`, `rpc`) using the installation's `engine_config_path`.
`select_engine` resolves an explicit public domain or reference URI to the one
existing engine configuration registered for that domain. `select_engine_for_refs`
extends the same routing to a batch `refs` read without admitting mixed
`mindie://<domain>/` authorities.

[Invariants]
- Resolves the explicit `engine_config_path` before invoking shared core service
  operations.
- Explicit routing reads only the trusted installation `domain_configs` mapping
  (`canonical_domain` to absolute existing engine config path) and never accepts
  a caller-supplied path or mapping.
- A no-domain/no-reference call, and a default-authority reference, keep the
  existing default call without importing the newer core selector; an unknown
  or non-default domain never falls back to the default configuration.
- A batch of references routes by its first reference and is rejected when its
  declared `mindie://` authorities disagree.
- Capability absence is reported only when the exact helper module is missing;
  internal helper import failures remain visible.

[Timeouts & Locks]
- Delegates RPC and startup timeouts to `mindie_knowledge.loop.cli`; explicit
  routing adds one bounded local JSON read performed by the shared selector.

[Side-Effect Policy]
- `status` is non-starting; `ensure_service` starts or connects to the local
  knowledge service. Routing itself is read-only and creates no configuration.
"""

from __future__ import annotations

from pathlib import Path
import re

from paths import engine_config_path, load_adapter_config, load_engine_config

_REFERENCE_AUTHORITY = re.compile(r"mindie://(?P<domain>[a-z][a-z0-9-]{0,63})/")
_MULTI_DOMAIN_UNAVAILABLE = (
    "multi-domain knowledge routing is unavailable in the installed knowledge runtime"
)


def _reference_authority(ref):
    match = _REFERENCE_AUTHORITY.match(ref) if isinstance(ref, str) else None
    return None if match is None else match.group("domain")


def _default_authority_reference(ref, adapter):
    """Whether this reference needs no newer routing helper at all.

    A malformed reference keeps the old forward-to-service behavior. A canonical
    default authority stays on the existing default call unless the trusted
    configuration explicitly registers that same domain to another engine.
    """
    authority = _reference_authority(ref)
    if authority is None:
        return True
    mappings = adapter.get("domain_configs")
    if mappings is not None and not isinstance(mappings, dict):
        return False
    if mappings and authority in mappings:
        return False
    return load_engine_config(adapter).get("domain") == authority


def select_engine(domain=None, ref=None, config=None):
    """Return ``(engine_config_path, routed_domain)`` for one explicit route.

    ``(None, None)`` means the legacy default service call: callers must use the
    existing no-argument ``ensure_service()`` path. An explicit route always
    returns a concrete path and its declared domain.
    """
    if domain is None and ref is None:
        return None, None
    adapter = config if config is not None else load_adapter_config()
    if domain is None and _default_authority_reference(ref, adapter):
        return None, None
    try:
        from mindie_knowledge.loop.domain_routes import select_engine_config
    except ModuleNotFoundError as exc:
        if exc.name == "mindie_knowledge.loop.domain_routes":
            raise ValueError(_MULTI_DOMAIN_UNAVAILABLE) from exc
        raise
    selected = select_engine_config(
        engine_config_path(adapter),
        domain=domain,
        ref=ref,
        domain_configs=adapter.get("domain_configs"),
    )
    selected = Path(selected).resolve()
    return selected, engine_config(str(selected))["domain"]


def reference_authority(ref):
    """Return the canonical ``mindie://<domain>/`` authority of a reference."""
    return _reference_authority(ref)


def select_engine_for_refs(refs=None, ref=None, config=None):
    """Resolve one engine for a single reference or a batch ``refs`` read.

    A single reference keeps the historical ``select_engine(ref=...)`` route.
    A batch uses its first reference as the route and rejects conflicting
    ``mindie://`` authorities so one response can never mix two services. An
    empty or absent batch without ``ref`` is a caller error, not a default read.
    """
    if ref is None:
        if not isinstance(refs, list) or not refs:
            raise ValueError("a reference or a nonempty refs array is required")
        if not all(isinstance(item, str) and item for item in refs):
            raise ValueError("refs must contain nonempty reference strings")
        authorities = set()
        for item in refs:
            authority = _reference_authority(item)
            if authority is not None:
                authorities.add(authority)
        if len(authorities) > 1:
            raise ValueError(
                "batched references conflict across mindie:// domain authorities"
            )
        ref = refs[0]
    return select_engine(ref=ref, config=config)


def engine_config(engine_file=None):
    from mindie_knowledge.loop.cli import config_at

    path = str(Path(engine_file or engine_config_path()).resolve())
    return config_at(path)


def load_transcript(engine_file=None):
    from mindie_knowledge.loop.cli import load_transcript_adapter

    return load_transcript_adapter(engine_config(engine_file))


def ensure_service(engine_file=None):
    from mindie_knowledge.loop.cli import ensure_service as core_ensure

    path = str(Path(engine_file or engine_config_path()).resolve())
    return core_ensure(path)


def prepare_service(engine_file=None):
    """Request the shared service owner without claiming readiness."""
    from mindie_knowledge.loop.cli import prepare_service as core_prepare

    path = str(Path(engine_file or engine_config_path()).resolve())
    return core_prepare(path)
