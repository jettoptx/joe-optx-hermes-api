"""Memory API — pluggable memory backend for Hermes Workspace."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from hermes_optx_api.config import settings
from hermes_optx_api.memory.base import MemoryBackend
from hermes_optx_api.memory.holographic import HolographicBackend
from hermes_optx_api.memory.spacetimedb import SpacetimeDBBackend

router = APIRouter()

# Backend registry — add new backends here
_backends: dict[str, type[MemoryBackend]] = {
    "holographic": HolographicBackend,
    "sqlite": HolographicBackend,  # same impl, different name for clarity
    "spacetimedb": SpacetimeDBBackend,
}

_active_backend: Optional[MemoryBackend] = None


def get_backend() -> MemoryBackend:
    """Get or create the active memory backend."""
    global _active_backend
    if _active_backend is None:
        backend_name = settings.memory_backend
        backend_cls = _backends.get(backend_name)
        if not backend_cls:
            raise HTTPException(
                status_code=500,
                detail=f"Unknown memory backend: {backend_name}. "
                f"Available: {list(_backends.keys())}",
            )
        _active_backend = backend_cls()
    return _active_backend


class StoreRequest(BaseModel):
    content: str
    category: str = "general"
    importance: float = 0.5
    metadata: Optional[dict] = None


class SearchRequest(BaseModel):
    query: str
    limit: int = 10
    filters: Optional[dict] = None


@router.get("/memory")
async def list_memories(limit: int = 100, offset: int = 0):
    """List all memories."""
    backend = get_backend()
    entries = await backend.list_all(limit=limit, offset=offset)
    return {
        "memories": [e.to_dict() for e in entries],
        "total": len(entries),
        "backend": settings.memory_backend,
    }


@router.post("/memory")
async def store_memory(req: StoreRequest):
    """Store a new memory."""
    backend = get_backend()
    entry = await backend.store(
        content=req.content,
        category=req.category,
        importance=req.importance,
        metadata=req.metadata or {},
    )
    return {"stored": True, "memory": entry.to_dict()}


@router.post("/memory/search")
async def search_memories(req: SearchRequest):
    """Search memories with query and optional filters."""
    backend = get_backend()
    entries = await backend.search(
        query=req.query, filters=req.filters, limit=req.limit
    )
    return {
        "results": [e.to_dict() for e in entries],
        "total": len(entries),
        "query": req.query,
    }


@router.get("/memory/recall")
async def recall_memories(query: str, limit: int = 10):
    """Quick recall — GET-based memory search."""
    backend = get_backend()
    entries = await backend.recall(query=query, limit=limit)
    return {"results": [e.to_dict() for e in entries], "query": query}


@router.delete("/memory/{memory_id}")
async def delete_memory(memory_id: str):
    """Delete a memory by ID."""
    backend = get_backend()
    deleted = await backend.delete(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"deleted": True, "memory_id": memory_id}


@router.get("/memory/stats")
async def memory_stats():
    """Memory backend statistics."""
    backend = get_backend()
    stats = await backend.stats()
    health = await backend.health()
    return {**stats, **health}
