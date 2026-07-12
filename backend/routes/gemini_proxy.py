"""Gemini API secure proxy route."""

import httpx
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

    # Inject host system information into system prompt so the model is aware of the OS (e.g. Windows 11)
    if "systemInstruction" in payload and "parts" in payload["systemInstruction"]:
        parts = payload["systemInstruction"]["parts"]
        if parts and isinstance(parts, list) and len(parts) > 0 and "text" in parts[0]:
            try:
                from backend.routes.settings import get_client_status
                client_status = get_client_status()
                host_info = (
                    f"\n\n[HOST SYSTEM INFO]\n"
                    f"- The host computer running the application is '{client_status['hostname']}' "
                    f"({client_status['system']} {client_status['release']})."
                )
                parts[0]["text"] += host_info
                # Log system info injection for traceability
                import logging
                logging.getLogger("freja").info(
                    f"Injected host system info into Gemini systemInstruction: {client_status['hostname']} ({client_status['system']} {client_status['release']})"
                )
            except Exception as e:
                import logging
                logging.getLogger("freja").warning(f"Failed to inject host system info: {e}")

    api_key = gemini_client.get_gemini_api_key()
    if not api_key:
        raise HTTPException(status_code=400, detail="Gemini API key is not configured on the server.")

    # Call official Gemini endpoint
    google_url = gemini_client.build_generate_url(model or gemini_client.get_gemini_model(), api_key)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(google_url, json=payload, timeout=30.0)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Google Gemini API error: {e.response.text}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"Failed to communicate with Google Gemini API: {str(e)}")
