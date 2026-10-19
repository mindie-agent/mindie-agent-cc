# Framework Stability & Testing Contracts

<a href="framework-stability.zh.md">中文</a> | <b>English</b> · <a href="README.en.md">Documentation Index</a>

This document describes the everyday usage invariants, cross-platform atomic configuration I/O, and reproducible test contracts for `mindie-agent-cc`.

---

## 1. Everyday Usage & Persistent User Preferences

- **Set Once, Persist Everywhere**: Community knowledge sharing and automatic tool fault reporting share a single unified contribution switch in `<profile>/.mindie/mindie-consent.json`. Your unified contribution choice and approved project scope persist across sessions, `--fork-session` branches, restarts, plugin updates, and transient failures. Legacy knowledge-only `contribute` records lacking the unified consent revision (`choice_revision`) do not automatically gain fault-upload permission on upgrade—they surface `needs_choice` until explicitly re-confirmed once.
- **Zero Overhead When Sharing Is Off**: When community sharing is disabled, the `Stop` hook performs no transcript collection or organizer model calls, while domain knowledge search (`mindie-knowledge`) and remote Ascend execution (`remote-dev`) remain fully available.
- **Explicit Fault Visibility**: Transient network or service issues are recovered using persisted worker state, whereas a corrupted configuration file is reported explicitly as a diagnostic error and never triggers a fresh onboarding prompt.

---

## 2. Atomic Configuration Reads & Writes on Windows

- **Atomic Publication**: The shared store writes a complete temporary JSON document and publishes it with `os.replace`. Readers use ordinary file reads; the adapter has no separate Win32 replacement implementation.
- **Visible Contention**: If Windows refuses replacement because another reader holds the file, the write fails visibly and the saved document remains intact. The adapter does not reset consent or retry indefinitely.

---

## 3. Reproducible Component Testing & Benchmarks

Component and contract tests use committed parser fixtures and exact Git commits pinned in [`uv.lock`](../uv.lock). They never probe or mutate your production installation.

### Running the Full Test Suite
From the repository root, prepare a local checkout of `mindie-agent/knowledge` at the exact commit pinned in `uv.lock`, then run:

```sh
uv sync --locked --no-editable --group test
MINDIE_TEST_KNOWLEDGE_CHECKOUT=/absolute/path/to/mindie-knowledge-pin \
uv run --locked --no-editable --group test python -m pytest tests -m "not live_npu" -q
```

### How the Local Candidate Identity Test Works
- `MINDIE_TEST_KNOWLEDGE_CHECKOUT` must point to an absolute local Git checkout whose `HEAD` matches the `mindie-knowledge` commit in `uv.lock`.
- The test verifies that commit using local `git` only (without fetching from GitHub or scanning parent directories), creates an isolated virtual environment reusing the locked wheels, and performs a single offline `pip install --no-deps --no-build-isolation` from `git+file://` to verify `direct_url.json` provenance.
- **Per-Release CPU Microbenchmark**: Each `0.0.2` release runs [`benchmarks/release.json`](../benchmarks/release.json) ([`benchmarks/release.py`](../benchmarks/release.py)) to measure `public-transcript` extraction and sanitization latency in `us/op`, publishing `benchmark-results.json`, `benchmark-results.md`, `benchmark-SHA256SUMS`, and `SHA256SUMS` as a reproducible CPU baseline.

---

## 4. Integration & Release Ordering

- **Paired Release Order**: When updating shared runtime dependencies, publish component Releases first (`mindie-knowledge`, `remote-dev`, `mindie-diagnostics`) before updating and releasing the native plugin adapters (`mindie-agent-cc`, `mindie-agent-codex`, `mindie-agent-kimi`).
- **Clear Acceptance Boundaries**: Automated CI and CPU microbenchmarks verify locked dependencies, parser fixtures, and cross-platform filesystem contracts; live host acceptance and remote Ascend NPU verification are recorded separately.
