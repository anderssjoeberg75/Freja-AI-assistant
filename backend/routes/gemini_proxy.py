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

    from backend.database import get_api_key
    from backend.services import ollama_client, gemini_client
    from backend.services.llm_client import _dispatch, check_providers, get_provider_preference

    provider_health = await check_providers()
    provider_pref = get_provider_preference()
    current_gemini_model = model or get_api_key("freja_gemini_model") or gemini_client.get_gemini_model()
    current_ollama_model = get_api_key("freja_ollama_model") or "llama3"
    ollama_url = get_api_key("freja_ollama_url") or "http://192.168.107.15:11434"

    # Inject detailed system information into the prompt instruction
    if "systemInstruction" in payload and "parts" in payload["systemInstruction"]:
        parts = payload["systemInstruction"]["parts"]
        if parts and isinstance(parts, list) and len(parts) > 0 and "text" in parts[0]:
            try:
                from backend.routes.settings import get_client_status
                client_status = get_client_status()
                
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

                # Check active integrations
                garmin_active = bool(get_api_key("freja_garmin_email"))
                strava_active = bool(get_api_key("freja_strava_client_id"))
                withings_active = bool(get_api_key("freja_withings_client_id"))
                mem0_active = get_api_key("freja_use_mem0") == "true"
                eleven_active = bool(get_api_key("freja_eleven_apikey"))

                pref_description = "Auto (Gemini först, Ollama som reserv)" if provider_pref == "auto_gemini" else "Auto (Ollama först, Gemini som reserv)" if provider_pref == "auto" else "Ollama endast" if provider_pref == "ollama" else "Gemini endast"

                system_info = (
                    f"\n\n[BACKEND CONFIGURATION & REALTIME LLM ENVIRONMENT]\n"
                    f"- Identity: You are FREJA (F.R.E.J.A.), Anders' personal AI assistant.\n"
                    f"- Configured AI Provider Strategy: {provider_pref} ({pref_description}).\n"
                    f"- Self-hosted Ollama Status: {'Online' if provider_health.get('ollama') else 'Offline'} (Model: {current_ollama_model} at {ollama_url}).\n"
                    f"- Google Gemini API Status: {'Online' if provider_health.get('gemini') else 'Offline'} (Model: {current_gemini_model}).\n"
                    f"- Integrations Status: Garmin ({'Active' if garmin_active else 'Inactive'}), Strava ({'Active' if strava_active else 'Inactive'}), Withings ({'Active' if withings_active else 'Inactive'}), Mem0 Neural Memory ({'Active' if mem0_active else 'Inactive'}), ElevenLabs Voice ({'Active' if eleven_active else 'Inactive'}).\n"
                    f"- Web Client Host: '{client_status.get('client_hostname', 'Local Browser')}' (OS: {client_os}).\n"
                    f"- Backend Server Host: '{client_status.get('hostname')}' (OS: {client_status.get('system')} {client_status.get('release')}).\n"
                    f"- DIRECTIVE: When asked what AI model or provider strategy you use, explain in Swedish that you are FREJA and state Anders' configured AI Provider strategy: '{pref_description}'. State clearly which engine responded."
                )
                parts[0]["text"] += system_info
            except Exception as e:
                import logging
                logging.getLogger("freja").warning(f"Failed to inject system info into prompt: {e}")

    async def _call_gemini():
        api_key = gemini_client.get_gemini_api_key()
        if not api_key:
            raise HTTPException(status_code=400, detail="Gemini API key is not configured on the server.")
        google_url = gemini_client.build_generate_url(model or gemini_client.get_gemini_model(), api_key)
        async with shared_client() as client:
            response = await client.post(google_url, json=payload, timeout=30.0)
            response.raise_for_status()
            return response.json()

    async def _call_ollama():
        sys_instruction = ""
        if "systemInstruction" in payload and "parts" in payload["systemInstruction"]:
            sys_parts = payload["systemInstruction"]["parts"]
            if sys_parts and isinstance(sys_parts, list) and len(sys_parts) > 0:
                sys_instruction = sys_parts[0].get("text", "")

        contents = payload.get("contents", [])
        if not contents:
            raise HTTPException(status_code=400, detail="Missing contents in request payload.")

        latest_user_text = ""
        full_prompt_lines = []
        for turn in contents:
            role = turn.get("role", "user")
            parts = turn.get("parts", [])
            text = "".join([p.get("text", "") for p in parts if isinstance(p, dict)])
            if text:
                if role == "user":
                    latest_user_text = text
                    full_prompt_lines.append(f"User: {text}")
                elif role in ("model", "assistant"):
                    full_prompt_lines.append(f"Assistant: {text}")

        full_context_prompt = "\n".join(full_prompt_lines) if len(full_prompt_lines) > 1 else latest_user_text

        ollama_text = await ollama_client.generate_text(
            prompt=full_context_prompt,
            system_instruction=sys_instruction,
            temperature=0.7,
            timeout=60.0
        )

        return {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": ollama_text}
                        ],
                        "role": "model"
                    },
                    "finishReason": "STOP"
                }
            ]
        }

    try:
        res_data = await _dispatch("chat generation", _call_ollama, _call_gemini)
        return res_data
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"LLM API error: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to communicate with LLM provider: {str(e)}")
