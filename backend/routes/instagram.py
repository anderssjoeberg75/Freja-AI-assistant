"""Meta Instagram Graph API routes using FastAPI."""

import httpx
import logging
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from backend.database import get_api_key, set_api_key

router = APIRouter()
logger = logging.getLogger("freja.instagram.router")

def get_oauth_config(request: Request):
    """Retrieves client ID, secret and callback redirect URL."""
    base_url = str(request.base_url).rstrip("/")
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
    
    auth_url = (
        f"https://www.facebook.com/v19.0/dialog/oauth?"
        f"client_id={client_id}&"
        f"redirect_uri={redirect_uri}&"
        f"scope={scope_str}&"
        f"response_type=code"
    )
    return RedirectResponse(auth_url)

@router.get("/api/instagram/callback")
async def instagram_callback(request: Request, code: str = Query(None)):
    """Handles the OAuth authorization code, exchanges it for tokens, and queries the user's accounts."""
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code.")
        
    client_id, client_secret, redirect_uri = get_oauth_config(request)
    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Missing OAuth client credentials.")

    try:
        # Step 1: Exchange code for short-lived user token
        token_url = "https://graph.facebook.com/v19.0/oauth/access_token"
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "client_secret": client_secret,
            "code": code
        }
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(token_url, params=params, timeout=15.0)
            if resp.status_code != 200:
                logger.error(f"Failed short-lived token exchange: {resp.text}")
                return RedirectResponse(f"/admin?error={resp.text}")
                
            short_token = resp.json().get("access_token")
            
            # Step 2: Exchange short-lived token for long-lived user token (60 days)
            long_token_url = "https://graph.facebook.com/v19.0/oauth/access_token"
            long_params = {
                "grant_type": "fb_exchange_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "fb_exchange_token": short_token
            }
            
            long_resp = await client.get(long_token_url, params=long_params, timeout=15.0)
            if long_resp.status_code != 200:
                logger.error(f"Failed long-lived token exchange: {long_resp.text}")
                return RedirectResponse(f"/admin?error={long_resp.text}")
                
            long_token = long_resp.json().get("access_token")
            
            # Step 3: Query linked Pages and retrieve the linked Instagram Business/Creator Account ID
            accounts_url = "https://graph.facebook.com/v19.0/me/accounts"
            accounts_params = {
                "fields": "instagram_business_account{id,username},name",
                "access_token": long_token
            }
            
            accounts_resp = await client.get(accounts_url, params=accounts_params, timeout=15.0)
            if accounts_resp.status_code != 200:
                logger.error(f"Failed to query user's accounts: {accounts_resp.text}")
                return RedirectResponse(f"/admin?error={accounts_resp.text}")
                
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
        return RedirectResponse(f"/admin?error={str(e)}")

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
