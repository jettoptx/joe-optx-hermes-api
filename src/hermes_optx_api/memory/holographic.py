"""Holographic memory backend — reads Hermes Agent's built-in memory (SQLite + FTS5).

This is the default backend. It reads from the agent's ~/.hermes/memories/ directory
and state.db, providing the Workspace UI with memory browsing capabilities.
"""

import json
import sqlite3
from pathlib import Path
from typing import Optional
from uuid import uuid4

import aiosqlite

from hermes_optx_api.config import settings
from hermes_optx_api.memory.base import MemoryBackend, MemoryEntry


class HolographicBackend(MemoryBackend):
    """Reads Hermes Agent's built-in holographic memory."""

    def __init__(self):
        self.memories_dir = settings.memories_dir
        self.state_db = settings.state_db_path
        self._local_db: Optional[str] = None

    async def _ensure_local_db(self) -> str:
        """Create local SQLite + FTS5 if no Hermes memory DB exists."""
        if self._local_db:
            return self._local_db

        db_path = str(settings.hermes_home / "optx_memory.db")
        async with aiosqlite.connect(db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    category TEXT DEFAULT 'general',
                    importance REAL DEFAULT 0.5,
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now'))
                )
            """)
            await db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                USING fts5(content, category, content=memories, content_rowid=rowid)
            """)
            await db.commit()

        self._local_db = db_path
        return db_path

    async def store(self, content: str, category: str = "general", **kwargs) -> MemoryEntry:
        db_path = await self._ensure_local_db()
        entry_id = str(uuid4())
        importance = kwargs.get("importance", 0.5)
        metadata = json.dumps(kwargs.get("metadata", {}))

        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO memories (id, content, category, importance, metadata) VALUES (?, ?, ?, ?, ?)",
                (entry_id, content, category, importance, metadata),
            )
            await db.execute(
                "INSERT INTO memories_fts (rowid, content, category) VALUES (last_insert_rowid(), ?, ?)",
                (content, category),
            )
            await db.commit()

        return MemoryEntry(id=entry_id, content=content, category=category, importance=importance)

    async def recall(self, query: str, limit: int = 10, **kwargs) -> list[MemoryEntry]:
        return await self.search(query, limit=limit)

    async def search(
        self, query: str, filters: Optional[dict] = None, limit: int = 10
    ) -> list[MemoryEntry]:
        db_path = await self._ensure_local_db()
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            if query:
                cursor = await db.execute(
                    """
                    SELECT m.* FROM memories m
                    JOIN memories_fts fts ON m.rowid = fts.rowid
                    WHERE memories_fts MATCH ?
                    LIMIT ?
                    """,
                    (query, limit),
                )
            else:
                cursor = await db.execute("SELECT * FROM memories LIMIT ?", (limit,))

            rows = await cursor.fetchall()
            return [
                MemoryEntry(
                    id=row["id"],
                    content=row["content"],
                    category=row["category"],
                    importance=row["importance"],
                    metadata=json.loads(row["metadata"] or "{}"),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
                for row in rows
            ]

    async def delete(self, memory_id: str) -> bool:
        db_path = await self._ensure_local_db()
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def list_all(self, limit: int = 100, offset: int = 0) -> list[MemoryEntry]:
        db_path = await self._ensure_local_db()
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM memories LIMIT ? OFFSET ?", (limit, offset)
            )
            rows = await cursor.fetchall()
            return [
                MemoryEntry(
                    id=row["id"],
                    content=row["content"],
                    category=row["category"],
                    importance=row["importance"],
                    metadata=json.loads(row["metadata"] or "{}"),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
                for row in rows
            ]

    async def stats(self) -> dict:
        db_path = await self._ensure_local_db()
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM memories")
            count = (await cursor.fetchone())[0]
            cursor = await db.execute("SELECT DISTINCT category FROM memories")
            categories = [row[0] for row in await cursor.fetchall()]
            return {
                "total_memories": count,
                "categories": categories,
                "backend": "holographic",
                "db_path": db_path,
            }
