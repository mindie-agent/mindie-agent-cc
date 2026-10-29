"""Use the selected core runtime for the single settings write boundary.

[Role]
Delegates community configuration writes to `mindie_knowledge.loop.settings.CommunityWriteContext`
in the configured Python runtime.

[Invariants]
- Preserves existing file bytes untouched if validation or write fails.

[Timeouts & Locks]
- Enforces a 30-second subprocess timeout and core `CommunityWriteContext` lock.

[Side-Effect Policy]
- Mutates only the requested community configuration file through the core
  atomic write boundary.
"""
import json
import sys

import bounded

SCRIPT = """
import json, sys
from mindie_knowledge.loop.settings import CommunityWriteContext
request = json.load(sys.stdin)
try:
    with CommunityWriteContext(request['path']) as context:
        result = context.configure(request['path'], request['settings'])
except Exception as exc:
    print(json.dumps(dict(status='failed', error_type=type(exc).__name__,
                          error=str(exc)[:240])))
    raise SystemExit(1)
print(json.dumps(result.raw))
"""

def configure(path, settings, python=None):
    request = json.dumps(dict(path=str(path), settings=settings))
    try:
        output = bounded.run(
            [str(python or sys.executable), '-c', SCRIPT],
            input_text=request, timeout=30.0,
        )
    except TimeoutError:
        raise
    except (OSError, RuntimeError) as exc:
        detail = getattr(exc, 'mindie_output', '') or getattr(exc, 'mindie_stderr', '')
        detail = detail.strip()[:400] if isinstance(detail, str) else ''
        message = ('shared community configuration failed; nothing was written; '
                   'existing bytes preserved')
        if detail:
            message += '; ' + detail
        raise SystemExit(message) from exc
    return json.loads(output)
