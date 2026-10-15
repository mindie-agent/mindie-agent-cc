"""Configured-source failures remain visible through the real local RPC."""
from contextlib import closing
import sys
import threading

from support import SCRIPTS
from mindie_knowledge.loop.engine import Engine
from mindie_knowledge.loop.feed import Feed
from mindie_knowledge.loop.store import Store
from mindie_knowledge.loop.transport import Service

sys.path.insert(0, str(SCRIPTS))
import mcp_server


def test_feed_unavailable_reaches_cc_without_empty_result_or_product_issue(tmp_path, monkeypatch):
    with closing(Store(tmp_path / "data", "test")) as store:
        feed = Feed(store, dict(repository="org/knowledge", domain="test"))
        store.feed_set("feed:" + feed.ident, dict(
            status="unavailable", detail="private source failure must stay local"))
        service = Service(Engine(store), feeds=[feed])
        monkeypatch.setattr(service, "_identify", lambda args, **_kw: (args, "native-task", None))
        monkeypatch.setattr(mcp_server, "claim_bind", lambda *_a: {"session": "native-task"})
        monkeypatch.setattr(mcp_server, "finish_bind", lambda *_a: None)
        monkeypatch.setattr("knowledge_service.ensure_service", lambda: service.connection)

        def unexpected(*_args, **_kwargs):
            raise AssertionError("known source failure must not query, sync, or report a defect")

        monkeypatch.setattr(store, "query", unexpected)
        monkeypatch.setattr(feed, "sync", unexpected)
        monkeypatch.setattr("mindie_knowledge.loop.transport.failure", unexpected)
        thread = threading.Thread(target=service.http.serve_forever)
        thread.start()
        try:
            response = mcp_server.handle("knowledge", {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "knowledge_query", "arguments": {"query": "synthetic"},
                           "_meta": {"claudecode/toolUseId": "fixture-source-failure"}},
            })
            result = response["result"]
            assert result["isError"] is True
            assert "configured feed has no verified installed snapshot" in result["content"][0]["text"]
            assert "org/knowledge" in result["content"][0]["text"]
            assert "private source failure" not in str(result)
            assert "agent_diagnostics" not in result
            assert "structuredContent" not in result
            assert service.engine.operations.snapshot()["activity"] == 0
        finally:
            service.http.shutdown()
            service.http.server_close()
            thread.join(timeout=5)
        assert not thread.is_alive()
