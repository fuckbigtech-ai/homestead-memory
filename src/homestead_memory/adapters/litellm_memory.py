"""LiteLLM helpers for keeping homestead-memory under the router."""
from __future__ import annotations

from typing import Any


class MemoryLogger:
    """Duck-typed LiteLLM CustomLogger that stamps served-model provenance."""

    def __init__(self, memory: Any, agent_name: str = "assistant") -> None:
        self.memory = memory
        self.agent_name = agent_name

    def log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        del start_time, end_time
        served_model = _served_model(kwargs, response_obj)
        self.memory.remember(
            "litellm",
            "last_success",
            served_model,
            source="LiteLLM CustomLogger",
            agent=f"{self.agent_name}@{served_model}",
        )


def inject_memory(messages: list[Any], memory: Any, budget: int = 2000) -> list[Any]:
    """Return a new messages list with homestead-memory context prepended."""
    copied = list(messages)
    query = _last_user_message(copied)
    context = _memory_context(memory, query, budget)
    if not context:
        return copied
    return [{"role": "system", "content": _system_content(context)}] + copied


def _served_model(kwargs: dict[str, Any], response_obj: Any) -> str:
    return str(
        getattr(response_obj, "model", None)
        or (response_obj.get("model") if isinstance(response_obj, dict) else None)
        or kwargs.get("model")
        or "unknown"
    )


def _last_user_message(messages: list[Any]) -> str:
    for msg in reversed(messages):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role == "user":
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            return _stringify_content(content)
    return ""


def _memory_context(memory: Any, query: str, budget: int) -> str:
    if not query:
        return ""
    try:
        answer = memory.ask(query, budget=budget)
    except TypeError:
        answer = memory.ask(query)
    except Exception:
        answer = None
    text = _context_text(answer)
    # Budget flows to memory.ask(token_budget=), but clamp locally too so the
    # injected system message is bounded regardless of what ask returns (~4 chars/token).
    max_chars = max(0, budget) * 4
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n…[memory context truncated to fit budget]"
    return text


def _context_text(answer: Any) -> str:
    if isinstance(answer, dict):
        for key in ("context", "answer"):
            value = answer.get(key)
            if value:
                return str(value)
        hits = answer.get("hits") or answer.get("passages") or []
        if isinstance(hits, list):
            parts = []
            for hit in hits:
                if isinstance(hit, dict):
                    parts.append(str(hit.get("snippet") or hit.get("text") or hit.get("content") or ""))
                else:
                    parts.append(str(hit))
            return "\n".join(part for part in parts if part)
    return str(answer or "")


def _system_content(context: str) -> str:
    return "Homestead memory context:\n" + context


def _stringify_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_stringify_content(item) for item in content)
    if isinstance(content, dict):
        if "text" in content:
            return str(content["text"])
        if "content" in content:
            return _stringify_content(content["content"])
    return str(content or "")
