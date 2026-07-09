from __future__ import annotations

import asyncio
import importlib

from homestead_memory import connect
from homestead_memory.adapters import tools
from homestead_memory.core import index


class StubMemory:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def remember(self, *args, **kwargs):
        self.calls.append(("remember", args, kwargs))
        return {"ok": True, "args": args, "kwargs": kwargs}

    def ask(self, *args, **kwargs):
        self.calls.append(("ask", args, kwargs))
        return {
            "hits": [{"rel": "distilled/test.md", "score": 1.0, "snippet": "remembered context"}],
            "context": "remembered context",
        }

    def search(self, *args, **kwargs):
        self.calls.append(("search", args, kwargs))
        return [{"rel": "distilled/test.md", "score": 1.0, "snippet": "remembered context"}]

    def verify(self, *args, **kwargs):
        self.calls.append(("verify", args, kwargs))
        return {"ok": True, "score": 100}


def test_universal_tools_round_trip_real_tmp_vault(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "_QMD", None)
    monkeypatch.delenv("HSM_READER", raising=False)
    monkeypatch.delenv("FBT_READER", raising=False)
    memory = connect(tmp_path, agent="adapter-test")

    remember = tools.remember_tool(memory)
    recall = tools.recall_tool(memory)
    verify = tools.verify_tool(memory)

    wrote = remember("user", "favorite_snack", "pineapple", source="unit-test")
    assert wrote["action"] == "recorded"

    answer = recall("what snack does the user like?", k=5)
    assert any(hit["rel"] == "distilled/user.md" for hit in answer["hits"])
    assert "pineapple" in answer["context"]

    verified = verify()
    assert verified["ok"] is True
    assert isinstance(verified["score"], int)

    specs = tools.tool_specs(memory)
    assert [spec["name"] for spec in specs] == ["remember", "recall", "verify"]
    assert specs[1]["parameters"]["properties"]["ask"]["type"] == "string"


def test_framework_adapter_modules_import_without_frameworks_installed():
    modules = {
        "homestead_memory.adapters.langgraph_store": ("HomesteadStore", ["put", "get", "search", "delete", "list_namespaces", "aput", "aget", "asearch", "adelete", "alist_namespaces"]),
        "homestead_memory.adapters.crewai_memory": ("HomesteadCrewAIStorage", ["save", "search", "reset", "verify"]),
        "homestead_memory.adapters.autogen_memory": ("HomesteadAutoGenMemory", ["add", "query", "update_context", "clear", "close"]),
        "homestead_memory.adapters.openai_agents": ("HomesteadSession", ["get_items", "add_items", "pop_item", "clear_session", "tools"]),
    }

    for module_name, (class_name, methods) in modules.items():
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
        for method in methods:
            assert hasattr(cls, method)
        assert cls.__doc__


def test_langgraph_store_delegates_to_memory():
    from homestead_memory.adapters.langgraph_store import HomesteadStore, StoreItem

    memory = StubMemory()
    store = HomesteadStore(memory)

    store.put(("user-1", "memories"), "food", {"memory": "likes pizza"})
    assert memory.calls[0][0] == "remember"
    assert memory.calls[0][1][:3] == (
        "langgraph:user-1/memories",
        "food",
        '{"memory":"likes pizza"}',
    )

    item = store.get(("user-1", "memories"), "food")
    assert isinstance(item, StoreItem)
    assert item.value == {"memory": "likes pizza"}

    store.get(("user-1", "memories"), "unknown")
    assert any(call[0] == "ask" for call in memory.calls)

    hits = store.search(("user-1",), query="pizza", limit=2)
    assert hits
    assert any(call[0] == "search" for call in memory.calls)

    store.delete(("user-1", "memories"), "food")
    assert memory.calls[-1][0] == "remember"
    assert memory.calls[-1][1][2] == "[deleted]"


def test_other_framework_adapters_delegate_to_memory():
    from homestead_memory.adapters.autogen_memory import HomesteadAutoGenMemory
    from homestead_memory.adapters.crewai_memory import HomesteadCrewAIStorage
    from homestead_memory.adapters.openai_agents import HomesteadSession, function_tools

    memory = StubMemory()

    crew = HomesteadCrewAIStorage(memory)
    crew.save("found a thing", metadata={"task": "research"})
    crew.search("thing")
    crew.verify()
    assert [call[0] for call in memory.calls[:3]] == ["remember", "search", "verify"]

    auto = HomesteadAutoGenMemory(memory)
    asyncio.run(auto.add("remember this"))
    asyncio.run(auto.query("what is remembered?"))
    assert memory.calls[-2][0] == "remember"
    assert memory.calls[-1][0] == "search"

    session = HomesteadSession(memory, session_id="s1")
    asyncio.run(session.add_items([{"role": "user", "content": "hi"}]))
    items = asyncio.run(session.get_items())
    popped = asyncio.run(session.pop_item())
    asyncio.run(session.clear_session())
    assert items == [{"role": "user", "content": "hi"}]
    assert popped == {"role": "user", "content": "hi"}
    assert any(call[0] == "ask" for call in memory.calls)

    bundle = function_tools(memory)
    assert sorted(bundle["callables"]) == ["recall", "remember", "verify"]
    assert [spec["name"] for spec in bundle["specs"]] == ["remember", "recall", "verify"]
