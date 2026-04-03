"""Abstract memory backend interface.

Implement this to add custom memory backends (SpacetimeDB, Redis, etc.).
"""

from abc import ABC, abstractmethod
from typing import Any, Optional


class MemoryEntry:
    """A single memory entry."""

    def __init__(
        self,
        id: str,
        content: str,
        category: str = "general",
        importance: float = 0.5,
        metadata: Optional[dict[str, Any]] = None,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
    ):
        self.id = id
        self.content = content
        self.category = category
        self.importance = importance
        self.metadata = metadata or {}
        self.created_at = created_at
        self.updated_at = updated_at

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "category": self.category,
            "importance": self.importance,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class MemoryBackend(ABC):
    """Abstract interface for memory storage backends.

    Implement this class to create a custom memory provider.
    See examples/ for SpacetimeDB and SQLite implementations.
    """

    @abstractmethod
    async def store(self, content: str, category: str = "general", **kwargs) -> MemoryEntry:
        """Store a new memory. Returns the created entry."""

    @abstractmethod
    async def recall(self, query: str, limit: int = 10, **kwargs) -> list[MemoryEntry]:
        """Retrieve memories matching a query (semantic or keyword search)."""

    @abstractmethod
    async def search(
        self, query: str, filters: Optional[dict] = None, limit: int = 10
    ) -> list[MemoryEntry]:
        """Search memories with optional filters (category, importance, date range)."""

    @abstractmethod
    async def delete(self, memory_id: str) -> bool:
        """Delete a memory by ID. Returns True if deleted."""

    @abstractmethod
    async def list_all(self, limit: int = 100, offset: int = 0) -> list[MemoryEntry]:
        """List all memories with pagination."""

    @abstractmethod
    async def stats(self) -> dict:
        """Return memory statistics (count, categories, storage size, etc.)."""

    async def health(self) -> dict:
        """Health check for the backend. Override for custom checks."""
        return {"status": "ok", "backend": self.__class__.__name__}
