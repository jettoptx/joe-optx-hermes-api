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

## Memory Backends

hermes-optx-api ships with a pluggable memory interface. Swap backends without changing the Workspace UI.

| Backend | Description | Config |
|---------|-------------|--------|
| `holographic` | Hermes built-in (SQLite + FTS5) | Default, reads `~/.hermes/memories/` |
| `sqlite` | Standalone SQLite with FTS5 | `MEMORY_DB_URL=sqlite:///path/to/db` |
| `spacetimedb` | SpacetimeDB edge database | `MEMORY_DB_URL=http://host:3000` + `SPACETIMEDB_DB=dbname` |
| `custom` | Bring your own backend | Implement `MemoryBackend` interface |

### Custom Memory Backend

```python
from hermes_optx_api.memory.base import MemoryBackend

class MyBackend(MemoryBackend):
    async def store(self, content, metadata=None): ...
    async def recall(self, query, limit=10): ...
    async def search(self, query, filters=None): ...
    async def delete(self, memory_id): ...
    async def list_all(self, limit=100, offset=0): ...
```

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
    volumes:
      - hermes-data:/home/hermes/.hermes:ro
    depends_on:
      - hermes-agent

  hermes-workspace:
    environment:
      - HERMES_API_URL=http://hermes-optx-api:8643  # Point workspace here instead
```

## Architecture

```
┌──────────────────┐     ┌──────────────────────┐     ┌──────────────────┐
│ Hermes Workspace │────▶│  hermes-optx-api     │────▶│  Hermes Agent    │
│ (React UI :5555) │     │  (FastAPI :8643)      │     │  (Gateway :8642) │
└──────────────────┘     │                      │     └──────────────────┘
                         │  Enhanced endpoints:  │
                         │  /api/sessions        │──▶ state.db (SQLite)
                         │  /api/skills          │──▶ skills directory
                         │  /api/memory          │──▶ pluggable backend
                         │  /api/config          │──▶ config.yaml
                         │  /api/jobs            │──▶ proxy to agent
                         │                      │
                         │  Passthrough:         │
                         │  /v1/*               │──▶ proxy to agent
                         │  /health             │──▶ proxy to agent
                         └──────────────────────┘
```

## Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT - See [LICENSE](LICENSE)

---

Built by [Jett Optical Technologies](https://jettoptics.ai)
