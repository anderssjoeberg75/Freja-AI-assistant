"""Gemini API secure proxy route."""

import httpx
from backend.services.http_client import shared_client
from fastapi import APIRouter, HTTPException, Query, Request
from backend.services import gemini_client

router = APIRouter()

@router.post("/api/gemini/generate")
async def proxy_gemini_generate(
    request: Request,
    model: str = Query(None, description="Gemini model identifier (defaults to server-configured model)")
):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Inject detailed system information into the prompt instruction so Gemini knows about client and backend hosts
    if "systemInstruction" in payload and "parts" in payload["systemInstruction"]:
        parts = payload["systemInstruction"]["parts"]
        if parts and isinstance(parts, list) and len(parts) > 0 and "text" in parts[0]:
            try:
                from backend.routes.settings import get_client_status
                client_status = get_client_status()
                
                # Parse request User-Agent to detect client OS
                ua = request.headers.get("user-agent", "")
                client_os = "Unknown"
                if "Windows" in ua:
                    client_os = "Windows (likely Windows 11)"
                elif "Macintosh" in ua or "Mac OS X" in ua:
                    client_os = "macOS"
                elif "Linux" in ua:
                    client_os = "Linux"
                elif "Android" in ua:
                    client_os = "Android"
                elif "iPhone" in ua or "iPad" in ua:
                    client_os = "iOS"

                client_name_info = f" (Computer Name/Hostname: '{client_status['client_hostname']}')" if client_status.get("client_hostname") and client_status["client_hostname"] != "Unknown" else ""
                system_info = (
                    f"\n\n[SYSTEM & PLATFORM INFO]\n"
                    f"- The web client (HUD) is running in a browser on the user's local machine{client_name_info} (Client OS: {client_os}).\n"
                    f"- The backend server is running on a host named '{client_status['hostname']}' (Backend OS: {client_status['system']} {client_status['release']})."
                )
                parts[0]["text"] += system_info
                
                # Log system info injection for traceability
                import logging
                logging.getLogger("freja").info(
                    f"Injected system info: Client OS={client_os}, Backend Host={client_status['hostname']} ({client_status['system']})"
                )
            except Exception as e:
                import logging
                logging.getLogger("freja").warning(f"Failed to inject system info into prompt: {e}")

    api_key = gemini_client.get_gemini_api_key()
    if not api_key:
        raise HTTPException(status_code=400, detail="Gemini API key is not configured on the server.")

    # Call official Gemini endpoint
    google_url = gemini_client.build_generate_url(model or gemini_client.get_gemini_model(), api_key)

    try:
        async with shared_client() as client:
            response = await client.post(google_url, json=payload, timeout=30.0)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Google Gemini API error: {e.response.text}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"Failed to communicate with Google Gemini API: {str(e)}")
