"""Small synthetic, model-free service ownership and index reuse experiment."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time


def run_probe(root, scripts, redactor):
    root, scripts = Path(root).resolve(), Path(scripts).resolve()
    root.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(scripts))
    import bounded
    from mindie_knowledge.loop.cli import connect, config_at
    from mindie_knowledge.loop.locks import lock_held
    from mindie_knowledge.loop.store import Store

    trace = root / "index-events.jsonl"
    # Observation only: the production methods and their return values remain
    # unchanged. Distinguish actual checkpoint loading from material rebuilding.
    instrumentation = """
from mindie_knowledge.materials.reme_index import ReMeIndex as _ObservedIndex
_observed_start = _ObservedIndex._start
_observed_apply = _ObservedIndex._apply_changes
_observed_refresh = _ObservedIndex._refresh
def _event(stage, **fields):
    with open(%r, 'a', encoding='utf-8') as stream:
        stream.write(json.dumps(dict(stage=stage, pid=os.getpid(), **fields)) + '\\n')
async def _index_start(self, records):
    _event('checkpoint_load', records=len(records), instance=id(self))
    return await _observed_start(self, records)
def _index_apply(self, *args, **kwargs):
    _event('material_rebuild', instance=id(self))
    return _observed_apply(self, *args, **kwargs)
def _index_refresh(self, *args, **kwargs):
    _event('refresh', instance=id(self))
    return _observed_refresh(self, *args, **kwargs)
_ObservedIndex._start = _index_start
_ObservedIndex._apply_changes = _index_apply
_ObservedIndex._refresh = _index_refresh
""" % str(trace)
    observed = root / "observed_transcript.py"
    observed.write_text((scripts / "transcript.py").read_text() + "\nimport os\n" + instrumentation, encoding="utf-8")
    engine = root / "engine.json"
    community = root / "community.json"
    community.write_text(json.dumps(dict(schema="mindie-community-config/1", enabled=False,
                                         repository=None, branch="main",
                                         project_roots=[])), encoding="utf-8")
    config = dict(root=str(root / "data"), domain="test", capture_mode="public-transcript",
                  redactor_executable=str(redactor), transcript_adapter=str(observed),
                  admission_path=str(root / "admission.sqlite3"),
                  community_config=str(community))
    engine.write_text(json.dumps(config), encoding="utf-8")
    adapter = root / "adapter.json"
    adapter.write_text(json.dumps(dict(python=sys.executable, engine_config=str(engine),
                                       community_config=str(community), state_dir=str(root / "adapter-state"))))
    store = Store(root / "data", "test")
    try:
        store.create_draft(kind="experience", title="Synthetic service ownership fixture",
                           summary="A dedicated local-only fixture.",
                           content="Synthetic ownership marker: quartz service reuse.")
    finally:
        store.close()
    helper = root / "query.py"
    helper.write_text(
        "import sys,json\nsys.path.insert(0, " + repr(str(scripts)) + ")\n"
        "from mcp_server import knowledge_call\n"
        "try:\n"
        "    print(json.dumps(knowledge_call('knowledge_query',"
        "{'query':'quartz service reuse'},'synthetic-mechanism-fixture')))\n"
        "except Exception:\n"
        "    import traceback\n    print(json.dumps({'fixture_error':traceback.format_exc()}))\n",
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(MINDIE_CC_CONFIG=str(adapter),
               MINDIE_DIAGNOSTICS_CONFIG=str(root / "diagnostics" / "off.json"),
               MINDIE_DIAGNOSTICS_ROOT=str(root / "diagnostics" / "state"))
    consumer = root / "data" / "test" / "consumer.lock"
    result = dict(kind="synthetic-model-free-mechanism", queries=[])
    try:
        for number in (1, 2):
            started = time.monotonic()
            output = bounded.run([sys.executable, str(helper)], env=env,
                                 timeout=30, allow_service=True)
            answer = json.loads(output)
            if "fixture_error" in answer:
                raise AssertionError(answer["fixture_error"])
            connection = connect(config_at(engine), config_path=engine)
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            result["queries"].append(dict(
                number=number, elapsed_seconds=round(time.monotonic() - started, 3),
                refs=[row["ref"] for row in answer["results"]],
                service_pid=events[-1]["pid"],
                consumer_held=lock_held(consumer),
                endpoint_identity=hashlib.sha256(json.dumps(connection, sort_keys=True).encode()).hexdigest(),
                index_events=events,
            ))
        output = bounded.run([sys.executable, str(scripts / "service_handoff.py"),
                              "stop", str(engine)], env=env, timeout=30)
        result["retirement"] = json.loads(output)
        result["consumer_held_after_retirement"] = lock_held(consumer)
        output = bounded.run([sys.executable, str(scripts / "service_handoff.py"),
                              "restore", str(engine), json.dumps(result["retirement"]["retirement"])],
                             env=env, timeout=30, allow_service=True)
        result["restoration"] = json.loads(output)
        assert result["queries"][0]["refs"], "the synthetic material must be read"
        assert result["queries"][0]["endpoint_identity"] == result["queries"][1]["endpoint_identity"]
        assert all(row["consumer_held"] is True for row in result["queries"])
        events = result["queries"][1]["index_events"]
        assert sum(item["stage"] == "checkpoint_load" for item in events) == 1
        assert sum(item["stage"] == "material_rebuild" for item in events) == 1
        assert sum(item["stage"] == "refresh" for item in events) == 2
        assert result["consumer_held_after_retirement"] is False
        assert result["retirement"]["retirement"]["status"] == "retired"
        assert result["restoration"]["status"] == "restored"
    finally:
        if consumer.exists() and lock_held(consumer) is True:
            bounded.run([sys.executable, str(scripts / "service_handoff.py"),
                         "stop", str(engine)], env=env, timeout=30)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--scripts", required=True)
    parser.add_argument("--redactor", required=True)
    args = parser.parse_args()
    receipt = run_probe(args.root, args.scripts, args.redactor)
    target = Path(args.root) / "result.json"
    with target.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2)
    print(json.dumps(receipt, indent=2))
