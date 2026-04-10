"""Chat streaming routes — SSE proxy to Hermes Agent v0.8.0+ gateway.

Implements:
- POST /api/sessions/{session_id}/chat/stream  → SSE streaming chat (Workspace/JettChat)
- POST /v1/chat/completions (streaming)        → OpenAI-compatible SSE proxy
"""

import json
import uuid
from typing import AsyncGenerator, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from hermes_optx_api.config import settings

router = APIRouter()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str = "user"
    content: str


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    model: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 4096
    stream: bool = True


class CompletionRequest(BaseModel):
    model: str = "grok-4.20-0309-reasoning"
    messages: list[dict] = Field(default_factory=list)
    temperature: float = 0.7
    max_tokens: int = 4096
    stream: bool = False


# ---------------------------------------------------------------------------
# SSE Chat — /api/sessions/{session_id}/chat/stream
# ---------------------------------------------------------------------------

async def _stream_chat(
    client: httpx.AsyncClient,
    session_id: str,
    message: str,
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> AsyncGenerator[dict, None]:
    """Stream chat response from Hermes Agent gateway via SSE."""

    # Build the request for Hermes gateway
    payload = {
        "messages": [{"role": "user", "content": message}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if model:
        payload["model"] = model

    # Try the Hermes v0.8.0 session chat endpoint first
    url = f"/api/sessions/{session_id}/chat"
    try:
        async with client.stream(
            "POST", url, json=payload, timeout=120.0
        ) as resp:
            if resp.status_code == 404:
                # Fall back to /v1/chat/completions (older gateway)
                raise httpx.HTTPStatusError(
                    "Not found", request=resp.request, response=resp
                )

            yield {"event": "session", "data": json.dumps({
                "session_id": session_id,
                "status": "streaming",
            })}

            buffer = ""
            async for chunk in resp.aiter_text():
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            yield {"event": "done", "data": json.dumps({
                                "session_id": session_id,
                                "status": "complete",
                            })}
                            return
                        try:
                            parsed = json.loads(data)
                            delta = (
                                parsed.get("choices", [{}])[0]
                                .get("delta", {})
                                .get("content", "")
                            )
                            if delta:
                                yield {"event": "content", "data": json.dumps({
                                    "content": delta,
                                    "session_id": session_id,
                                })}
                        except json.JSONDecodeError:
                            yield {"event": "content", "data": json.dumps({
                                "content": data,
                                "session_id": session_id,
                            })}

            yield {"event": "done", "data": json.dumps({
                "session_id": session_id,
                "status": "complete",
            })}
            return

    except (httpx.HTTPStatusError, httpx.ConnectError):
        pass

    # Fallback: /v1/chat/completions with streaming
    payload["model"] = model or "grok-4.20-0309-reasoning"
    try:
        async with client.stream(
            "POST", "/v1/chat/completions", json=payload, timeout=120.0
        ) as resp:
            if resp.status_code != 200:
                yield {"event": "error", "data": json.dumps({
                    "error": f"Upstream returned {resp.status_code}",
                    "session_id": session_id,
                })}
                return

            yield {"event": "session", "data": json.dumps({
                "session_id": session_id,
                "status": "streaming",
                "fallback": "v1_completions",
            })}

            buffer = ""
            async for chunk in resp.aiter_text():
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            yield {"event": "done", "data": json.dumps({
                                "session_id": session_id,
                                "status": "complete",
                            })}
                            return
                        try:
                            parsed = json.loads(data)
                            delta = (
                                parsed.get("choices", [{}])[0]
                                .get("delta", {})
                                .get("content", "")
                            )
                            if delta:
                                yield {"event": "content", "data": json.dumps({
                                    "content": delta,
                                    "session_id": session_id,
                                })}
                        except json.JSONDecodeError:
                            pass

            yield {"event": "done", "data": json.dumps({
                "session_id": session_id,
                "status": "complete",
            })}

    except httpx.TimeoutException:
        yield {"event": "error", "data": json.dumps({
            "error": "Gateway timeout",
            "session_id": session_id,
        })}
    except Exception as e:
        yield {"event": "error", "data": json.dumps({
            "error": str(e)[:200],
            "session_id": session_id,
        })}


@router.post("/sessions/{session_id}/chat/stream")
async def chat_stream(session_id: str, req: ChatRequest, request: Request):
    """SSE streaming chat — sends user message to Hermes Agent, streams response.

    Events:
    - session: {session_id, status} — session opened
    - content: {content, session_id} — token chunk
    - done: {session_id, status} — stream complete
    - error: {error, session_id} — error occurred
    """
    client: httpx.AsyncClient = request.app.state.hermes_client

    async def event_generator():
        async for event in _stream_chat(
            client=client,
            session_id=session_id,
            message=req.message,
            model=req.model,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
        ):
            yield event

    return EventSourceResponse(event_generator())


@router.post("/sessions/new/chat/stream")
async def new_session_chat(req: ChatRequest, request: Request):
    """Create a new session and immediately start streaming.

    Convenience endpoint for JettChat — avoids a separate session create call.
    """
    session_id = req.session_id or str(uuid.uuid4())[:12]
    client: httpx.AsyncClient = request.app.state.hermes_client

    async def event_generator():
        yield {"event": "session", "data": json.dumps({
            "session_id": session_id,
            "status": "created",
        })}

        async for event in _stream_chat(
            client=client,
            session_id=session_id,
            message=req.message,
            model=req.model,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
        ):
            yield event

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# Streaming /v1/chat/completions proxy
# ---------------------------------------------------------------------------

@router.post("/chat/completions/stream")
async def completions_stream(req: CompletionRequest, request: Request):
    """OpenAI-compatible streaming completions proxy.

    Accepts the same payload as /v1/chat/completions but always streams via SSE.
    Useful for clients that want SSE without setting stream=true themselves.
    """
    client: httpx.AsyncClient = request.app.state.hermes_client

    payload = {
        "model": req.model,
        "messages": req.messages,
        "temperature": req.temperature,
        "max_tokens": req.max_tokens,
        "stream": True,
    }

    async def event_generator():
        try:
            async with client.stream(
                "POST", "/v1/chat/completions", json=payload, timeout=120.0
            ) as resp:
                if resp.status_code != 200:
                    yield {"event": "error", "data": json.dumps({
                        "error": f"Upstream returned {resp.status_code}",
                    })}
                    return

                buffer = ""
                async for chunk in resp.aiter_text():
                    buffer += chunk
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                yield {"event": "done", "data": "[DONE]"}
                                return
                            yield {"data": data}

                yield {"event": "done", "data": "[DONE]"}

        except httpx.TimeoutException:
            yield {"event": "error", "data": json.dumps({"error": "Gateway timeout"})}
        except Exception as e:
            yield {"event": "error", "data": json.dumps({"error": str(e)[:200]})}

    return EventSourceResponse(event_generator())
