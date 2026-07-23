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

    from backend.services import ollama_client, gemini_client, llm_client, system_context
    from backend.services.llm_client import _dispatch

    # Cached (see llm_client.get_provider_status): describing the setup in every system
    # prompt must not cost a live probe of the Ollama box plus a round-trip to Google
    # before every single chat turn.
    provider_health = await llm_client.get_provider_status()
    providers = provider_health.get("providers") or {}

    # Base system instruction, before either provider appends its own runtime line. Kept
    # separate so a fallback retry composes from the base instead of stacking the failed
    # provider's line on top of the next one's.
    base_system_instruction = None
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

                parts[0]["text"] += system_context.build_backend_context_block(
                    provider_health, client_status, client_os
                )
            except Exception as e:
                import logging
                logging.getLogger("freja").warning(f"Failed to inject system info into prompt: {e}")
            base_system_instruction = parts[0]["text"]

    # A missing Gemini key is only fatal when Gemini is the one that would have to serve
    # this request. With a reachable Ollama server (and the operator pointing at it), the
    # chat works perfectly well without any Google credentials at all.
    if not gemini_client.get_gemini_api_key() and not (providers.get("ollama") or {}).get("ok"):
        raise HTTPException(status_code=400, detail="Gemini API key is not configured on the server.")

    def _instruction_for(provider: str, provider_model: str) -> str:
        """The system instruction with this provider's runtime line appended. Built from
        the base text every time, so a fallback never inherits the previous attempt's line."""
        if base_system_instruction is None:
            return ""
        return base_system_instruction + system_context.build_runtime_provider_line(provider, provider_model)

    async def _call_gemini():
        api_key = gemini_client.get_gemini_api_key()
        if not api_key:
            raise HTTPException(status_code=400, detail="Gemini API key is not configured on the server.")
        gemini_model = model or gemini_client.get_gemini_model()
        google_url = gemini_client.build_generate_url(gemini_model, api_key)

        outbound = payload
        if base_system_instruction is not None:
            # Shallow copy: only the system instruction differs per provider, and the
            # caller's payload must not be mutated on the way to a possible retry.
            outbound = dict(payload)
            outbound["systemInstruction"] = {
                "parts": [{"text": _instruction_for("gemini", gemini_model)}]
            }

        async with shared_client() as client:
            response = await client.post(google_url, json=outbound, timeout=30.0)
            response.raise_for_status()
            return response.json()

    async def _call_ollama():
        sys_instruction = _instruction_for("ollama", ollama_client.get_ollama_model())
        if not sys_instruction and "systemInstruction" in payload and "parts" in payload["systemInstruction"]:
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
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"LLM API error: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to communicate with LLM provider: {str(e)}")
