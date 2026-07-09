"""OpenAI Agents SDK adapters.

``HomesteadSession`` targets the OpenAI Agents SDK Session protocol
(openai-agents >=0.0.1): async ``get_items``, ``add_items``, ``pop_item``, and
``clear_session``. ``function_tools`` exposes the universal homestead-memory
function-tool definitions for Agents that prefer explicit tool calls.
"""
from __future__ import annotations

import json
from typing import Any

from .tools import recall_tool, remember_tool, tool_specs, verify_tool


class HomesteadSession:
    """OpenAI Agents SDK Session-compatible facade backed by Memory."""

    session_settings = None

    def __init__(self, memory: Any, session_id: str = "homestead-memory") -> None:
        self.memory = memory
        self.session_id = session_id
        self._items: list[Any] = []

    async def get_items(self, limit: int | None = None) -> list[Any]:
        self.memory.ask(f"openai_agents session {self.session_id}", k=limit or 20)
        return list(self._items if limit is None else self._items[-limit:])

    async def add_items(self, items: list[Any]) -> None:
        for index, item in enumerate(items):
            self._items.append(item)
            self.memory.remember(
                f"openai_agents:{self.session_id}",
                f"item_{len(self._items)}",
                _stringify(item),
                source="OpenAI Agents Session",
            )

    async def pop_item(self) -> Any | None:
        if not self._items:
            return None
        item = self._items.pop()
        self.memory.remember(
            f"openai_agents:{self.session_id}",
            "pop_marker",
            _stringify(item),
            source="OpenAI Agents Session",
        )
        return item

    async def clear_session(self) -> None:
        self._items.clear()
        self.memory.remember(
            f"openai_agents:{self.session_id}",
            "clear_marker",
            "clear requested",
            source="OpenAI Agents Session",
        )

    def tools(self) -> dict[str, Any]:
        return function_tools(self.memory)


def function_tools(memory: Any) -> dict[str, Any]:
    """Return callable tools plus JSON-schema specs for OpenAI Agents runners."""
    return {
        "callables": {
            "remember": remember_tool(memory),
            "recall": recall_tool(memory),
            "verify": verify_tool(memory),
        },
        "specs": tool_specs(memory),
    }


def _stringify(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        return str(value)
