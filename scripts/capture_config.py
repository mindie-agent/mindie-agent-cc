"""Install-time wiring for deterministic public transcript capture.

[Role]
Resolves the installed `mindie_knowledge.loop.transcript_redaction` executable
and constructs the capture configuration (`organizer.py` summary worker) during
setup.

[Invariants]
- Requires an absolute regular-file redactor executable path.
- Formats `prepare-store` failures with explicit `error_type` and `error` details.

[Timeouts & Locks]
- Uses `bounded.run` for subprocess queries during setup.

[Side-Effect Policy]
- Prepares capture configuration dictionaries and triggers `prepare-store` in
  the shared knowledge core.
"""
import json
from pathlib import Path


def prepare(python, scripts):
    import bounded
    output = bounded.run(
        [str(python), '-m', 'mindie_knowledge.loop.transcript_redaction'],
    )
    path = output.strip()
    if not Path(path).is_absolute() or not Path(path).is_file():
        raise RuntimeError('installed transcript redactor is missing')
    return dict(capture_mode='public-transcript', redactor_executable=path,
                summary_command=[str(python), str(Path(scripts) / 'organizer.py')])


def store_failure(output, code):
    try:
        result = json.loads(output)
        if (result['status'] == 'failed' and result['stage'] == 'prepare-store'
                and isinstance(result['error_type'], str) and isinstance(result['error'], str)):
            return RuntimeError(f"knowledge store preparation failed ({result['error_type']}): {result['error'][:240]}; not retried")
    except (ValueError, KeyError, TypeError):
        pass
    return RuntimeError(f'knowledge store preparation failed with exit {code}; failure receipt unavailable; not retried')


def prepare_store(python, scripts, config):
    """Initialize through the selected core before publishing an installation.

    The bounded local receipt carries the core cause; stderr is never
    forwarded and a failed initialization is never reported as ready.
    """
    import bounded
    try:
        output = bounded.run(
            [str(python), str(Path(scripts) / 'capture_config.py'), 'prepare-store'],
            json.dumps(config),
        )
    except Exception as exc:
        if hasattr(exc, 'returncode'):
            raise store_failure(getattr(exc, 'mindie_output', ''), exc.returncode) from exc
        raise
    if json.loads(output) != {'status': 'ready'}:
        raise RuntimeError('knowledge store preparation did not confirm readiness')


def prepare_domains(python, adapter, engine, **options):
    """Use the selected core's one catalog and policy-preserving preparation."""
    import bounded
    output = bounded.run([str(python), '-m', 'mindie_knowledge.loop.domain_catalog'],
                         json.dumps(dict(adapter=adapter, engine=engine, **options)),
                         timeout=90, max_output=65536)
    return json.loads(output)['domain_configs']


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ['prepare-store']:
        try:
            from mindie_knowledge.loop.store import Store
            from mindie_knowledge.git_layout import validate_config_feed_layout
            config = json.load(sys.stdin)
            validate_config_feed_layout(config)
            store = Store(config['root'], config['domain'])
            store.close()
        except Exception as exc:
            # A bounded local receipt preserves the core cause without
            # forwarding raw stderr or treating a failed initialization as ready.
            print(json.dumps(dict(status='failed', stage='prepare-store',
                                  error_type=type(exc).__name__, error=str(exc)[:240])))
            raise SystemExit(1)
        print(json.dumps({'status': 'ready'}))
    elif sys.argv[1:]:
        raise ValueError('unsupported capture preparation operation')
    else:
        print(json.dumps(prepare(sys.executable, Path(__file__).resolve().parent)))
