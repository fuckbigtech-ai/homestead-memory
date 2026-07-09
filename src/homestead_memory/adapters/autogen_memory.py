"""AutoGen memory adapter.

``HomesteadAutoGenMemory`` targets AutoGen ``autogen_core`` Memory (>=0.4):
async ``add``, ``query``, ``update_context``, ``clear``, and ``close``.
Framework types are imported lazily only when result objects are constructed.
"""
from __future__ import annotations

import json
from typing import Any


class HomesteadAutoGenMemory:
    """AutoGen Memory protocol facade backed by ``homestead_memory.Memory``."""

    component_type = "memory"

    def __init__(self, memory: Any, name: str = "homestead_memory") -> None:
        self.memory = memory
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def add(self, content: Any, cancellation_token: Any | None = None) -> None:
        del cancellation_token
        text = _content_text(content)
        metadata = getattr(content, "metadata", None) or {}
        field = str(metadata.get("id") or metadata.get("key") or "memory")
        self.memory.remember("autogen", field, text, source="autogen_core Memory")

    async def query(
        self,
        query: str | Any = "",
        cancellation_token: Any | None = None,
        **kwargs: Any,
    ) -> Any:
        del cancellation_token
        question = _content_text(query) if not isinstance(query, str) else query
        hits = self.memory.search(question, k=int(kwargs.get("k", kwargs.get("limit", 5))))
        return _autogen_query_result(hits)

    async def update_context(self, model_context: Any) -> Any:
        result = await self.query("")
        text = _result_text(result)
        if text and hasattr(model_context, "add_message"):
            await _maybe_await(model_context.add_message(_autogen_system_message(text)))
        return _autogen_update_result(result)

    async def clear(self) -> None:
        self.memory.remember("autogen", "clear_marker", "clear requested", source="autogen_core Memory")

    async def close(self) -> None:
        return None


def _content_text(content: Any) -> str:
    raw = getattr(content, "content", content)
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    if isinstance(raw, (dict, list)):
        return json.dumps(raw, sort_keys=True, default=str)
    return str(raw)


def _autogen_query_result(hits: list[dict[str, Any]]) -> Any:
    try:  # pragma: no cover - framework optional in this repo.
        from autogen_core.memory import MemoryContent, MemoryMimeType, MemoryQueryResult

        results = [
            MemoryContent(
                content=hit.get("snippet", "") or hit.get("rel", ""),
                mime_type=MemoryMimeType.TEXT,
                metadata={"rel": hit.get("rel"), "score": hit.get("score")},
            )
            for hit in hits
        ]
        return MemoryQueryResult(results=results)
    except Exception:
        return {"results": hits}


def _autogen_update_result(memories: Any) -> Any:
    try:  # pragma: no cover - framework optional in this repo.
        from autogen_core.memory import UpdateContextResult

        return UpdateContextResult(memories=memories)
    except Exception:
        return {"memories": memories}


def _autogen_system_message(text: str) -> Any:
    try:  # pragma: no cover - framework optional in this repo.
        from autogen_core.models import SystemMessage

        return SystemMessage(content=text)
    except Exception:
        return {"role": "system", "content": text}


def _result_text(result: Any) -> str:
    results = getattr(result, "results", None)
    if results is None and isinstance(result, dict):
        results = result.get("results", [])
    parts = []
    for item in results or []:
        if isinstance(item, dict):
            parts.append(str(item.get("snippet") or item.get("content") or item.get("rel") or ""))
        else:
            parts.append(_content_text(item))
    return "\n".join(part for part in parts if part)


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value
