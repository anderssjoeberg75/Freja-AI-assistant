"""Meta Instagram Graph API routes using FastAPI."""

import httpx
import secrets
import time
import urllib.parse
from backend.services.http_client import shared_client
import logging
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from backend.config import GRAPH_API_VERSION, GRAPH_BASE_URL
from backend.database import get_api_key, set_api_key

router = APIRouter()
logger = logging.getLogger("freja.instagram.router")

# CSRF protection for the OAuth dance: /auth mints a nonce and /callback must see the same
# one back. Without this, an attacker who completes their OWN Facebook login (getting a
# valid `code` for their own account) can trick the admin's browser into hitting
# /api/instagram/callback?code=<attacker_code> - the callback is necessarily auth-exempt
# (Meta redirects the browser directly, with no way to attach our access token), so nothing
# else stopped that code from being exchanged and silently re-pointing every automated
# Instagram publish/reply action at an account the admin doesn't control. A single in-memory
# slot is enough - this app has exactly one admin and one OAuth flow in flight at a time.
_PENDING_OAUTH_STATE = {"value": None, "expires_at": 0.0}
_OAUTH_STATE_TTL_SECONDS = 600

from backend.origins import is_trusted_host


def get_oauth_config(request: Request):
    """Retrieves client ID, secret and callback redirect URL."""
    base_url = str(request.base_url).rstrip("/")
    hostname = request.url.hostname or ""
    
    # If accessing via local IP or loopback, force redirect_uri to localhost to satisfy Meta's HTTPS whitelist exemption
    if is_trusted_host(hostname):
        port = request.url.port or 8000
        redirect_uri = f"http://localhost:{port}/api/instagram/callback"
    else:
        redirect_uri = f"{base_url}/api/instagram/callback"
    
    client_id = get_api_key("freja_instagram_client_id") or get_api_key("freja_facebook_client_id") or ""
    client_secret = get_api_key("freja_instagram_client_secret") or get_api_key("freja_facebook_client_secret") or ""
    
    return client_id, client_secret, redirect_uri

@router.get("/api/instagram/auth")
async def instagram_auth(request: Request):
    """Redirects user to the Facebook/Meta Login for Business dialog."""
    client_id, _, redirect_uri = get_oauth_config(request)
    if not client_id:
        raise HTTPException(
            status_code=400,
            detail="Instagram/Facebook Client ID is missing. Configure it in Settings."
        )

    scopes = [
        "instagram_basic",
        "instagram_content_publish",
        "instagram_manage_comments",
        "pages_read_engagement",
        "pages_show_list"
    ]
    scope_str = ",".join(scopes)

    state = secrets.token_urlsafe(24)
    _PENDING_OAUTH_STATE["value"] = state
    _PENDING_OAUTH_STATE["expires_at"] = time.time() + _OAUTH_STATE_TTL_SECONDS

    auth_url = (
        f"https://www.facebook.com/{GRAPH_API_VERSION}/dialog/oauth?"
        f"client_id={client_id}&"
        f"redirect_uri={redirect_uri}&"
        f"scope={scope_str}&"
        f"state={state}&"
        f"response_type=code"
    )
    return RedirectResponse(auth_url)

@router.get("/api/instagram/callback")
async def instagram_callback(request: Request, code: str = Query(None), state: str = Query(None)):
    """Handles the OAuth authorization code, exchanges it for tokens, and queries the user's accounts."""
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code.")

    expected_state = _PENDING_OAUTH_STATE["value"]
    state_expired = time.time() > _PENDING_OAUTH_STATE["expires_at"]
    _PENDING_OAUTH_STATE["value"] = None  # single-use, regardless of outcome
    if not expected_state or state_expired or not secrets.compare_digest(state or "", expected_state):
        logger.error("Instagram OAuth callback rejected: missing or mismatched state parameter.")
        return RedirectResponse("/admin?error=" + urllib.parse.quote("Invalid or expired OAuth state - please try connecting again."))

    client_id, client_secret, redirect_uri = get_oauth_config(request)
    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Missing OAuth client credentials.")

    try:
        # Step 1: Exchange code for short-lived user token
        token_url = f"{GRAPH_BASE_URL}/oauth/access_token"
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "client_secret": client_secret,
            "code": code
        }
        
        async with shared_client() as client:
            resp = await client.get(token_url, params=params, timeout=15.0)
            if resp.status_code != 200:
                logger.error(f"Failed short-lived token exchange: {resp.text}")
                return RedirectResponse("/admin?error=" + urllib.parse.quote(resp.text))
                
            short_token = resp.json().get("access_token")
            
            # Step 2: Exchange short-lived token for long-lived user token (60 days)
            long_token_url = f"{GRAPH_BASE_URL}/oauth/access_token"
            long_params = {
                "grant_type": "fb_exchange_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "fb_exchange_token": short_token
            }
            
            long_resp = await client.get(long_token_url, params=long_params, timeout=15.0)
            if long_resp.status_code != 200:
                logger.error(f"Failed long-lived token exchange: {long_resp.text}")
                return RedirectResponse("/admin?error=" + urllib.parse.quote(long_resp.text))
                
            long_token = long_resp.json().get("access_token")
            
            # Step 3: Query linked Pages and retrieve the linked Instagram Business/Creator Account ID
            accounts_url = f"{GRAPH_BASE_URL}/me/accounts"
            accounts_params = {
                "fields": "instagram_business_account{id,username},name",
                "access_token": long_token
            }
            
            accounts_resp = await client.get(accounts_url, params=accounts_params, timeout=15.0)
            if accounts_resp.status_code != 200:
                logger.error(f"Failed to query user's accounts: {accounts_resp.text}")
                return RedirectResponse("/admin?error=" + urllib.parse.quote(accounts_resp.text))
                
            pages = accounts_resp.json().get("data", [])
            ig_id = ""
            ig_username = ""
            
            for p in pages:
                ig_account = p.get("instagram_business_account")
                if ig_account:
                    ig_id = ig_account.get("id", "")
                    ig_username = ig_account.get("username", "")
                    break
                    
            if not ig_id:
                logger.error(f"No linked Instagram Professional account was found: {accounts_resp.text}")
                return RedirectResponse("/admin?error=no_instagram_account_found")
                
            # Save credentials in database
            set_api_key("freja_instagram_access_token", long_token)
            set_api_key("freja_instagram_business_account_id", ig_id)
            set_api_key("freja_instagram_username", ig_username)
            
            logger.info(f"Successfully integrated Instagram account '{ig_username}' (ID: {ig_id})")
            return RedirectResponse("/admin?status=instagram_linked")
            
    except Exception as e:
        logger.exception("Failed to complete Instagram OAuth callback")
        return RedirectResponse("/admin?error=" + urllib.parse.quote(str(e)))

@router.get("/api/instagram/status")
async def instagram_status():
    """Returns the current connection status and username of the linked account."""
    token = get_api_key("freja_instagram_access_token")
    ig_id = get_api_key("freja_instagram_business_account_id")
    ig_username = get_api_key("freja_instagram_username") or "Kopplad"
    
    if token and ig_id:
        return {
            "status": "connected",
            "username": ig_username,
            "instagram_business_account_id": ig_id
        }
    return {"status": "disconnected"}

@router.delete("/api/instagram/status")
async def instagram_disconnect():
    """Disconnects the Instagram integration by wiping the saved credentials."""
    set_api_key("freja_instagram_access_token", "")
    set_api_key("freja_instagram_business_account_id", "")
    set_api_key("freja_instagram_username", "")
    return {"status": "disconnected"}
