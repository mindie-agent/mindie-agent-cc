"""Reuse shared core config, transcript loader, and service startup."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from paths import engine_config_path


def engine_config(engine_file=None):
    from mindie_knowledge.loop.cli import config_at

    path = str(Path(engine_file or engine_config_path()).resolve())
    return config_at(path)


def load_transcript(engine_file=None):
    from mindie_knowledge.loop.cli import load_transcript_adapter

    return load_transcript_adapter(engine_config(engine_file))


def existing_service(engine_file=None):
    """Probe a live connection. Never spawn."""
    from mindie_knowledge.loop.cli import connect, rpc

    config = engine_config(engine_file)
    connection = connect(config)
    rpc(connection, "status", timeout=0.4)
    return connection


def ensure_service(engine_file=None):
    from mindie_knowledge.loop.cli import ensure_service as core_ensure

    path = str(Path(engine_file or engine_config_path()).resolve())
    return core_ensure(path)


def prepare_service(engine_file=None):
    """Spawn ensure_service in its own process group. Do not wait.

    Used from explicit init/sharing-enable when contribution is on.
    Stop never starts the service. The child is not in the Hook tree.
    """
    path = str(Path(engine_file or engine_config_path()).resolve())
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    kwargs = dict(
        args=[sys.executable, str(Path(__file__).resolve()), path],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        close_fds=True,
    )
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        kwargs["creationflags"] = flags
    subprocess.Popen(**kwargs)
    return "starting"


if __name__ == "__main__":
    ensure_service(sys.argv[1] if len(sys.argv) > 1 else None)
