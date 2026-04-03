"""Sessions API — read/manage Hermes Agent sessions from state.db."""

from typing import Optional

import aiosqlite
from fastapi import APIRouter, HTTPException

from hermes_optx_api.config import settings

router = APIRouter()


async def _get_db():
    """Open the Hermes state.db (read-only)."""
    db_path = settings.state_db_path
    if not db_path.exists():
        raise HTTPException(status_code=503, detail="state.db not found")
    return await aiosqlite.connect(str(db_path), uri=True)


@router.get("/sessions")
async def list_sessions(limit: int = 50, offset: int = 0):
    """List all agent sessions."""
    try:
        async with await _get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM sessions LIMIT ? OFFSET ?",
                (limit, offset),
            )
            rows = await cursor.fetchall()
            return {
                "sessions": [dict(row) for row in rows],
                "total": len(rows),
                "limit": limit,
                "offset": offset,
            }
    except Exception as e:
        if "no such table" in str(e):
            return {"sessions": [], "total": 0, "limit": limit, "offset": offset}
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Get a specific session by ID."""
    try:
        async with await _get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            )
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Session not found")
            return dict(row)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    limit: int = 100,
    offset: int = 0,
):
    """Get messages for a specific session."""
    try:
        async with await _get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM messages WHERE session_id = ? LIMIT ? OFFSET ?",
                (session_id, limit, offset),
            )
            rows = await cursor.fetchall()
            return {
                "messages": [dict(row) for row in rows],
                "session_id": session_id,
                "total": len(rows),
            }
    except Exception as e:
        if "no such table" in str(e):
            return {"messages": [], "session_id": session_id, "total": 0}
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session and its messages."""
    try:
        async with await _get_db() as db:
            await db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            await db.commit()
            return {"deleted": True, "session_id": session_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
