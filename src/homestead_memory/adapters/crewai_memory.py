"""CrewAI memory adapter.

``HomesteadCrewAIStorage`` targets CrewAI Storage/memory (>=0.70), whose
storage-style integrations expose ``save``, ``search``, and ``reset`` methods.
Framework imports are optional and intentionally deferred.
"""
from __future__ import annotations

import json
from typing import Any


class HomesteadCrewAIStorage:
    """CrewAI-compatible storage facade backed by ``homestead_memory.Memory``."""

    def __init__(self, memory: Any, entity: str = "crewai") -> None:
        self.memory = memory
        self.entity = entity

    def save(
        self,
        value: Any,
        metadata: dict[str, Any] | None = None,
        agent: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload = _stringify({"value": value, "metadata": metadata or {}, "extra": kwargs})
        field = _field_from_metadata(metadata, agent)
        return self.memory.remember(self.entity, field, payload, source="CrewAI Storage")

    def search(
        self,
        query: str,
        limit: int = 5,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        del score_threshold, kwargs
        return self.memory.search(query, k=limit)

    def reset(self) -> dict[str, Any]:
        return self.memory.remember(self.entity, "reset_marker", "reset requested", source="CrewAI Storage")

    def verify(self, deep: bool = False) -> dict[str, Any]:
        return self.memory.verify(deep=deep)


def _field_from_metadata(metadata: dict[str, Any] | None, agent: str | None) -> str:
    if metadata:
        for key in ("id", "key", "task", "type"):
            if metadata.get(key):
                return str(metadata[key])
    if agent:
        return f"{agent}_memory"
    return "memory"


def _stringify(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        return str(value)
