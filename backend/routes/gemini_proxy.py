"""Gemini API secure proxy route."""

import re
import httpx
from backend.services.http_client import shared_client
from fastapi import APIRouter, HTTPException, Query, Request
from backend.services import gemini_client

router = APIRouter()

# Gemini model identifiers are e.g. "gemini-2.5-flash" - restricting the shape before it's
# spliced into the request URL stops an arbitrary client-supplied value (any string, since
# this was previously unvalidated) from being forwarded as-is to Google.
_MODEL_NAME_PATTERN = re.compile(r'^[A-Za-z0-9_.-]+$')

# A generous cap on the whole request body - large enough for any real conversation, small
# enough to stop a buggy/malicious client from running up unbounded cost against the server's
# own Gemini API key with a single request.
MAX_PAYLOAD_BYTES = 2_000_000

@router.post("/api/gemini/generate")
async def proxy_gemini_generate(
    request: Request,
    model: str = Query(None, description="Gemini model identifier (defaults to server-configured model)")
):
    if model is not None and not _MODEL_NAME_PATTERN.match(model):
        raise HTTPException(status_code=400, detail="Invalid model identifier.")

    body = await request.body()
    if len(body) > MAX_PAYLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Request payload is too large.")

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

                from backend.database import get_api_key
                from backend.services.llm_client import check_providers, get_active_provider

                provider_health = await check_providers()
                active_provider_mode = get_active_provider()
                current_gemini_model = model or get_api_key("freja_gemini_model") or gemini_client.get_gemini_model()
                current_ollama_model = get_api_key("freja_ollama_model") or "llama3"
                ollama_url = get_api_key("freja_ollama_url") or "http://192.168.107.15:11434"

                # Check active integrations
                garmin_active = bool(get_api_key("freja_garmin_email"))
                strava_active = bool(get_api_key("freja_strava_client_id"))
                withings_active = bool(get_api_key("freja_withings_client_id"))
                mem0_active = get_api_key("freja_use_mem0") == "true"
                eleven_active = bool(get_api_key("freja_eleven_apikey"))

                system_info = (
                    f"\n\n[BACKEND CONFIGURATION & REALTIME LLM ENVIRONMENT]\n"
                    f"- Identity: You are FREJA (F.R.E.J.A.), Anders' personal AI assistant.\n"
                    f"- Current Primary AI Model: {current_gemini_model}.\n"
                    f"- Active AI Provider Mode: {active_provider_mode} (Gemini: {'Online' if provider_health.get('gemini') else 'Offline'}, Ollama: {'Online' if provider_health.get('ollama') else 'Offline'} at {ollama_url}).\n"
                    f"- Configured Ollama Model: {current_ollama_model}.\n"
                    f"- Integrations Status: Garmin ({'Active' if garmin_active else 'Inactive'}), Strava ({'Active' if strava_active else 'Inactive'}), Withings ({'Active' if withings_active else 'Inactive'}), Mem0 Neural Memory ({'Active' if mem0_active else 'Inactive'}), ElevenLabs Voice ({'Active' if eleven_active else 'Inactive'}).\n"
                    f"- Web Client Host: '{client_status.get('client_hostname', 'Local Browser')}' (OS: {client_os}).\n"
                    f"- Backend Server Host: '{client_status.get('hostname')}' (OS: {client_status.get('system')} {client_status.get('release')}).\n"
                    f"- DIRECTIVE: When asked what AI model you use, state clearly in Swedish that you are FREJA and are currently powered by {current_gemini_model} (with failover/Ollama model {current_ollama_model} in Anders' AI environment). When asked about backend settings, answer using the exact active configuration listed above."
                )
                parts[0]["text"] += system_info
                
                # Log system info injection for traceability
                import logging
                logging.getLogger("freja").info(
                    f"Injected system info: Client OS={client_os}, Model={current_gemini_model}, Provider={active_provider_mode}"
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
