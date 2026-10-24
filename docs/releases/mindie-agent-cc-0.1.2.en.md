# mindie-agent-cc 0.1.2 Release Notes

[中文](https://github.com/mindie-agent/mindie-agent-cc/blob/v0.1.2/docs/releases/mindie-agent-cc-0.1.2.md) | **English**

`mindie-agent-cc 0.1.2` is a maintenance release for Claude Code (paired with `mindie-agent 0.1.1`, while preserving the existing immutable `0.1.1` plugin tag). It adds independent formal GitHub Release auto-updates for shared runtime components and refines cold activation and per-session manual `/mindie-agent` knowledge boundaries.

## Highlights

- **Independent Shared Component Auto-Update & Installed Floor Inheritance**:
  - When no newer plugin Release is selected, background update checks discover formal GitHub Releases directly from official component repositories, taking the non-downgrading maximum across the currently installed version, the plugin baseline, and the latest formal component Release, and building a side-by-side isolated `uv` runtime with a verified `uv.lock`.
  - When upgrading to a newer plugin Release, runtime setup inherits the installed component version floor so a plugin upgrade never downgrades a component that was already updated independently.
- **Model-Free Candidate Validation & Cold Atomic Cutover**:
  - Removed preparation-time knowledge worker startup; candidate runtimes are verified through isolated package inventory checks and a model-free runtime API probe, followed by idle service retirement and cold atomic publication of the installation binding file (`~/.config/mindie-agent/cc.json` by default). The knowledge worker starts only on a subsequent authorized manual knowledge call.
- **Exploration Payload Pass-Through & Explicit Session Gate**:
  - Exploration outputs from `mindie-knowledge` pass straight through without host-side rewriting. Background updates never grant session knowledge authority or modify saved user consent; knowledge features remain active only after manually invoking `/mindie-agent` in the current session.

## Runtime Components & Verification Scope

- **Paired Runtime Components**: Targets the formal releases of `mindie-knowledge 0.13.3`, `remote-dev 0.10.6` (`0463a13fce9618ec9a51904869d61acb0cdb8538`), and `mindie-diagnostics 0.5.2` (`801a01b2789aed7f58e971a909205a7d40b61077`). Exact commit SHAs and supported Claude Code versions are recorded in `uv.lock`, `product-contract.json`, and `harness-compatibility.json`.
- **Verification & Acceptance Boundary**: Passed the new component-update and candidate-validation suite (`13 passed, 31 subtests`) and the existing release-update suite (`62 passed`). Cross-host end-to-end native new-session acceptance after formal package publication is tracked with the main product release gate.
