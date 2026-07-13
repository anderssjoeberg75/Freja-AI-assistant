"""Instagram Graph API Integration Service for F.R.E.J.A.

Implements backend helper methods to interact with Meta's official Instagram endpoints.

Design notes:
  * The Graph API version/base URL lives in `backend.config` (GRAPH_BASE_URL) so it is
    bumped in a single place, shared with the OAuth routes in `backend/routes/instagram.py`.
  * The access token is sent in the `Authorization: Bearer` header rather than as a query
    parameter, so it never ends up in request URLs (and therefore not in access logs).
  * Every public coroutine returns a JSON-serialisable dict and never raises: failures are
    reported as {"error": "..."} so the calling tool can explain the problem to the user.
"""

import logging
import asyncio
import httpx
from backend.config import GRAPH_BASE_URL
from backend.database import get_api_key

logger = logging.getLogger("freja.instagram")

# Meta processes uploaded media asynchronously behind a "container". Images are usually
# FINISHED on the first check; video/reels can take much longer, hence the generous window.
_CONTAINER_POLL_ATTEMPTS = 20
_CONTAINER_POLL_INTERVAL = 3.0
_DEFAULT_TIMEOUT = 20.0


def get_instagram_config() -> tuple[str, str]:
    """Retrieves the Instagram Access Token and Business Account ID from the database."""
    token = get_api_key("freja_instagram_access_token") or ""
    ig_id = get_api_key("freja_instagram_business_account_id") or ""
    return token, ig_id


def _require_config(need_account: bool = True) -> tuple[str, str, dict | None]:
    """Loads credentials and returns (token, ig_id, error). `error` is a ready-to-return
    dict when something is missing, otherwise None."""
    token, ig_id = get_instagram_config()
    if not token:
        return "", "", {"error": "Instagram integration is not authenticated. Link the account in Settings first."}
    if need_account and not ig_id:
        return token, "", {"error": "No linked Instagram Business account was found. Re-link the account in Settings."}
    return token, ig_id, None


def _extract_error(resp: httpx.Response) -> str:
    """Turns a Meta Graph API error response into a short, human-readable message.

    Meta wraps failures as {"error": {"message": ..., "code": ...}}; fall back to the raw
    body only when that envelope is absent so we never surface an opaque blob to the user."""
    try:
        err = resp.json().get("error")
        if isinstance(err, dict) and err.get("message"):
            return err["message"]
    except Exception:
        pass
    return (resp.text or "").strip() or f"HTTP {resp.status_code}"


async def _graph_request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    token: str,
    *,
    params: dict | None = None,
    data: dict | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> tuple[dict | None, str | None]:
    """Performs a single Graph API call and returns (json, error_message).

    Exactly one of the tuple slots is populated: a non-200 response yields (None, message),
    a successful one yields (parsed_json, None). The token is passed via the Authorization
    header so it stays out of the request URL."""
    url = f"{GRAPH_BASE_URL}/{path}"
    resp = await client.request(
        method,
        url,
        params=params,
        data=data,
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    )
    if resp.status_code != 200:
        logger.error(f"Graph API {method} {path} failed (HTTP {resp.status_code}): {resp.text}")
        return None, _extract_error(resp)
    return resp.json(), None


async def _await_container_ready(client: httpx.AsyncClient, container_id: str, token: str) -> str | None:
    """Polls a media container until it is FINISHED. Returns None on success or an error
    message string on failure/timeout."""
    for attempt in range(_CONTAINER_POLL_ATTEMPTS):
        data, err = await _graph_request(
            client, "GET", container_id, token,
            params={"fields": "status_code"}, timeout=10.0,
        )
        if err is None:
            status_code = (data.get("status_code") or "").upper()
            logger.info(f"IG media container {container_id} status: {status_code} (attempt {attempt + 1})")
            if status_code == "FINISHED":
                return None
            if status_code in ("ERROR", "EXPIRED"):
                return f"Media processing failed with status: {status_code}"
        await asyncio.sleep(_CONTAINER_POLL_INTERVAL)
    return "Media processing timed out before the container was ready to publish."


async def publish_media(media_url: str, caption: str, media_type: str = "IMAGE") -> dict:
    """Publishes a photo (IMAGE) or reel/video (REELS) to the linked Instagram account.

    Uses Meta's three-step flow: create a media container, wait for it to finish processing,
    then publish it. `media_url` must be a publicly accessible direct link to the asset."""
    media_type = (media_type or "IMAGE").upper()
    if media_type not in ("IMAGE", "REELS"):
        return {"error": f"Unsupported media_type '{media_type}'. Use 'IMAGE' or 'REELS'."}
    if not media_url:
        return {"error": "A public media URL is required."}

    token, ig_id, cfg_err = _require_config()
    if cfg_err:
        return cfg_err

    # IMAGE uses image_url; REELS uses video_url plus an explicit media_type field.
    container_payload = {"caption": caption}
    if media_type == "IMAGE":
        container_payload["image_url"] = media_url
    else:
        container_payload["media_type"] = "REELS"
        container_payload["video_url"] = media_url

    try:
        async with httpx.AsyncClient() as client:
            # Step 1: create the media container.
            data, err = await _graph_request(client, "POST", f"{ig_id}/media", token, data=container_payload)
            if err:
                return {"error": f"Failed to upload media to Instagram: {err}"}
            container_id = data.get("id")
            if not container_id:
                return {"error": "Meta API response missing media container ID."}

            # Step 2: wait until Meta has finished processing the upload.
            poll_err = await _await_container_ready(client, container_id, token)
            if poll_err:
                return {"error": poll_err}

            # Step 3: publish the finished container.
            pub, err = await _graph_request(
                client, "POST", f"{ig_id}/media_publish", token,
                data={"creation_id": container_id},
            )
            if err:
                return {"error": f"Failed to publish post: {err}"}

            return {
                "status": "success",
                "message": "Post successfully published to Instagram.",
                "post_id": pub.get("id"),
            }
    except Exception as e:
        logger.exception("Unexpected error during Instagram media publishing")
        return {"error": f"Unexpected error during publishing: {str(e)}"}


async def publish_photo(image_url: str, caption: str) -> dict:
    """Backwards-compatible wrapper: publishes a single photo. See publish_media."""
    return await publish_media(image_url, caption, media_type="IMAGE")


async def get_recent_media(limit: int = 5) -> dict:
    """Fetches the user's latest published media items."""
    token, ig_id, cfg_err = _require_config()
    if cfg_err:
        return cfg_err

    try:
        async with httpx.AsyncClient() as client:
            data, err = await _graph_request(
                client, "GET", f"{ig_id}/media", token,
                params={
                    "fields": "id,caption,media_type,media_url,permalink,timestamp",
                    "limit": limit,
                },
                timeout=15.0,
            )
            if err:
                return {"error": f"Failed to retrieve Instagram media: {err}"}
            return {"status": "success", "media": data.get("data", [])}
    except Exception as e:
        logger.exception("Failed to retrieve Instagram media")
        return {"error": f"Unexpected error: {str(e)}"}


async def get_comments(media_id: str) -> dict:
    """Retrieves comments on a specific Instagram media post."""
    if not media_id:
        return {"error": "A media ID is required."}

    token, _, cfg_err = _require_config(need_account=False)
    if cfg_err:
        return cfg_err

    try:
        async with httpx.AsyncClient() as client:
            data, err = await _graph_request(
                client, "GET", f"{media_id}/comments", token,
                params={"fields": "id,text,username,timestamp"},
                timeout=15.0,
            )
            if err:
                return {"error": f"Failed to retrieve comments: {err}"}
            return {"status": "success", "comments": data.get("data", [])}
    except Exception as e:
        logger.exception(f"Failed to retrieve comments for media {media_id}")
        return {"error": f"Unexpected error: {str(e)}"}


async def post_comment_reply(comment_id: str, text: str) -> dict:
    """Posts a reply to a specific comment."""
    if not comment_id:
        return {"error": "A comment ID is required."}
    if not text:
        return {"error": "Reply text cannot be empty."}

    token, _, cfg_err = _require_config(need_account=False)
    if cfg_err:
        return cfg_err

    try:
        async with httpx.AsyncClient() as client:
            data, err = await _graph_request(
                client, "POST", f"{comment_id}/replies", token,
                data={"message": text},
                timeout=15.0,
            )
            if err:
                return {"error": f"Failed to reply to comment: {err}"}
            return {"status": "success", "reply_id": data.get("id")}
    except Exception as e:
        logger.exception(f"Failed to reply to comment {comment_id}")
        return {"error": f"Unexpected error: {str(e)}"}
