"""Universal callable tools backed by :class:`homestead_memory.Memory`.

These helpers do not depend on any agent framework. Register the returned
callables directly, or wrap the specs from :func:`tool_specs` in the function
tool shape expected by your runner.
"""
from __future__ import annotations

from typing import Any, Callable


def remember_tool(memory: Any) -> Callable[..., dict[str, Any]]:
    """Return a plain callable that records one distilled fact."""

    def remember(
        entity: str,
        field: str,
        value: str,
        source: str | None = None,
    ) -> dict[str, Any]:
        return memory.remember(entity, field, value, source=source)

    remember.__name__ = "remember"
    remember.__doc__ = "Remember one durable fact: entity, field, value, optional source."
    return remember


def recall_tool(memory: Any) -> Callable[..., dict[str, Any]]:
    """Return a plain callable that asks the memory layer for context."""

    def recall(
        ask: str,
        k: int | None = 5,
        question_type: str | None = None,
        budget: int = 6000,
    ) -> dict[str, Any]:
        return memory.ask(ask, k=k, question_type=question_type, budget=budget)

    recall.__name__ = "recall"
    recall.__doc__ = "Recall relevant memory for a question."
    return recall


def verify_tool(memory: Any) -> Callable[..., dict[str, Any]]:
    """Return a plain callable that runs the vault integrity gate."""

    def verify(deep: bool = False) -> dict[str, Any]:
        return memory.verify(deep=deep)

    verify.__name__ = "verify"
    verify.__doc__ = "Verify memory integrity and return the RotBench score."
    return verify


def tool_specs(memory: Any) -> list[dict[str, Any]]:
    """Return framework-neutral JSON-schema tool definitions.

    The ``memory`` parameter keeps the signature parallel with the callable
    factories and lets callers build specs from the same integration point. It
    is intentionally not inspected.
    """
    del memory
    return [
        {
            "name": "remember",
            "description": "Store one durable, auditable memory fact.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity": {
                        "type": "string",
                        "description": "Entity or subject to remember, such as user or project.",
                    },
                    "field": {
                        "type": "string",
                        "description": "Fact name to update on the entity.",
                    },
                    "value": {
                        "type": "string",
                        "description": "The fact value to store.",
                    },
                    "source": {
                        "type": "string",
                        "description": "Optional source label or note for provenance.",
                    },
                },
                "required": ["entity", "field", "value"],
                "additionalProperties": False,
            },
        },
        {
            "name": "recall",
            "description": "Ask homestead-memory for relevant context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ask": {
                        "type": "string",
                        "description": "Question to answer from memory.",
                    },
                    "k": {
                        "type": "integer",
                        "description": "Maximum retrieval hits.",
                        "minimum": 1,
                        "default": 5,
                    },
                    "question_type": {
                        "type": "string",
                        "description": "Optional retrieval mode hint.",
                    },
                    "budget": {
                        "type": "integer",
                        "description": "Context token budget.",
                        "minimum": 1,
                        "default": 6000,
                    },
                },
                "required": ["ask"],
                "additionalProperties": False,
            },
        },
        {
            "name": "verify",
            "description": "Run homestead-memory integrity checks and return the score.",
            "parameters": {
                "type": "object",
                "properties": {
                    "deep": {
                        "type": "boolean",
                        "description": "Run deeper retrieval and provenance checks.",
                        "default": False,
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    ]
