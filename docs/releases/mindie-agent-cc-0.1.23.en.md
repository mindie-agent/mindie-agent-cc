# mindie-agent-cc 0.1.23

[中文](mindie-agent-cc-0.1.23.md)

- **Windows `SessionStart` synchronous Job ownership (`session_start.py`, `bootstrap.py`, `bounded.py`)**: Replaces raw `os.execve` re-exec calls in `session_start.py` and `bootstrap.py` with `bounded.exec_or_wait` so Windows `SessionStart` hooks hold Job Object ownership with `CREATE_NO_WINDOW` until `uv --python 3.13` finishes instead of exiting the parent process immediately under Windows CRT `execve`.
- **WSL2 host binary isolation and typed transport error parity (`paths.py`, `component_update.py`, `release_update.py`)**: Adds `which_host_binary` to prevent WSL2 `/mnt/<drive>` and PE `MZ` shims from shadowing Linux `claude`, `uv`, `gh`, and `mindie-*` CLIs, and classifies mid-stream `ssl.SSLError`, `SSLCertVerificationError`, `http.client` stream drops, and DNS errors as non-reportable transport failures while keeping source-integrity mismatches and local HTTP state bugs reportable.
