"""SpacetimeDB memory backend — reads/writes to optx-cortex on Jetson edge node.

Uses the SpacetimeDB HTTP SQL API and reducer calls.
Tables: memory_entry (category, key, value, source, owner, importance, session_id, created_at)
"""

import json
import os
import time
from typing import Optional
from uuid import uuid4

import httpx

from hermes_optx_api.config import settings
from hermes_optx_api.memory.base import MemoryBackend, MemoryEntry

STDB_URL = os.getenv("SPACETIMEDB_URL", "http://100.85.183.16:3000")
STDB_DB = os.getenv("SPACETIMEDB_DB", "optx-cortex")
STDB_TIMEOUT = 8.0


class SpacetimeDBBackend(MemoryBackend):
    """SpacetimeDB memory backend — real-time edge database on Jetson."""

    def __init__(self):
        self.base_url = settings.memory_db_url or STDB_URL
        self.db = settings.spacetimedb_db or STDB_DB

    async def _sql(self, query: str) -> list[dict]:
        """Execute SQL query against SpacetimeDB."""
        async with httpx.AsyncClient(timeout=STDB_TIMEOUT) as client:
            resp = await client.post(
                f"{self.base_url}/v1/database/{self.db}/sql",
                content=query,
                headers={"Content-Type": "text/plain"},
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
                return data.get("rows", [])
            return []

    async def _call(self, reducer: str, args: list) -> bool:
        """Call a SpacetimeDB reducer."""
        async with httpx.AsyncClient(timeout=STDB_TIMEOUT) as client:
            resp = await client.post(
                f"{self.base_url}/v1/database/{self.db}/call/{reducer}",
                json=args,
                headers={"Content-Type": "application/json"},
            )
            return resp.status_code in (200, 204)

    async def store(self, content: str, category: str = "general", **kwargs) -> MemoryEntry:
        entry_id = str(uuid4())[:12]
        importance = kwargs.get("importance", 0.5)
        metadata = kwargs.get("metadata", {})
        source = kwargs.get("source", "hermes-optx-api")
        owner = kwargs.get("owner", "astrojoe")
        session_id = kwargs.get("session_id", "")

        value = json.dumps({"content": content, "metadata": metadata})

        ok = await self._call("store_memory", [
            category,
            f"mem:{entry_id}",
            value,
            source,
            owner,
            int(importance * 10),  # SpacetimeDB uses integer importance
            session_id,
        ])

        if not ok:
            raise RuntimeError("SpacetimeDB store_memory reducer failed")

        return MemoryEntry(
            id=entry_id,
            content=content,
            category=category,
            importance=importance,
            metadata=metadata,
            created_at=str(int(time.time())),
        )

    async def recall(self, query: str, limit: int = 10, **kwargs) -> list[MemoryEntry]:
        return await self.search(query, limit=limit)

    async def search(
        self, query: str, filters: Optional[dict] = None, limit: int = 10
    ) -> list[MemoryEntry]:
        # SpacetimeDB doesn't support LIKE — fetch all and filter client-side
        category_filter = ""
        if filters and filters.get("category"):
            category_filter = f" WHERE category = '{filters['category']}'"
        elif not filters:
            category_filter = ""

        rows = await self._sql(
            f"SELECT id, category, key, value, importance, owner, created_at "
            f"FROM memory_entry{category_filter}"
        )

        entries = []
        query_lower = query.lower() if query else ""

        for row in rows:
            try:
                raw_value = row.get("value", "{}")
                data = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
                content = data.get("content", raw_value if isinstance(raw_value, str) else "")
                metadata = data.get("metadata", {})

                # Client-side text search
                if query_lower and query_lower not in content.lower():
                    key = row.get("key", "")
                    cat = row.get("category", "")
                    if query_lower not in key.lower() and query_lower not in cat.lower():
                        continue

                entries.append(MemoryEntry(
                    id=row.get("key", "").replace("mem:", ""),
                    content=content,
                    category=row.get("category", "general"),
                    importance=row.get("importance", 5) / 10.0,
                    metadata=metadata,
                    created_at=str(row.get("created_at", "")),
                ))

                if len(entries) >= limit:
                    break

            except (json.JSONDecodeError, AttributeError):
                continue

        return entries

    async def delete(self, memory_id: str) -> bool:
        return await self._call("delete_memory", [f"mem:{memory_id}"])

    async def list_all(self, limit: int = 100, offset: int = 0) -> list[MemoryEntry]:
        rows = await self._sql(
            "SELECT id, category, key, value, importance, owner, created_at "
            "FROM memory_entry"
        )

        entries = []
        for i, row in enumerate(rows):
            if i < offset:
                continue
            if len(entries) >= limit:
                break

            try:
                raw_value = row.get("value", "{}")
                data = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
                content = data.get("content", raw_value if isinstance(raw_value, str) else "")
                metadata = data.get("metadata", {})

                entries.append(MemoryEntry(
                    id=row.get("key", "").replace("mem:", ""),
                    content=content,
                    category=row.get("category", "general"),
                    importance=row.get("importance", 5) / 10.0,
                    metadata=metadata,
                    created_at=str(row.get("created_at", "")),
                ))
            except (json.JSONDecodeError, AttributeError):
                continue

        return entries

    async def stats(self) -> dict:
        rows = await self._sql("SELECT category FROM memory_entry")
        categories = list(set(r.get("category", "") for r in rows))
        return {
            "total_memories": len(rows),
            "categories": categories,
            "backend": "spacetimedb",
            "db_url": self.base_url,
            "db_name": self.db,
        }

    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.base_url}/v1/ping")
                if resp.status_code == 200:
                    return {"status": "ok", "backend": "spacetimedb"}
        except Exception:
            pass
        return {"status": "unreachable", "backend": "spacetimedb"}
