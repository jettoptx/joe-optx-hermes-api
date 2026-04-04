"""Tempo Wallet routes — metered HEDGEHOG API billing, escrow, and agent wallet status.

The Tempo Wallet is the OPTX payment and billing layer:
- **Metered API billing**: Every HEDGEHOG call (Grok inference) costs the caller tokens.
  xAI is paid for the inference, OPTX keeps $0.08 margin per call.
- **Task reward escrow**: Lock/release funds for task completion.
- **On-chain wallet status**: Track JOE's Solana + EVM balances.
- **Gaze-gated settlement**: AARON verification required for fund releases.
"""

import json
import time
import uuid
from enum import Enum
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()

# ---------------------------------------------------------------------------
# Constants — JOE's registered wallets
# ---------------------------------------------------------------------------

JOE_WALLETS = {
    "solana": {
        "address": "EFvgELE1Hb4PC5tbPTAe8v1uEDGee8nwYBMCU42bZRGk",
        "chain": "solana",
        "network": "mainnet-beta",
        "explorer": "https://solscan.io/account/EFvgELE1Hb4PC5tbPTAe8v1uEDGee8nwYBMCU42bZRGk",
    },
    "evm": {
        "address": "0xB06c9B91Fa4B460551880459dc35803DC7567b93",
        "chains": ["ethereum", "base"],
        "explorer": "https://basescan.org/address/0xB06c9B91Fa4B460551880459dc35803DC7567b93",
    },
}

SOLANA_RPC = "https://mainnet.helius-rpc.com/?api-key=98ca6456-20a8-4518-8393-1b9ee6c2b7f3"
SOLANA_DEVNET_RPC = "https://devnet.helius-rpc.com/?api-key=98ca6456-20a8-4518-8393-1b9ee6c2b7f3"

# Token mints
OPTX_MINT_DEVNET = "4r9WxVWBNMphYfSyGBuMFYRLsLEnzUNquJPnpFessXRH"
CSTB_MINT_DEVNET = "4waAimBGeubfVBp4MX9vRh7iTWxoR2RYYqiuChqCH7rX"
JTX_MINT_MAINNET = "9XpJiKEYzq5yDo5pJzRfjSRMPL2yPfDQXgiN7uYtBhUj"

# SpacetimeDB (shared with tasks.py)
STDB_URL = "http://100.85.183.16:3000"
STDB_DB = "optx-cortex"
STDB_TIMEOUT = 8.0

# AARON Router for gaze verification
AARON_URL = "http://100.85.183.16:8888"

# HEDGEHOG gateway for metered API calls
HEDGEHOG_URL = "http://100.85.183.16:8811"

# Tempo billing constants
TEMPO_MARGIN_USD = 0.08  # OPTX margin per HEDGEHOG API call
XAI_BASE_COST_PER_1K_TOKENS = 0.003  # Estimated xAI cost per 1K tokens (Grok 4.20)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class EscrowStatus(str, Enum):
    locked = "locked"
    released = "released"
    refunded = "refunded"
    expired = "expired"


class EscrowCreate(BaseModel):
    task_id: str
    amount: float = Field(gt=0)
    token: str = "OPTX"
    recipient: Optional[str] = None
    gaze_required: bool = True
    ttl_seconds: int = Field(default=86400, ge=60, le=604800)  # 1min to 7 days


class EscrowRelease(BaseModel):
    escrow_id: str
    recipient: str
    gaze_session_id: Optional[str] = None  # AARON session for verification


class TempoCallRequest(BaseModel):
    """Metered HEDGEHOG API call — caller pays xAI cost + $0.08 OPTX margin."""
    caller_id: str  # Wallet address or agent ID
    model: str = "grok-4.20-multi-agent-beta-0309"
    messages: list[dict] = Field(default_factory=list)
    prompt: Optional[str] = None  # Alternative to messages
    max_tokens: int = 2048
    temperature: float = 0.7
    tools: list[str] = Field(default_factory=list)  # web_search, x_search


# ---------------------------------------------------------------------------
# SpacetimeDB helpers
# ---------------------------------------------------------------------------

async def _stdb_sql(query: str) -> dict:
    async with httpx.AsyncClient(timeout=STDB_TIMEOUT) as client:
        resp = await client.post(
            f"{STDB_URL}/v1/database/{STDB_DB}/sql",
            content=query,
            headers={"Content-Type": "text/plain"},
        )
        if resp.status_code == 200:
            return resp.json()
        return {"error": resp.text}


async def _stdb_call(reducer: str, args: list) -> dict:
    async with httpx.AsyncClient(timeout=STDB_TIMEOUT) as client:
        resp = await client.post(
            f"{STDB_URL}/v1/database/{STDB_DB}/call/{reducer}",
            json=args,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code in (200, 204):
            return {"ok": True}
        return {"error": resp.text}


# ---------------------------------------------------------------------------
# Wallet status endpoints
# ---------------------------------------------------------------------------

@router.get("/wallet")
async def wallet_status():
    """Get JOE agent wallet overview — balances across all chains."""
    solana_balance = await _get_solana_balance(
        JOE_WALLETS["solana"]["address"], rpc=SOLANA_RPC
    )
    devnet_balance = await _get_solana_balance(
        JOE_WALLETS["solana"]["address"], rpc=SOLANA_DEVNET_RPC
    )

    # Get escrow totals
    escrow_stats = await _get_escrow_stats()

    return {
        "agent": "astrojoe",
        "wallets": {
            "solana": {
                **JOE_WALLETS["solana"],
                "balance_sol": solana_balance,
            },
            "solana_devnet": {
                "address": JOE_WALLETS["solana"]["address"],
                "chain": "solana",
                "network": "devnet",
                "balance_sol": devnet_balance,
            },
            "evm": JOE_WALLETS["evm"],
        },
        "escrow": escrow_stats,
        "tokens": {
            "OPTX": {"mint": OPTX_MINT_DEVNET, "network": "devnet"},
            "CSTB": {"mint": CSTB_MINT_DEVNET, "network": "devnet"},
            "JTX": {"mint": JTX_MINT_MAINNET, "network": "mainnet"},
        },
    }


@router.get("/wallet/balances")
async def wallet_balances():
    """Fetch live balances from RPC — Solana mainnet + devnet."""
    mainnet = await _get_solana_balance(
        JOE_WALLETS["solana"]["address"], rpc=SOLANA_RPC
    )
    devnet = await _get_solana_balance(
        JOE_WALLETS["solana"]["address"], rpc=SOLANA_DEVNET_RPC
    )

    # Token balances (devnet SPL)
    optx_balance = await _get_token_balance(
        JOE_WALLETS["solana"]["address"], OPTX_MINT_DEVNET, rpc=SOLANA_DEVNET_RPC
    )
    cstb_balance = await _get_token_balance(
        JOE_WALLETS["solana"]["address"], CSTB_MINT_DEVNET, rpc=SOLANA_DEVNET_RPC
    )

    return {
        "solana_mainnet": {"sol": mainnet},
        "solana_devnet": {"sol": devnet, "optx": optx_balance, "cstb": cstb_balance},
        "fetched_at": int(time.time()),
    }


# ---------------------------------------------------------------------------
# Tempo metered API — pay-per-call HEDGEHOG gateway
# ---------------------------------------------------------------------------

@router.post("/wallet/tempo/call")
async def tempo_call(req: TempoCallRequest):
    """Metered HEDGEHOG API call.

    Flow:
    1. Caller sends request with caller_id (wallet/agent)
    2. HEDGEHOG proxies to xAI Grok inference
    3. Tempo records the call: xAI cost + $0.08 OPTX margin
    4. Response returned to caller with billing receipt

    Pricing: xAI inference cost + $0.08 OPTX margin per call.
    """
    call_id = f"tempo-{str(uuid.uuid4())[:8]}"
    started_at = time.time()

    # Build messages
    messages = req.messages
    if not messages and req.prompt:
        messages = [{"role": "user", "content": req.prompt}]

    if not messages:
        raise HTTPException(status_code=400, detail="Provide messages or prompt")

    # Route through HEDGEHOG gateway
    hedgehog_payload = {
        "model": req.model,
        "messages": messages,
        "max_tokens": req.max_tokens,
        "temperature": req.temperature,
    }

    # Add tools for multi-agent models
    if req.tools:
        hedgehog_payload["tools"] = [{"type": t} for t in req.tools]

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{HEDGEHOG_URL}/v1/chat/completions",
                json=hedgehog_payload,
                headers={
                    "Authorization": "Bearer sk-hedgehog-local",
                    "Content-Type": "application/json",
                },
            )
            elapsed = time.time() - started_at

            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"HEDGEHOG returned {resp.status_code}: {resp.text[:200]}",
                )

            result = resp.json()

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="HEDGEHOG gateway timeout")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"HEDGEHOG error: {str(e)[:200]}")

    # Extract token usage
    usage = result.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)

    # Calculate billing
    xai_cost = (total_tokens / 1000) * XAI_BASE_COST_PER_1K_TOKENS
    tempo_total = xai_cost + TEMPO_MARGIN_USD

    # Record billing in SpacetimeDB
    billing_entry = {
        "call_id": call_id,
        "caller_id": req.caller_id,
        "model": req.model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "xai_cost_usd": round(xai_cost, 6),
        "tempo_margin_usd": TEMPO_MARGIN_USD,
        "total_cost_usd": round(tempo_total, 6),
        "latency_ms": round(elapsed * 1000, 1),
        "created_at": int(time.time()),
    }

    await _stdb_call("store_memory", [
        "tempo_billing",
        f"tempo:{call_id}",
        json.dumps(billing_entry),
        "hermes-optx-api",
        req.caller_id,
        5,
        "",
    ])

    # Return response with billing receipt
    return {
        "call_id": call_id,
        "result": result.get("choices", [{}])[0].get("message", {}),
        "usage": usage,
        "billing": {
            "xai_cost_usd": round(xai_cost, 6),
            "optx_margin_usd": TEMPO_MARGIN_USD,
            "total_usd": round(tempo_total, 6),
            "token": "OPTX",
        },
        "latency_ms": round(elapsed * 1000, 1),
    }


@router.get("/wallet/tempo/usage")
async def tempo_usage(caller_id: Optional[str] = None, limit: int = 50):
    """Get Tempo API usage history and billing totals."""
    result = await _stdb_sql(
        "SELECT key, value FROM memory_entry WHERE category = 'tempo_billing'"
    )

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    rows = result if isinstance(result, list) else result.get("rows", [])
    calls = []
    totals = {
        "total_calls": 0,
        "total_tokens": 0,
        "total_xai_cost_usd": 0.0,
        "total_margin_usd": 0.0,
        "total_billed_usd": 0.0,
    }

    for row in rows:
        try:
            data = json.loads(row.get("value", "{}"))
            if caller_id and data.get("caller_id") != caller_id:
                continue

            totals["total_calls"] += 1
            totals["total_tokens"] += data.get("total_tokens", 0)
            totals["total_xai_cost_usd"] += data.get("xai_cost_usd", 0.0)
            totals["total_margin_usd"] += data.get("tempo_margin_usd", 0.0)
            totals["total_billed_usd"] += data.get("total_cost_usd", 0.0)

            calls.append(data)
        except (json.JSONDecodeError, AttributeError):
            continue

    # Round totals
    for k in totals:
        if isinstance(totals[k], float):
            totals[k] = round(totals[k], 6)

    # Sort by created_at descending, limit
    calls.sort(key=lambda c: c.get("created_at", 0), reverse=True)

    return {
        "calls": calls[:limit],
        "totals": totals,
        "caller_id": caller_id,
    }


@router.get("/wallet/tempo/pricing")
async def tempo_pricing():
    """Get current Tempo API pricing."""
    return {
        "model": "grok-4.20-multi-agent-beta-0309",
        "pricing": {
            "per_1k_tokens_usd": XAI_BASE_COST_PER_1K_TOKENS,
            "optx_margin_per_call_usd": TEMPO_MARGIN_USD,
            "payment_token": "OPTX",
            "payment_chain": "solana",
            "payment_mint": OPTX_MINT_DEVNET,
        },
        "supported_models": [
            "grok-4.20-multi-agent-beta-0309",
            "grok-4.20-0309-reasoning",
            "grok-4-1-fast-reasoning",
        ],
        "supported_tools": ["web_search", "x_search"],
    }


# ---------------------------------------------------------------------------
# Escrow endpoints — task reward lifecycle
# ---------------------------------------------------------------------------

@router.post("/wallet/escrow")
async def create_escrow(escrow: EscrowCreate):
    """Lock funds in escrow for a task reward.

    Escrow is stored in SpacetimeDB as a memory entry (category: escrow).
    Funds are logically locked — on-chain settlement happens at release.
    """
    escrow_id = f"esc-{str(uuid.uuid4())[:8]}"
    now = int(time.time())

    entry = {
        "escrow_id": escrow_id,
        "task_id": escrow.task_id,
        "amount": escrow.amount,
        "token": escrow.token,
        "status": EscrowStatus.locked.value,
        "recipient": escrow.recipient,
        "gaze_required": escrow.gaze_required,
        "created_at": now,
        "expires_at": now + escrow.ttl_seconds,
        "released_at": None,
    }

    result = await _stdb_call("store_memory", [
        "escrow",
        f"escrow:{escrow_id}",
        json.dumps(entry),
        "hermes-optx-api",
        "tempo-wallet",
        8,  # High importance
        "",
    ])

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    return entry


@router.get("/wallet/escrow")
async def list_escrows(status: Optional[str] = None, task_id: Optional[str] = None):
    """List all escrow entries, optionally filtered by status or task."""
    result = await _stdb_sql(
        "SELECT key, value FROM memory_entry WHERE category = 'escrow'"
    )

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    rows = result if isinstance(result, list) else result.get("rows", [])
    escrows = []

    for row in rows:
        try:
            data = json.loads(row.get("value", "{}"))
            if status and data.get("status") != status:
                continue
            if task_id and data.get("task_id") != task_id:
                continue

            # Check expiry
            if (
                data.get("status") == EscrowStatus.locked.value
                and data.get("expires_at", 0) < int(time.time())
            ):
                data["status"] = EscrowStatus.expired.value

            escrows.append(data)
        except (json.JSONDecodeError, AttributeError):
            continue

    return {"escrows": escrows, "total": len(escrows)}


@router.get("/wallet/escrow/{escrow_id}")
async def get_escrow(escrow_id: str):
    """Get a specific escrow entry."""
    result = await _stdb_sql(
        f"SELECT value FROM memory_entry WHERE key = 'escrow:{escrow_id}'"
    )

    rows = result if isinstance(result, list) else result.get("rows", [])
    if not rows:
        raise HTTPException(status_code=404, detail="Escrow not found")

    data = json.loads(rows[0].get("value", "{}"))

    # Check expiry
    if (
        data.get("status") == EscrowStatus.locked.value
        and data.get("expires_at", 0) < int(time.time())
    ):
        data["status"] = EscrowStatus.expired.value

    return data


@router.post("/wallet/escrow/release")
async def release_escrow(release: EscrowRelease):
    """Release escrow funds to recipient. Requires gaze verification if flagged."""
    result = await _stdb_sql(
        f"SELECT value FROM memory_entry WHERE key = 'escrow:{release.escrow_id}'"
    )

    rows = result if isinstance(result, list) else result.get("rows", [])
    if not rows:
        raise HTTPException(status_code=404, detail="Escrow not found")

    data = json.loads(rows[0].get("value", "{}"))

    if data.get("status") != EscrowStatus.locked.value:
        raise HTTPException(
            status_code=409,
            detail=f"Escrow is {data.get('status')}, cannot release",
        )

    # Check expiry
    if data.get("expires_at", 0) < int(time.time()):
        data["status"] = EscrowStatus.expired.value
        await _update_escrow(release.escrow_id, data)
        raise HTTPException(status_code=410, detail="Escrow has expired")

    # Gaze verification via AARON Router
    if data.get("gaze_required"):
        gaze_ok = await _verify_gaze(release.gaze_session_id)
        if not gaze_ok:
            raise HTTPException(
                status_code=403,
                detail="Gaze verification required — provide valid gaze_session_id from AARON",
            )

    # Release
    data["status"] = EscrowStatus.released.value
    data["recipient"] = release.recipient
    data["released_at"] = int(time.time())

    await _update_escrow(release.escrow_id, data)

    return {
        "escrow_id": release.escrow_id,
        "status": "released",
        "amount": data["amount"],
        "token": data["token"],
        "recipient": release.recipient,
        "settlement": "pending_on_chain",
    }


@router.post("/wallet/escrow/{escrow_id}/refund")
async def refund_escrow(escrow_id: str):
    """Refund a locked escrow back to the creator."""
    result = await _stdb_sql(
        f"SELECT value FROM memory_entry WHERE key = 'escrow:{escrow_id}'"
    )

    rows = result if isinstance(result, list) else result.get("rows", [])
    if not rows:
        raise HTTPException(status_code=404, detail="Escrow not found")

    data = json.loads(rows[0].get("value", "{}"))

    if data.get("status") not in (EscrowStatus.locked.value, EscrowStatus.expired.value):
        raise HTTPException(
            status_code=409,
            detail=f"Escrow is {data.get('status')}, cannot refund",
        )

    data["status"] = EscrowStatus.refunded.value
    data["refunded_at"] = int(time.time())

    await _update_escrow(escrow_id, data)

    return {"escrow_id": escrow_id, "status": "refunded", "amount": data["amount"]}


# ---------------------------------------------------------------------------
# Helpers — Solana RPC
# ---------------------------------------------------------------------------

async def _get_solana_balance(address: str, rpc: str = SOLANA_RPC) -> float:
    """Fetch SOL balance from Solana RPC."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                rpc,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getBalance",
                    "params": [address],
                },
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                lamports = data.get("result", {}).get("value", 0)
                return lamports / 1_000_000_000  # Convert lamports to SOL
    except Exception:
        pass
    return 0.0


async def _get_token_balance(
    owner: str, mint: str, rpc: str = SOLANA_DEVNET_RPC
) -> float:
    """Fetch SPL token balance for a specific mint."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                rpc,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenAccountsByOwner",
                    "params": [
                        owner,
                        {"mint": mint},
                        {"encoding": "jsonParsed"},
                    ],
                },
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                accounts = data.get("result", {}).get("value", [])
                total = 0.0
                for acct in accounts:
                    info = acct.get("account", {}).get("data", {}).get("parsed", {})
                    token_amount = info.get("info", {}).get("tokenAmount", {})
                    total += float(token_amount.get("uiAmountString", "0"))
                return total
    except Exception:
        pass
    return 0.0


# ---------------------------------------------------------------------------
# Helpers — AARON gaze verification
# ---------------------------------------------------------------------------

async def _verify_gaze(session_id: Optional[str]) -> bool:
    """Verify gaze attestation via AARON Router."""
    if not session_id:
        return False

    try:
        async with httpx.AsyncClient(timeout=STDB_TIMEOUT) as client:
            resp = await client.get(
                f"{AARON_URL}/session/{session_id}",
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                # Session must be verified and recent (within 5 minutes)
                if data.get("verified"):
                    verified_at = data.get("verified_at", 0)
                    if int(time.time()) - verified_at < 300:
                        return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Helpers — SpacetimeDB escrow persistence
# ---------------------------------------------------------------------------

async def _update_escrow(escrow_id: str, data: dict):
    """Update an escrow entry in SpacetimeDB."""
    await _stdb_call("store_memory", [
        "escrow",
        f"escrow:{escrow_id}",
        json.dumps(data),
        "hermes-optx-api",
        "tempo-wallet",
        8,
        "",
    ])


async def _get_escrow_stats() -> dict:
    """Get aggregate escrow statistics."""
    result = await _stdb_sql(
        "SELECT value FROM memory_entry WHERE category = 'escrow'"
    )
    rows = result if isinstance(result, list) else result.get("rows", [])

    stats = {"locked": 0.0, "released": 0.0, "refunded": 0.0, "expired": 0.0, "count": 0}
    now = int(time.time())

    for row in rows:
        try:
            data = json.loads(row.get("value", "{}"))
            amount = data.get("amount", 0.0)
            status = data.get("status", "locked")

            # Auto-expire
            if status == "locked" and data.get("expires_at", 0) < now:
                status = "expired"

            stats[status] = stats.get(status, 0.0) + amount
            stats["count"] += 1
        except (json.JSONDecodeError, AttributeError):
            continue

    return stats
