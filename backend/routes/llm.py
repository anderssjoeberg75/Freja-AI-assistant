"""LLM provider status route.

Backs the admin portal's provider indicator: which engine is selected, whether the
backend can actually reach it, and what the other one looks like.
"""

from fastapi import APIRouter, Query

from backend.services import llm_client

router = APIRouter()


@router.get("/api/system/llm-status")
async def get_llm_status(refresh: bool = Query(False, description="Bypass the short status cache")):
    """Returns the selected LLM provider, per-provider reachability, and which provider
    would serve a request right now. `refresh=true` forces a fresh probe - the portal uses
    it right after settings are saved, when the cached answer is known to be stale.

    The cache lives in `llm_client` rather than here, so the portal's polling and the chat
    proxy's per-request lookup share one probe instead of each paying for their own."""
    return await llm_client.get_provider_status(
        max_age=0 if refresh else llm_client.STATUS_CACHE_TTL_SECONDS
    )
