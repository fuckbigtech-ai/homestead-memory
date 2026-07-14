from __future__ import annotations

import importlib
from types import SimpleNamespace

from homestead_memory import Memory
from homestead_memory.core import index


def test_module_imports_without_litellm_installed():
    module = importlib.import_module("homestead_memory.adapters.litellm_memory")
    assert hasattr(module, "inject_memory")
    assert hasattr(module, "MemoryLogger")


def test_inject_memory_is_pure(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "_QMD", None)
    monkeypatch.delenv("HSM_READER", raising=False)
    monkeypatch.delenv("FBT_READER", raising=False)

    from homestead_memory.adapters.litellm_memory import inject_memory

    memory = Memory(tmp_path, agent="seed")
    memory.remember("User", "project", "orchard-ledger")
    memory.ingest()
    original = [{"role": "user", "content": "what project?"}]

    injected = inject_memory(original, memory)

    assert injected is not original
    assert original == [{"role": "user", "content": "what project?"}]
    assert injected[0]["role"] == "system"
    assert "orchard-ledger" in injected[0]["content"]


def test_memory_logger_duck_type_records_success(tmp_path):
    from homestead_memory.adapters.litellm_memory import MemoryLogger

    memory = Memory(tmp_path, agent="seed")
    logger = MemoryLogger(memory, agent_name="assistant")
    response = SimpleNamespace(model="served-model")

    logger.log_success_event({"model": "requested-model"}, response, None, None)

    rows = memory.history("litellm")
    assert rows
    assert rows[0]["agent"] == "assistant@served-model"
