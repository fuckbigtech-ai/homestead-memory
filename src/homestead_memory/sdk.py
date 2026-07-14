"""Small stdlib-only Python SDK for homestead-memory."""
from __future__ import annotations

from pathlib import Path
from typing import Any


class Memory:
    """Client wrapper around the core homestead-memory functions."""

    def __init__(self, vault: str | Path | None = None, agent: str | None = None) -> None:
        """Resolve the vault path and store the default writer agent."""
        from .core import vault as vaultlib

        self.vault = vaultlib._resolve(vault)
        self.agent = agent or "homestead-memory-sdk"

    def ask(
        self,
        query: str,
        k: int | None = None,
        question_type: str | None = None,
        budget: int = 6000,
    ) -> dict[str, Any]:
        """Retrieve context and, when configured, synthesize an answer."""
        from .core import index

        return index.ask(
            query,
            self.vault,
            k=k,
            question_type=question_type,
            token_budget=budget,
        )

    def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        """Return ranked passages from the vault."""
        from .core import index

        return index.search(query, self.vault, k=k)

    def remember(
        self,
        entity: str,
        field: str,
        value: str,
        source: str | None = None,
        agent: str | None = None,
    ) -> dict[str, Any]:
        """Record or update one distilled fact."""
        from .core import remember as remember_mod

        return remember_mod.remember(
            entity,
            field,
            value,
            vault=self.vault,
            source=source,
            agent=agent if agent is not None else self.agent,
        )

    def resolve(
        self,
        entity: str,
        field: str | None = None,
        strategy: str = "latest",
        agent: str | None = None,
    ) -> dict[str, Any]:
        """Resolve duplicate values in a distilled note."""
        from .core import resolve as resolve_mod

        return resolve_mod.resolve(
            entity,
            vault=self.vault,
            field=field,
            strategy=strategy,
            agent=agent if agent is not None else self.agent,
        )

    def verify(self, deep: bool = False) -> dict[str, Any]:
        """Run the memory integrity checks for this vault."""
        from .core import verify as verify_mod

        return verify_mod.verify_vault(self.vault, deep=deep)

    def history(self, note: str, as_of: str | None = None) -> list[dict[str, Any]]:
        """Return recorded history for a note, optionally as of a date."""
        from .core import temporal

        if as_of is not None:
            return temporal.as_of(note, as_of, vault=self.vault)
        return temporal.history(note, vault=self.vault)

    def ingest(self) -> dict[str, Any]:
        """Index the vault for retrieval."""
        from .core import index

        return index.ingest(self.vault)

    def distill(self, dry: bool = False, agent: str | None = None) -> dict[str, Any]:
        """Run the cited distilled-layer extraction pass."""
        from .core import distill as distill_mod

        return distill_mod.distill(
            self.vault,
            dry=dry,
            agent=agent if agent is not None else self.agent,
        )

    def okf_import(
        self,
        source: str | Path,
        agent: str = "okf-import",
    ) -> dict[str, Any]:
        """Import an Open Knowledge Format file or directory into this vault."""
        from .adapters.okf import okf_import

        return okf_import(source, vault=self.vault, agent=agent)

    def okf_export(self, out_dir: str | Path | None = None) -> dict[str, Any]:
        """Export this vault as an Open Knowledge Format directory."""
        from .adapters.okf import okf_export

        return okf_export(vault=self.vault, out_dir=out_dir)


def connect(vault: str | Path | None = None, agent: str | None = None) -> Memory:
    """Create a Memory client for a vault."""
    return Memory(vault, agent)
