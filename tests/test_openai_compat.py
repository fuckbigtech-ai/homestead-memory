from __future__ import annotations

from types import SimpleNamespace

from homestead_memory import Memory
from homestead_memory.adapters.openai_compat import MemoryChat
from homestead_memory.core import index


class StubCompletions:
    def __init__(self) -> None:
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        content = f"reply from {kw['model']}"
        return SimpleNamespace(
            model=kw["model"],
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        )


class StubClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=StubCompletions())


def _remember_reply(response, memory, agent):
    memory.remember(
        "Conversation",
        "assistant_reply",
        response.choices[0].message.content,
        source="OpenAI-compatible chat",
        agent=agent,
    )


def test_injected_system_message_contains_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "_QMD", None)
    monkeypatch.delenv("HSM_READER", raising=False)
    monkeypatch.delenv("FBT_READER", raising=False)

    memory = Memory(tmp_path, agent="seed")
    memory.remember("User", "project", "orchard-ledger")
    memory.ingest()
    client = StubClient()
    chat = MemoryChat(client, memory)

    response = chat.create("model-a", [{"role": "user", "content": "what project?"}])

    assert response.model == "model-a"
    sent = client.chat.completions.calls[0]["messages"]
    assert sent[0]["role"] == "system"
    assert "orchard-ledger" in sent[0]["content"]


def test_swapping_model_stamps_provenance_in_same_vault(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "_QMD", None)
    monkeypatch.delenv("HSM_READER", raising=False)
    monkeypatch.delenv("FBT_READER", raising=False)

    memory = Memory(tmp_path, agent="seed")
    memory.remember("User", "project", "orchard-ledger")
    memory.ingest()
    chat = MemoryChat(StubClient(), memory, remember_fn=_remember_reply)

    chat.create("model-a", [{"role": "user", "content": "what project?"}])
    chat.create("model-b", [{"role": "user", "content": "what project?"}])

    agents = {row["agent"] for row in memory.history("conversation")}
    assert "assistant@model-a" in agents
    assert "assistant@model-b" in agents


def test_provenance_uses_served_model_not_requested(tmp_path, monkeypatch):
    # A router may serve a different model than requested; provenance must record
    # the SERVED model (response.model), not the requested alias.
    monkeypatch.setattr(index, "_QMD", None)
    monkeypatch.delenv("HSM_READER", raising=False)
    monkeypatch.delenv("FBT_READER", raising=False)

    class ReroutingCompletions:
        def create(self, **kw):
            return SimpleNamespace(
                model="glm-4.7-served",
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            )

    class ReroutingClient:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(completions=ReroutingCompletions())

    memory = Memory(tmp_path, agent="seed")
    chat = MemoryChat(ReroutingClient(), memory, inject=False, remember_fn=_remember_reply)
    chat.create("claude-sonnet-4.7", [{"role": "user", "content": "hi"}])

    agents = {row["agent"] for row in memory.history("conversation")}
    assert "assistant@glm-4.7-served" in agents
    assert "assistant@claude-sonnet-4.7" not in agents


def test_context_injection_is_budget_clamped(tmp_path, monkeypatch):
    # A huge memory answer must not produce an unbounded system message.
    monkeypatch.setattr(index, "_QMD", None)

    class BigMemory:
        def ask(self, query, budget=2000):
            return {"context": "x" * 100_000}

    client = StubClient()
    chat = MemoryChat(client, BigMemory(), budget=100)
    chat.create("model-a", [{"role": "user", "content": "q"}])
    sent = client.chat.completions.calls[0]["messages"]
    # budget=100 tokens -> ~400 chars ceiling + the small header/marker.
    assert len(sent[0]["content"]) < 600


def test_inject_false_leaves_messages_alone(tmp_path):
    memory = Memory(tmp_path, agent="seed")
    client = StubClient()
    chat = MemoryChat(client, memory, inject=False)
    messages = [{"role": "user", "content": "hello"}]

    chat.create("model-a", messages)

    sent = client.chat.completions.calls[0]["messages"]
    assert sent == messages
    assert sent is not messages
