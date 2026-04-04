"""MPP (Machine Payments Protocol) integration for hermes-optx-api.

Provides pay-per-request gating via Tempo stablecoins and Stripe.
Bypassed when a valid API_KEY is provided in the Authorization header.
"""

from typing import Optional

from fastapi import HTTPException, Request

from hermes_optx_api.config import settings

# Lazy imports — pympp is optional
_mpp_server = None
_mpp_initialized = False


def _get_mpp_server():
    """Lazily initialize the MPP server. Returns None if MPP is disabled."""
    global _mpp_server, _mpp_initialized
    if _mpp_initialized:
        return _mpp_server

    _mpp_initialized = True

    if not settings.mpp_enabled or not settings.mpp_recipient:
        return None

    try:
        from mpp.server import Mpp
        from mpp.methods.tempo import tempo, ChargeIntent
        from mpp.methods.tempo._defaults import PATH_USD, TESTNET_CHAIN_ID, MAINNET_CHAIN_ID

        chain_id = MAINNET_CHAIN_ID if settings.mpp_network == "mainnet" else TESTNET_CHAIN_ID
        currency = PATH_USD  # pathUSD stablecoin

        # Optional: fee payer sponsors gas so clients don't need TEMPO for fees
        fee_payer = None
        if settings.mpp_fee_payer_key:
            from mpp.methods.tempo import TempoAccount
            fee_payer = TempoAccount.from_key(settings.mpp_fee_payer_key)

        _mpp_server = Mpp.create(
            method=tempo(
                chain_id=chain_id,
                currency=currency,
                recipient=settings.mpp_recipient,
                fee_payer=fee_payer,
                intents={"charge": ChargeIntent()},
            ),
        )
    except ImportError:
        import logging
        logging.getLogger(__name__).warning(
            "pympp not installed — MPP payment gating disabled. "
            "Install with: pip install 'pympp[tempo]'"
        )
        _mpp_server = None

    return _mpp_server


async def verify_payment(request: Request) -> Optional[dict]:
    """FastAPI dependency that enforces API key OR MPP payment.

    Priority:
    1. Valid API_KEY in Authorization header -> bypass payment (subscriber)
    2. MPP payment credential in Authorization header -> verify on-chain
    3. Neither -> return 402 Payment Required (if MPP enabled) or 401 (if key-only)

    Returns a dict with payer info, or None if auth is open.
    """
    auth_header = request.headers.get("Authorization", "")

    # --- API key check (always takes priority) ---
    if settings.api_key:
        if auth_header == f"Bearer {settings.api_key}":
            return {"auth": "api_key", "payer": "subscriber"}

    # --- MPP payment check ---
    server = _get_mpp_server()
    if server is not None:
        from mpp import Challenge

        amount = settings.mpp_amount_per_request
        chain_id_kwarg = {}
        if settings.mpp_network == "testnet":
            from mpp.methods.tempo._defaults import TESTNET_CHAIN_ID
            chain_id_kwarg["chain_id"] = TESTNET_CHAIN_ID

        fee_payer_kwarg = {}
        if settings.mpp_fee_payer_key:
            fee_payer_kwarg["fee_payer"] = True

        result = await server.charge(
            authorization=auth_header or None,
            amount=amount,
            **chain_id_kwarg,
            **fee_payer_kwarg,
        )

        if isinstance(result, Challenge):
            # Return 402 with WWW-Authenticate header so clients know how to pay
            raise HTTPException(
                status_code=402,
                detail="Payment required. Use Tempo CLI, mppx, or a compatible wallet.",
                headers={"WWW-Authenticate": result.to_www_authenticate(server.realm)},
            )

        credential, receipt = result
        return {
            "auth": "mpp",
            "payer": credential.source,
            "tx": receipt.reference,
        }

    # --- API key required but not provided ---
    if settings.api_key:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key.",
            headers={"WWW-Authenticate": 'Bearer realm="hermes-optx-api"'},
        )

    # --- Open access (no key, no MPP) ---
    return None
