# hermes-optx-api

**Enhanced API bridge for [Hermes Agent](https://github.com/NousResearch/hermes-agent) v0.7.0+ and [Hermes Workspace](https://github.com/outsourc-e/hermes-workspace)**

Hermes Workspace expects enhanced gateway endpoints (`/api/sessions`, `/api/skills`, `/api/memory`, `/api/config`) that the upstream NousResearch Hermes Agent doesn't yet provide. This bridge fills the gap — unlocking the full Workspace UI (Memory, Skills, Sessions, Settings) without forking the agent.

## The Problem

```
Hermes Workspace UI  ──→  /api/sessions  ──→  404 (upstream doesn't have it)
                     ──→  /api/skills    ──→  404
                     ──→  /api/memory    ──→  404
                     ──→  /api/config    ──→  404
```

## The Solution

```
Hermes Workspace UI  ──→  hermes-optx-api (proxy)  ──→  Hermes Agent gateway
                          ├── /api/sessions  → reads state.db
                          ├── /api/skills    → reads skill directory
                          ├── /api/memory    → pluggable backend
                          └── /api/config    → reads/writes config.yaml
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

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `HERMES_AGENT_URL` | `http://localhost:8642` | Hermes Agent gateway URL |
| `HERMES_HOME` | `~/.hermes` | Hermes config/data directory |
| `OPTX_API_PORT` | `8643` | Port for the bridge API |
| `OPTX_API_HOST` | `0.0.0.0` | Bind address |
| `MEMORY_BACKEND` | `holographic` | Memory backend: `holographic`, `sqlite`, `spacetimedb`, `custom` |
| `MEMORY_DB_URL` | `""` | Connection URL for external memory backends |
| `API_KEY` | `""` | Optional API key for authentication |
| `MPP_ENABLED` | `false` | Enable MPP pay-per-request gating |
| `MPP_RECIPIENT` | `""` | Your Tempo wallet address (0x...) |
| `MPP_AMOUNT` | `0.10` | pathUSD charged per request |
| `MPP_NETWORK` | `testnet` | `testnet` or `mainnet` |
| `MPP_FEE_PAYER_KEY` | `""` | Optional: sponsor gas for clients |

## MPP (Machine Payments Protocol)

Pay-per-request via [Tempo](https://tempo.xyz) stablecoins using the [MPP protocol](https://mpp.dev). Clients pay with `tempo` CLI, `mppx`, or any MPP-compatible wallet/agent.

### How It Works

```
Client                          hermes-optx-api                    Tempo Chain
  │                                   │                                │
  │─── GET /v1/chat/completions ────▶│                                │
  │                                   │ (no payment credential)       │
  │◀── 402 + WWW-Authenticate ──────│                                │
  │                                   │                                │
  │─── GET + Authorization: Payment ▶│                                │
  │                                   │── verify on-chain ───────────▶│
  │                                   │◀─ confirmed ─────────────────│
  │◀── 200 + Payment-Receipt ───────│                                │
```

### Setup

1. **Install with MPP support:**
   ```bash
   pip install -e ".[mpp]"
   ```

2. **Configure `.env`:**
   ```env
   MPP_ENABLED=true
   MPP_RECIPIENT=0x73cEA865A381c731Aa1c370381E714bCc4b75adf
   MPP_AMOUNT=0.10
   MPP_NETWORK=testnet
   ```

3. **Start the server:**
   ```bash
   hermes-optx-api
   ```

### Pricing

$0.10 pathUSD per API call covers:
- ~$0.001 MPP protocol fee
- ~$0.005–$0.02 xAI Grok 4.20 multi-agent cost (varies by tokens)
- ~$0.08 net margin

### Client Usage

**With Tempo CLI:**
```bash
tempo request -t -X POST \
  --json '{"model":"hermes","messages":[{"role":"user","content":"hello"}]}' \
  http://localhost:8643/v1/chat/completions
```

**With mppx (npm):**
```bash
npx mppx http://localhost:8643/v1/chat/completions \
  -X POST -H "Content-Type: application/json" \
  -d '{"model":"hermes","messages":[{"role":"user","content":"hello"}]}'
```

**With API key (bypass payment):**
```bash
curl http://localhost:8643/v1/chat/completions \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"hermes","messages":[{"role":"user","content":"hello"}]}'
```

### Auth Priority

| Credential | Result |
|------------|--------|
| Valid `API_KEY` in `Authorization: Bearer` | Access granted (subscriber, no payment) |
| Valid MPP payment credential | Access granted (pay-per-request) |
| No credential, MPP enabled | `402 Payment Required` with challenge |
| No credential, MPP disabled, key required | `401 Unauthorized` |
| No credential, nothing configured | Open access |

### Protected Routes

All routes except `/health` are gated when MPP is enabled:
- `/v1/*` (Hermes Agent proxy)
- `/api/sessions`, `/api/skills`, `/api/memory`, `/api/config`, `/api/tasks`

`/health` and `/api/gateway-status` are always free.

## Memory Backends

hermes-optx-api ships with a pluggable memory interface. Swap backends without changing the Workspace UI.

| Backend | Description | Config |
|---------|-------------|--------|
| `holographic` | Hermes built-in (SQLite + FTS5) | Default, reads `~/.hermes/memories/` |
| `sqlite` | Standalone SQLite with FTS5 | `MEMORY_DB_URL=sqlite:///path/to/db` |
| `spacetimedb` | SpacetimeDB edge database | `MEMORY_DB_URL=http://host:3000` + `SPACETIMEDB_DB=dbname` |
| `custom` | Bring your own backend | Implement `MemoryBackend` interface |

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
      - MEMORY_BACKEND=holographic
      - MPP_ENABLED=true
      - MPP_RECIPIENT=0x73cEA865A381c731Aa1c370381E714bCc4b75adf
      - MPP_AMOUNT=0.10
      - MPP_NETWORK=testnet
    volumes:
      - hermes-data:/home/hermes/.hermes:ro
    depends_on:
      - hermes-agent

  hermes-workspace:
    environment:
      - HERMES_API_URL=http://hermes-optx-api:8643
```

## Architecture

```
┌──────────────────┐     ┌──────────────────────┐     ┌──────────────────┐
│ Hermes Workspace │────▶│  hermes-optx-api     │────▶│  Hermes Agent    │
│ (React UI :5555) │     │  (FastAPI :8643)      │     │  (Gateway :8642) │
└──────────────────┘     │                      │     └──────────────────┘
                         │  Auth layer:          │
                         │  API_KEY → bypass     │
                         │  MPP 402 → pay $0.10  │
                         │                      │
                         │  Enhanced endpoints:  │
                         │  /api/sessions        │──▶ state.db (SQLite)
                         │  /api/skills          │──▶ skills directory
                         │  /api/memory          │──▶ pluggable backend
                         │  /api/config          │──▶ config.yaml
                         │  /api/jobs            │──▶ proxy to agent
                         │                      │
                         │  Passthrough:         │
                         │  /v1/*               │──▶ proxy to agent
                         │  /health             │──▶ free (no auth)
                         └──────────────────────┘
```

## License

MIT - See [LICENSE](LICENSE)

---

Built by [Jett Optical Technologies](https://jettoptics.ai)
