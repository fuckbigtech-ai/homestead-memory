"""LangGraph store adapter.

``HomesteadStore`` targets langgraph BaseStore (>=0.2): the documented
``put/get/search/delete/list_namespaces`` sync methods plus the required async
``aput/aget/asearch/adelete/alist_namespaces`` methods.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


try:  # pragma: no cover - exercised only when langgraph is installed.
    from langgraph.store.base import BaseStore as _BaseStore
except Exception:  # Framework is optional; keep this module importable.
    class _BaseStore:  # type: ignore[no-redef]
        pass


Namespace = tuple[str, ...]


@dataclass
class StoreItem:
    """Small Item-compatible object matching LangGraph's public item fields."""

    namespace: Namespace
    key: str
    value: dict[str, Any]
    created_at: str
    updated_at: str
    score: float | None = None

    def dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "value": self.value,
            "key": self.key,
            "namespace": list(self.namespace),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.score is not None:
            out["score"] = self.score
        return out


class HomesteadStore(_BaseStore):
    """Persistent LangGraph BaseStore facade backed by ``homestead_memory.Memory``.

    Each ``namespace`` plus ``key`` is written through ``Memory.remember`` as a
    distilled fact. This adapter also keeps a process-local exact lookup cache so
    LangGraph's key-value ``get`` and namespace listing semantics work during a
    graph process without requiring private homestead-memory delete APIs.
    """

    def __init__(self, memory: Any) -> None:
        self.memory = memory
        self._items: dict[tuple[Namespace, str], StoreItem] = {}

    def put(
        self,
        namespace: Iterable[str],
        key: str,
        value: dict[str, Any],
        index: Any | None = None,
    ) -> None:
        del index
        ns = _namespace(namespace)
        now = _now()
        item_key = (ns, str(key))
        created_at = self._items.get(item_key).created_at if item_key in self._items else now
        item = StoreItem(ns, str(key), _json_object(value), created_at, now)
        self._items[item_key] = item
        self.memory.remember(
            _entity(ns),
            str(key),
            json.dumps(item.value, sort_keys=True, separators=(",", ":")),
            source="langgraph BaseStore",
        )

    def get(self, namespace: Iterable[str], key: str) -> StoreItem | None:
        ns = _namespace(namespace)
        answer = self.memory.ask(f"{_entity(ns)} {key}", k=1)
        item = self._items.get((ns, str(key)))
        if item is not None:
            return item
        return _item_from_answer(ns, str(key), answer)

    def search(
        self,
        namespace_prefix: Iterable[str],
        *,
        query: str | None = None,
        filter: dict[str, Any] | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[StoreItem]:
        prefix = _namespace(namespace_prefix)
        results = [
            item
            for (ns, _key), item in self._items.items()
            if _starts_with(ns, prefix) and _matches_filter(item.value, filter)
        ]
        if query is not None:
            hits = self.memory.search(query, k=limit + offset)
            synthetic = [_item_from_hit(prefix, hit) for hit in hits]
            results.extend(item for item in synthetic if item is not None)
        else:
            self.memory.ask(_entity(prefix), k=limit + offset)
        return results[offset: offset + limit]

    def delete(self, namespace: Iterable[str], key: str) -> None:
        ns = _namespace(namespace)
        self._items.pop((ns, str(key)), None)
        self.memory.remember(
            _entity(ns),
            str(key),
            "[deleted]",
            source="langgraph BaseStore delete",
        )

    def list_namespaces(
        self,
        *,
        prefix: Iterable[str] | None = None,
        suffix: Iterable[str] | None = None,
        max_depth: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Namespace]:
        prefix_ns = _namespace(prefix or ())
        suffix_ns = _namespace(suffix or ())
        namespaces = sorted({
            ns
            for ns, _key in self._items
            if _starts_with(ns, prefix_ns) and _ends_with(ns, suffix_ns)
        })
        if max_depth is not None:
            namespaces = [ns[:max_depth] for ns in namespaces if len(ns) >= max_depth]
            namespaces = sorted(set(namespaces))
        return namespaces[offset: offset + limit]

    async def aput(
        self,
        namespace: Iterable[str],
        key: str,
        value: dict[str, Any],
        index: Any | None = None,
    ) -> None:
        self.put(namespace, key, value, index=index)

    async def aget(self, namespace: Iterable[str], key: str) -> StoreItem | None:
        return self.get(namespace, key)

    async def asearch(
        self,
        namespace_prefix: Iterable[str],
        *,
        query: str | None = None,
        filter: dict[str, Any] | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[StoreItem]:
        return self.search(
            namespace_prefix,
            query=query,
            filter=filter,
            limit=limit,
            offset=offset,
        )

    async def adelete(self, namespace: Iterable[str], key: str) -> None:
        self.delete(namespace, key)

    async def alist_namespaces(
        self,
        *,
        prefix: Iterable[str] | None = None,
        suffix: Iterable[str] | None = None,
        max_depth: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Namespace]:
        return self.list_namespaces(
            prefix=prefix,
            suffix=suffix,
            max_depth=max_depth,
            limit=limit,
            offset=offset,
        )

    def batch(self, ops: Iterable[Any]) -> list[Any]:
        return [_dispatch_sync(self, op) for op in ops]

    async def abatch(self, ops: Iterable[Any]) -> list[Any]:
        return [await _dispatch_async(self, op) for op in ops]


def _namespace(namespace: Iterable[str]) -> Namespace:
    return tuple(str(part) for part in namespace)


def _entity(namespace: Namespace) -> str:
    return "langgraph:" + "/".join(namespace)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_object(value: dict[str, Any]) -> dict[str, Any]:
    json.dumps(value)
    return dict(value)


def _starts_with(namespace: Namespace, prefix: Namespace) -> bool:
    return namespace[: len(prefix)] == prefix


def _ends_with(namespace: Namespace, suffix: Namespace) -> bool:
    return not suffix or namespace[-len(suffix):] == suffix


def _matches_filter(value: dict[str, Any], filter: dict[str, Any] | None) -> bool:
    if not filter:
        return True
    return all(value.get(key) == expected for key, expected in filter.items())


def _item_from_answer(namespace: Namespace, key: str, answer: Any) -> StoreItem | None:
    if not isinstance(answer, dict) or not answer.get("hits"):
        return None
    context = str(answer.get("context", ""))
    now = _now()
    return StoreItem(namespace, key, {"memory": context}, now, now)


def _item_from_hit(namespace: Namespace, hit: Any) -> StoreItem | None:
    if not isinstance(hit, dict):
        return None
    now = _now()
    rel = str(hit.get("rel") or hit.get("path") or "memory")
    return StoreItem(
        namespace,
        rel,
        {"memory": str(hit.get("snippet", "")), "rel": rel},
        now,
        now,
        score=hit.get("score"),
    )


def _op_name(op: Any) -> str:
    return op.__class__.__name__.lower()


def _getattr_any(op: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if hasattr(op, name):
            return getattr(op, name)
    return default


def _dispatch_sync(store: HomesteadStore, op: Any) -> Any:
    name = _op_name(op)
    namespace = _getattr_any(op, "namespace", "namespace_prefix", default=())
    key = _getattr_any(op, "key", default=None)
    if "put" in name:
        store.put(namespace, key, _getattr_any(op, "value", default={}), index=_getattr_any(op, "index", default=None))
        return None
    if "get" in name:
        return store.get(namespace, key)
    if "delete" in name:
        store.delete(namespace, key)
        return None
    if "search" in name:
        return store.search(
            namespace,
            query=_getattr_any(op, "query", default=None),
            filter=_getattr_any(op, "filter", default=None),
            limit=_getattr_any(op, "limit", default=10),
            offset=_getattr_any(op, "offset", default=0),
        )
    if "list" in name:
        return store.list_namespaces(
            prefix=_getattr_any(op, "prefix", default=None),
            suffix=_getattr_any(op, "suffix", default=None),
            max_depth=_getattr_any(op, "max_depth", default=None),
            limit=_getattr_any(op, "limit", default=100),
            offset=_getattr_any(op, "offset", default=0),
        )
    raise NotImplementedError(f"Unsupported LangGraph store operation: {op!r}")


async def _dispatch_async(store: HomesteadStore, op: Any) -> Any:
    return _dispatch_sync(store, op)


try:
    HomesteadStore.__abstractmethods__ = frozenset()
except Exception:  # pragma: no cover
    pass
