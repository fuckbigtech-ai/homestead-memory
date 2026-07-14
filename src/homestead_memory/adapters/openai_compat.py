"""OpenAI-compatible chat adapter with homestead-memory context injection.

The client is duck-typed: official OpenAI, OpenRouter, LiteLLM proxy, vLLM,
ollama, and similar clients all work when they expose
``client.chat.completions.create``.
"""
from __future__ import annotations

from typing import Any, Callable


RememberFn = Callable[..., Any]


class MemoryChat:
    """Wrap an OpenAI-compatible chat client while keeping memory below the router."""

    def __init__(
        self,
        client: Any,
        memory: Any,
        agent_name: str = "assistant",
        inject: bool = True,
        budget: int = 2000,
        remember_fn: RememberFn | None = None,
    ) -> None:
        self.client = client
        self.memory = memory
        self.agent_name = agent_name
        self.inject = inject
        self.budget = budget
        self.remember_fn = remember_fn

    def create(self, model: str, messages: list[Any], **kw: Any) -> Any:
        augmented = list(messages)
        if self.inject:
            query = _last_user_message(augmented)
            context = _memory_context(self.memory, query, self.budget)
            if context:
                augmented = [{"role": "system", "content": _system_content(context)}] + augmented

        response = self.client.chat.completions.create(model=model, messages=augmented, **kw)
        resolved_model = str(getattr(response, "model", None) or model)
        if self.remember_fn is not None:
            self.remember_fn(response, self.memory, agent=f"{self.agent_name}@{resolved_model}")
        return response


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
    if isinstance(answer, list):
        return "\n".join(_context_text({"hits": answer}).splitlines())
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
