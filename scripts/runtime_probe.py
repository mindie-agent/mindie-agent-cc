"""Checks for the installed, locked adapter runtime.

[Role]
Builds a side-effect-free probe script to verify that `mindie_knowledge`,
`remote_dev`, and the Claude Code transcript adapter exports are importable in
a candidate Python interpreter.

[Invariants]
- Verifies `PROBE_MODULES` and transcript adapter callables (`FileIdentity`,
  `identify`, `read_material`).

[Timeouts & Locks]
- Pure string builder; execution timeout is enforced by the caller via
  `bounded.run`.

[Side-Effect Policy]
- Probe script performs imports and attribute checks only; writes no state.
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
    return f'''import importlib.util, math
missing = []
missing_packages = [name for name in ("mindie_knowledge", "remote_dev")
                    if importlib.util.find_spec(name) is None]
if missing_packages:
    missing.append("missing packages: " + ", ".join(missing_packages))
else:
    try:
        from mindie_knowledge.loop.cli import ensure_service, prepare_service, load_transcript_adapter
        from mindie_knowledge.loop.activation import Admission
        from mindie_knowledge.materials.summarizer import MAX_PROMPT_BYTES, MAX_RESPONSE_BYTES
        from mindie_knowledge.loop.process import bounded_run
        from mindie_knowledge.loop.engine import Engine
        from mindie_knowledge.loop.store import Store
        from mindie_knowledge.loop.transport import Service
        from remote_dev.mcp.tools import call_tool
    except Exception as exc:
        missing.append(f"pinned runtime import ({{type(exc).__name__}}: {{exc}})")
    else:
        for name, item in (("Store", Store), ("Service", Service), ("Admission", Admission),
                           ("Engine", Engine), ("bounded_run", bounded_run),
                           ("ensure_service", ensure_service), ("prepare_service", prepare_service),
                           ("load_transcript_adapter", load_transcript_adapter), ("call_tool", call_tool)):
            if not callable(item):
                missing.append(f"{{name}} is unavailable")
        for name, value in (("MAX_PROMPT_BYTES", MAX_PROMPT_BYTES), ("MAX_RESPONSE_BYTES", MAX_RESPONSE_BYTES)):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                missing.append(name + " must be finite and positive")
        try:
            module = load_transcript_adapter({{"transcript_adapter": {adapter!r}}})
            if module is None or not all(hasattr(module, name) for name in ("FileIdentity", "identify", "read_material")):
                missing.append("transcript adapter API is incomplete")
        except Exception as exc:
            missing.append(f"transcript adapter ({{type(exc).__name__}}: {{exc}})")

print("MISSING: " + "; ".join(missing) if missing else "OK", flush=True)
'''
