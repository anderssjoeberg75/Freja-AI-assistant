"""Instagram Graph API Integration Service for F.R.E.J.A.

Implements backend helper methods to interact with Meta's official Instagram endpoints.
"""

import logging
import httpx
import asyncio
from backend.database import get_api_key

logger = logging.getLogger("freja.instagram")

GRAPH_BASE_URL = "https://graph.facebook.com/v19.0"

def get_instagram_config() -> tuple[str, str]:
    """Retrieves the Instagram Access Token and Business Account ID from the database."""
    token = get_api_key("freja_instagram_access_token") or ""
    ig_id = get_api_key("freja_instagram_business_account_id") or ""
    return token, ig_id

async def publish_photo(image_url: str, caption: str) -> dict:
    """Publishes a single photo with a caption to the linked Instagram Business account."""
    token, ig_id = get_instagram_config()
    if not token or not ig_id:
        return {"error": "Instagram integration is not fully configured or authenticated."}

    # Step 1: Create a media container
    container_url = f"{GRAPH_BASE_URL}/{ig_id}/media"
    payload = {
        "image_url": image_url,
        "caption": caption,
        "access_token": token
    }
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(container_url, data=payload, timeout=20.0)
            if resp.status_code != 200:
                logger.error(f"Failed to create IG media container: {resp.text}")
                return {"error": f"Failed to upload media to Instagram: {resp.text}"}
            
            container_id = resp.json().get("id")
            if not container_id:
                return {"error": "Meta API response missing media container ID."}
            
            # Step 2: Poll container status until it's finished/ready
            status_url = f"{GRAPH_BASE_URL}/{container_id}"
            params = {
                "fields": "status_code",
                "access_token": token
            }
            
            # Poll up to 10 times, waiting 3 seconds between attempts
            for attempt in range(10):
                await asyncio.sleep(3.0)
                status_resp = await client.get(status_url, params=params, timeout=10.0)
                if status_resp.status_code == 200:
                    status_code = status_resp.json().get("status_code", "").upper()
                    logger.info(f"IG Media container {container_id} status: {status_code}")
                    if status_code == "FINISHED":
                        break
                    elif status_code in ("ERROR", "EXPIRED"):
                        return {"error": f"IG Media container processing failed with status: {status_code}"}
                else:
                    logger.warning(f"Failed to poll IG container status (HTTP {status_resp.status_code}): {status_resp.text}")
            
            # Step 3: Publish the container
            publish_url = f"{GRAPH_BASE_URL}/{ig_id}/media_publish"
            publish_payload = {
                "creation_id": container_id,
                "access_token": token
            }
            
            pub_resp = await client.post(publish_url, data=publish_payload, timeout=20.0)
            if pub_resp.status_code != 200:
                logger.error(f"Failed to publish IG media container: {pub_resp.text}")
                return {"error": f"Failed to publish post: {pub_resp.text}"}
                
            return {
                "status": "success",
                "message": "Post successfully published to Instagram.",
                "post_id": pub_resp.json().get("id")
            }
            
    except Exception as e:
        logger.exception("Unexpected error during Instagram photo publishing")
        return {"error": f"Unexpected error during publishing: {str(e)}"}

async def get_recent_media(limit: int = 5) -> dict:
    """Fetches the user's latest published media items."""
    token, ig_id = get_instagram_config()
    if not token or not ig_id:
        return {"error": "Instagram integration is not fully configured or authenticated."}

    url = f"{GRAPH_BASE_URL}/{ig_id}/media"
    params = {
        "fields": "id,caption,media_type,media_url,permalink,timestamp",
        "limit": limit,
        "access_token": token
    }
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=15.0)
            if resp.status_code != 200:
                logger.error(f"Failed to fetch IG media: {resp.text}")
                return {"error": f"Failed to retrieve Instagram media: {resp.text}"}
                
            return {"status": "success", "media": resp.json().get("data", [])}
    except Exception as e:
        logger.exception("Failed to retrieve Instagram media")
        return {"error": f"Unexpected error: {str(e)}"}

async def get_comments(media_id: str) -> dict:
    """Retrieves comments on a specific Instagram media post."""
    token, _ = get_instagram_config()
    if not token:
        return {"error": "Instagram access token is missing."}

    url = f"{GRAPH_BASE_URL}/{media_id}/comments"
    params = {
        "fields": "id,text,username,timestamp",
        "access_token": token
    }
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=15.0)
            if resp.status_code != 200:
                logger.error(f"Failed to fetch IG comments for media {media_id}: {resp.text}")
                return {"error": f"Failed to retrieve comments: {resp.text}"}
                
            return {"status": "success", "comments": resp.json().get("data", [])}
    except Exception as e:
        logger.exception(f"Failed to retrieve comments for media {media_id}")
        return {"error": f"Unexpected error: {str(e)}"}

async def post_comment_reply(comment_id: str, text: str) -> dict:
    """Posts a reply to a specific comment."""
    token, _ = get_instagram_config()
    if not token:
        return {"error": "Instagram access token is missing."}

    url = f"{GRAPH_BASE_URL}/{comment_id}/replies"
    payload = {
        "message": text,
        "access_token": token
    }
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, data=payload, timeout=15.0)
            if resp.status_code != 200:
                logger.error(f"Failed to reply to comment {comment_id}: {resp.text}")
                return {"error": f"Failed to reply to comment: {resp.text}"}
                
            return {"status": "success", "reply_id": resp.json().get("id")}
    except Exception as e:
        logger.exception(f"Failed to reply to comment {comment_id}")
        return {"error": f"Unexpected error: {str(e)}"}
