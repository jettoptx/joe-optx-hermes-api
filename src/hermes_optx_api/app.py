"""FastAPI application — Enhanced API bridge for Hermes Agent v0.8.0+."""

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from hermes_optx_api.config import settings
from hermes_optx_api.payments import verify_payment
from hermes_optx_api.routes import sessions, skills, memory, config, tasks, wallet, chat

logger = logging.getLogger(__name__)

VERSION = "0.3.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown — create shared HTTP client for proxying."""
    app.state.hermes_client = httpx.AsyncClient(
        base_url=settings.hermes_agent_url,
        timeout=30.0,
    )
    # Probe upstream capabilities at startup
    app.state.gateway_caps = await _probe_capabilities(app.state.hermes_client)
    logger.info("hermes-optx-api %s → %s (%s)",
                VERSION, settings.hermes_agent_url, app.state.gateway_caps.get("mode", "unknown"))
    yield
    await app.state.hermes_client.aclose()


async def _probe_capabilities(client: httpx.AsyncClient) -> dict:
    """Probe the Hermes Agent gateway for supported features."""
    caps = {
        "mode": "disconnected",
        "model": "",
        "version": "",
        "sessions_api": False,
        "streaming": False,
        "mcp_servers": False,
    }
    try:
        resp = await client.get("/health", timeout=5.0)
        if resp.status_code == 200:
            caps["mode"] = "portable"
            data = resp.json()
            caps["version"] = data.get("version", "")

        # Check for enhanced endpoints (v0.8.0+)
        resp = await client.get("/v1/models", timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("data"):
                caps["model"] = data["data"][0].get("id", "")
            caps["mode"] = "enhanced-hermes"
            caps["streaming"] = True

        # Check if session API exists on upstream
        resp = await client.get("/api/sessions", timeout=3.0)
        if resp.status_code in (200, 401):
            caps["sessions_api"] = True

        # Check MCP
        resp = await client.get("/api/config", timeout=3.0)
        if resp.status_code == 200:
            cfg = resp.json()
            caps["mcp_servers"] = bool(cfg.get("config", {}).get("mcp_servers"))

    except Exception:
        pass

    return caps


app = FastAPI(
    title="hermes-optx-api",
    description="Enhanced API bridge for Hermes Agent v0.8.0+ — SSE streaming, memory, tasks, wallet",
    version=VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Enhanced endpoints — payment-gated when MPP_ENABLED=true
app.include_router(
    sessions.router, prefix="/api", tags=["sessions"],
    dependencies=[Depends(verify_payment)],
)
app.include_router(
    skills.router, prefix="/api", tags=["skills"],
    dependencies=[Depends(verify_payment)],
)
app.include_router(
    memory.router, prefix="/api", tags=["memory"],
    dependencies=[Depends(verify_payment)],
)
app.include_router(
    config.router, prefix="/api", tags=["config"],
    dependencies=[Depends(verify_payment)],
)
app.include_router(
    tasks.router, prefix="/api", tags=["tasks"],
    dependencies=[Depends(verify_payment)],
)
# Tempo Wallet — metered HEDGEHOG billing + escrow + on-chain balances
app.include_router(
    wallet.router, prefix="/api", tags=["wallet"],
    dependencies=[Depends(verify_payment)],
)
# SSE streaming chat — sessions + completions
app.include_router(
    chat.router, prefix="/api", tags=["chat"],
    dependencies=[Depends(verify_payment)],
)


@app.get("/health")
async def health(request: Request):
    """Health check — always free, no auth required."""
    upstream_ok = False
    try:
        resp = await request.app.state.hermes_client.get("/health")
        upstream_ok = resp.status_code == 200
    except Exception:
        pass

    return {
        "status": "ok",
        "platform": "hermes-optx-api",
        "version": VERSION,
        "upstream": {
            "url": settings.hermes_agent_url,
            "connected": upstream_ok,
        },
        "capabilities": getattr(request.app.state, "gateway_caps", {}),
        "memory_backend": settings.memory_backend,
        "mpp_enabled": settings.mpp_enabled,
    }


@app.api_route(
    "/v1/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    dependencies=[Depends(verify_payment)],
)
async def proxy_v1(request: Request, path: str):
    """Passthrough proxy — forward all /v1/* requests to Hermes Agent.

    Supports streaming: if the upstream returns chunked/SSE, we stream it through.
    """
    from starlette.responses import StreamingResponse

    client: httpx.AsyncClient = request.app.state.hermes_client
    url = f"/v1/{path}"

    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "transfer-encoding")
    }

    body = await request.body()

    # Check if this is a streaming request
    is_stream = False
    if request.method == "POST" and body:
        try:
            import json
            payload = json.loads(body)
            is_stream = payload.get("stream", False)
        except (json.JSONDecodeError, AttributeError):
            pass

    if is_stream:
        # Streaming proxy — pass SSE chunks through directly
        req = client.build_request(
            method=request.method,
            url=url,
            headers=headers,
            content=body,
            params=request.query_params,
        )
        resp = await client.send(req, stream=True)

        async def stream_body():
            try:
                async for chunk in resp.aiter_bytes():
                    yield chunk
            finally:
                await resp.aclose()

        return StreamingResponse(
            stream_body(),
            status_code=resp.status_code,
            headers={
                k: v for k, v in resp.headers.items()
                if k.lower() not in ("transfer-encoding", "content-length")
            },
            media_type=resp.headers.get("content-type", "text/event-stream"),
        )

    # Non-streaming — standard proxy
    resp = await client.request(
        method=request.method,
        url=url,
        headers=headers,
        content=body,
        params=request.query_params,
    )

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
        media_type=resp.headers.get("content-type"),
    )


@app.get("/api/gateway-status")
async def gateway_status(request: Request):
    """Gateway status — tells Workspace/JettChat what features are available.

    Modes:
    - enhanced-hermes: Full Hermes v0.8.0+ gateway with all features
    - portable: Basic gateway, limited features
    - disconnected: Upstream unreachable
    """
    caps = getattr(request.app.state, "gateway_caps", {})

    # Live-check upstream
    upstream_ok = False
    try:
        resp = await request.app.state.hermes_client.get("/health", timeout=3.0)
        upstream_ok = resp.status_code == 200
    except Exception:
        pass

    mode = caps.get("mode", "disconnected") if upstream_ok else "disconnected"

    return {
        "status": "connected" if upstream_ok else "disconnected",
        "mode": mode,
        "model": caps.get("model", ""),
        "version": VERSION,
        "upstream_version": caps.get("version", ""),
        "features": {
            "sessions": True,
            "skills": True,
            "memory": True,
            "config": True,
            "tasks": True,
            "streaming": True,
            "chat_stream": True,
            "mpp": settings.mpp_enabled,
            "wallet": True,
            "tempo_billing": True,
            "mcp_servers": caps.get("mcp_servers", False),
            "sessions_api_upstream": caps.get("sessions_api", False),
        },
    }
