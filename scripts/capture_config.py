"""Install-time wiring for deterministic public transcript capture."""
import json
from pathlib import Path
import subprocess


def prepare(python, scripts):
    result = subprocess.run(
        [str(python), '-m', 'mindie_knowledge.loop.transcript_redaction'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    if result.returncode:
        raise RuntimeError('could not install the verified transcript redactor')
    path = result.stdout.decode('utf-8').strip()
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
    result = subprocess.run(
        [str(python), str(Path(scripts) / 'capture_config.py'), 'prepare-store'],
        input=json.dumps(config).encode('utf-8'),
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=90,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    output = result.stdout.decode('utf-8', 'replace')
    if result.returncode:
        raise store_failure(output, result.returncode)
    if json.loads(output) != {'status': 'ready'}:
        raise RuntimeError('knowledge store preparation did not confirm readiness')


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ['prepare-store']:
        try:
            from mindie_knowledge.loop.store import Store
            config = json.load(sys.stdin)
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
