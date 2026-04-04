"""FastAPI application — Enhanced API bridge for Hermes Agent."""

from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from hermes_optx_api.config import settings
from hermes_optx_api.payments import verify_payment
from hermes_optx_api.routes import sessions, skills, memory, config, tasks


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown — create shared HTTP client for proxying."""
    app.state.hermes_client = httpx.AsyncClient(
        base_url=settings.hermes_agent_url,
        timeout=30.0,
    )
    yield
    await app.state.hermes_client.aclose()


app = FastAPI(
    title="hermes-optx-api",
    description="Enhanced API bridge for Hermes Agent v0.7.0+ and Hermes Workspace",
    version="0.2.0",
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
        "version": "0.2.0",
        "upstream": {
            "url": settings.hermes_agent_url,
            "connected": upstream_ok,
        },
        "memory_backend": settings.memory_backend,
        "mpp_enabled": settings.mpp_enabled,
    }


@app.api_route(
    "/v1/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    dependencies=[Depends(verify_payment)],
)
async def proxy_v1(request: Request, path: str):
    """Passthrough proxy — forward all /v1/* requests to Hermes Agent."""
    client: httpx.AsyncClient = request.app.state.hermes_client
    url = f"/v1/{path}"

    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "transfer-encoding")
    }

    body = await request.body()

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
    """Gateway status — tells Workspace we support enhanced mode."""
    upstream_ok = False
    model = ""
    try:
        resp = await request.app.state.hermes_client.get("/health")
        upstream_ok = resp.status_code == 200
        models_resp = await request.app.state.hermes_client.get("/v1/models")
        if models_resp.status_code == 200:
            data = models_resp.json()
            if data.get("data"):
                model = data["data"][0].get("id", "")
    except Exception:
        pass

    return {
        "status": "connected" if upstream_ok else "disconnected",
        "mode": "enhanced-hermes",
        "model": model,
        "features": {
            "sessions": True,
            "skills": True,
            "memory": True,
            "config": True,
            "jobs": True,
            "streaming": True,
            "mpp": settings.mpp_enabled,
        },
    }
