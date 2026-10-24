"""[Role] Prepare independent component releases for Claude's existing binding.
[Invariants] A newer plugin inherits installed component versions. Published
adapter source stays unchanged; the effective lock belongs to a new runtime.
[Timeouts & Locks] Builder 420s plus candidate validation 180s; activation uses
the existing exclusive operation lock and 30s idle handoff.
[Side-Effect Policy] Software metadata/builds and native binding publication;
no knowledge activation, feed maintenance, model or consent changes.
"""
from __future__ import annotations
import json
from pathlib import Path
import sys

from product_contract import identity, package_files


def prepare(source, python, root, *, current_python=None, force=False, current_source=None):
    from mindie_knowledge.component_runtime import build, discover
    from bootstrap import readback
    baseline = identity(source)
    selected = discover(current_python or python, baseline["runtime"], force=force)
    if not selected["changed"] and current_source == baseline["source_sha256"]:
        return None
    pins = {name: row["revision"] for name, row in selected["runtime"].items()}
    if pins == baseline["runtime"]:
        return None
    artifact = build(source, root, baseline, selected["runtime"], python=python,
                     adapter_distribution="mindie-agent-cc",
                     files=[str(path.relative_to(source)) for path in package_files(source)])
    scripts = readback(Path(artifact["python"]), Path(artifact["source"]))
    return dict(artifact, scripts=str(scripts))


def check(binding, source, *, force=False):
    from bounded import run
    artifact = prepare(source, binding["python"], Path(binding["state_dir"]) / "component-runtimes", force=force,
                       current_source=binding["product_validation"]["source_sha256"])
    if artifact is None:
        return dict(status="up_to_date")
    output = run([artifact["python"], str(Path(artifact["scripts"]) / "setup.py"),
                  "--source", artifact["source"], "--config", binding["base_config"]], timeout=360)
    prepared = json.loads(output)
    result = run([artifact["python"], str(Path(artifact["scripts"]) / "setup.py"),
                  "--activate", prepared["config"]], timeout=40)
    return dict(json.loads(result), runtime=artifact["components"])


if __name__ == "__main__":
    print(json.dumps(prepare(*sys.argv[1:4], current_python=sys.argv[4]), sort_keys=True))
