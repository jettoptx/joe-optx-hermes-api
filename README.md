# hermes-optx-api

**Enhanced API bridge for [Hermes Agent](https://github.com/NousResearch/hermes-agent) v0.8.0+ — SSE streaming, memory, tasks, wallet**

> **[Read the full OPTX documentation](https://optxspace.dev/docs/getting-started/what-is-optx)**

Hermes Workspace and JettChat expect enhanced gateway endpoints (`/api/sessions`, `/api/skills`, `/api/memory`, `/api/config`, `/api/sessions/:id/chat/stream`) that the upstream NousResearch Hermes Agent doesn't yet provide. This bridge fills the gap — unlocking SSE streaming chat, pluggable memory backends, task orchestration, and Tempo wallet billing without forking the agent.

Part of the **[OPTX ecosystem](https://optxspace.dev)** — privacy-preserving spatial encryption for the agentic web.

## What's New in v0.3.0

- **SSE Streaming Chat** — `/api/sessions/{id}/chat/stream` and `/api/sessions/new/chat/stream` for real-time token streaming
- **Streaming /v1/* proxy** — auto-detects `stream: true` and passes SSE chunks through
- **Gateway capability probing** — auto-detects enhanced-hermes / portable / disconnected mode at startup
- **SpacetimeDB memory backend** — reads/writes to optx-cortex on Jetson edge node
- **Updated model routing** — Grok 4.20 multi-agent + reasoning models
- **JettChat-ready** — new session + stream in one call, SSE event format for web frontends

## Architecture

```
┌──────────────────┐     ┌──────────────────────┐     ┌──────────────────┐
│  JettChat / UI   │────▶│  hermes-optx-api     │────▶│  Hermes Agent    │
│  (Web :3000)     │     │  (FastAPI :8643)      │     │  (Gateway :8642) │
└──────────────────┘     │                      │     └──────────────────┘
                         │  SSE Streaming:       │
                         │  /api/sessions/*/chat │──▶ stream tokens via SSE
                         │  /api/chat/completions│──▶ streaming proxy
                         │                      │
                         │  Enhanced endpoints:  │
                         │  /api/sessions        │──▶ state.db (SQLite)
                         │  /api/skills          │──▶ skills directory
                         │  /api/memory          │──▶ pluggable backend
                         │  /api/config          │──▶ config.yaml
                         │  /api/tasks           │──▶ SpacetimeDB DAG
                         │  /api/wallet          │──▶ Tempo billing
                         │                      │
                         │  Auth layer:          │
                         │  API_KEY → bypass     │
                         │  MPP 402 → pay $0.10  │
                         │                      │
                         │  Passthrough:         │
                         │  /v1/*  (+ streaming) │──▶ proxy to agent
                         │  /health              │──▶ free (no auth)
                         └──────────────────────┘
```

## Quick Start

```bash
# With Docker (recommended)
docker compose up -d

# Or standalone
pip install -e .
hermes-optx-api --hermes-url http://localhost:8642 --port 8643

# With MPP payment support
pip install -e ".[mpp]"
```

## SSE Streaming Chat

**New session + stream (JettChat pattern):**
```bash
curl -N -X POST http://localhost:8643/api/sessions/new/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "What is OPTX?"}'
```

**Stream into existing session:**
```bash
curl -N -X POST http://localhost:8643/api/sessions/abc123/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "Tell me more"}'
```

**SSE Events:**
| Event | Data | Description |
|-------|------|-------------|
| `session` | `{session_id, status}` | Session created/opened |
| `content` | `{content, session_id}` | Token chunk |
| `done` | `{session_id, status}` | Stream complete |
| `error` | `{error, session_id}` | Error occurred |

## Gateway Capabilities

The bridge probes the upstream Hermes Agent at startup and exposes capabilities at `/api/gateway-status`:

```json
{
  "status": "connected",
  "mode": "enhanced-hermes",
  "model": "grok-4.20-0309-reasoning",
  "features": {
    "sessions": true,
    "streaming": true,
    "chat_stream": true,
    "memory": true,
    "tasks": true,
    "wallet": true,
    "mcp_servers": true
  }
}
```

Modes:
- **enhanced-hermes** — Full Hermes v0.8.0+ gateway with all features
- **portable** — Basic gateway, limited feature set
- **disconnected** — Upstream unreachable, local-only features still work

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `HERMES_AGENT_URL` | `http://localhost:8642` | Hermes Agent gateway URL |
| `HERMES_HOME` | `~/.hermes` | Hermes config/data directory |
| `OPTX_API_PORT` | `8643` | Port for the bridge API |
| `OPTX_API_HOST` | `0.0.0.0` | Bind address |
| `MEMORY_BACKEND` | `holographic` | Memory backend: `holographic`, `sqlite`, `spacetimedb` |
| `MEMORY_DB_URL` | `""` | Connection URL for external memory backends |
| `SPACETIMEDB_DB` | `""` | SpacetimeDB database name (for spacetimedb backend) |
| `API_KEY` | `""` | Optional API key for authentication |
| `MPP_ENABLED` | `false` | Enable MPP pay-per-request gating |
| `MPP_RECIPIENT` | `""` | Your Tempo wallet address (0x...) |
| `MPP_AMOUNT` | `0.10` | pathUSD charged per request |
| `MPP_NETWORK` | `testnet` | `testnet` or `mainnet` |

## Memory Backends

| Backend | Description | Config |
|---------|-------------|--------|
| `holographic` | Hermes built-in (SQLite + FTS5) | Default, reads `~/.hermes/memories/` |
| `sqlite` | Standalone SQLite with FTS5 | `MEMORY_DB_URL=sqlite:///path/to/db` |
| `spacetimedb` | SpacetimeDB edge database | `MEMORY_DB_URL=http://host:3000` + `SPACETIMEDB_DB=optx-cortex` |

## Docker Compose Integration

Add to your existing Hermes stack:

```yaml
services:
  hermes-optx-api:
    image: ghcr.io/jettoptx/hermes-optx-api:latest
    ports:
      - "8643:8643"
    environment:
      - HERMES_AGENT_URL=http://hermes-agent:8642
      - HERMES_HOME=/home/hermes/.hermes
      - MEMORY_BACKEND=spacetimedb
      - MEMORY_DB_URL=http://100.85.183.16:3000
      - SPACETIMEDB_DB=optx-cortex
    volumes:
      - hermes-data:/home/hermes/.hermes:ro
    depends_on:
      - hermes-agent
```

## MPP (Machine Payments Protocol)

Pay-per-request via [Tempo](https://tempo.xyz) stablecoins using the [MPP protocol](https://mpp.dev).

| Credential | Result |
|------------|--------|
| Valid `API_KEY` in `Authorization: Bearer` | Access granted (subscriber, no payment) |
| Valid MPP payment credential | Access granted (pay-per-request) |
| No credential, MPP enabled | `402 Payment Required` with challenge |
| No credential, MPP disabled, key required | `401 Unauthorized` |
| No credential, nothing configured | Open access |

All routes except `/health` and `/api/gateway-status` are gated when MPP is enabled.

## License

MIT - See [LICENSE](LICENSE)

---

Built by [OPTX](https://optxspace.dev) · [Jett Optical Technologies](https://jettoptics.ai)

<!-- Luke 18:31 -->
