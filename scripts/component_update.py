"""[Role] Prepare independent component releases for Claude's existing binding.
[Invariants] A newer plugin inherits installed component versions. Published
adapter source stays unchanged; the effective lock belongs to a new runtime.
[Timeouts & Locks] Builder 420s plus candidate validation 180s; activation uses
the existing exclusive operation lock and 30s idle handoff.
[Side-Effect Policy] Software metadata/builds and native binding publication;
no knowledge activation, feed maintenance, model or consent changes.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

# Isolated Python omits the script directory; helpers belong to this candidate.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from product_contract import identity, package_files


def prepare(source, python, root, *, current_python=None, force=False, current_source=None,
            plugin_upgrade=False):
    from mindie_knowledge.component_bindings import managed_commands, required_components, rebind_commands
    from mindie_knowledge.component_runtime import build, discover
    from bootstrap import readback
    from paths import config_path
    config = config_path()
    adapter = json.loads(config.read_text()) if config.is_file() else {}
    if adapter.get("base_config"):
        adapter = json.loads(Path(adapter["base_config"]).read_text())
    if plugin_upgrade and not adapter.get("component_source") and not managed_commands(adapter):
        return None
    baseline = identity(source)
    selected = discover(current_python or python, baseline["runtime"], force=force,
                        required_components=required_components(adapter))
    needs_binding = bool(adapter.get("component_commands")) and (
        rebind_commands(adapter, python, selected["runtime"]) != adapter["component_commands"])
    if not selected["changed"] and current_source == baseline["source_sha256"] and not needs_binding:
        return None
    pins = {name: row["revision"] for name, row in selected["runtime"].items()}
    if pins == baseline["runtime"] and not needs_binding:
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("python")
    parser.add_argument("root", type=Path)
    parser.add_argument("current_python")
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.python, args.root,
                             current_python=args.current_python, plugin_upgrade=True), sort_keys=True))


if __name__ == "__main__":
    main()
