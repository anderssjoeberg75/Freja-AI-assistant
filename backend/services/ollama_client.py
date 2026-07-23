"""Shared client for the self-hosted Ollama server.

Mirrors gemini_client.py's shape (key/model lookup, generate_text, generate_json) so
llm_client.py can try this provider first and fall back to Gemini without callers
needing to know which one actually answered.
"""

import json

from backend.services.http_client import shared_client
from backend.database import get_api_key

# Points at the RTX 3060 (12GB) box running Ollama. qwen2.5:14b at Q4 fully offloads to
# that GPU with room to spare at this context size - see OLLAMA_NUM_CTX below.
DEFAULT_OLLAMA_BASE_URL = "http://192.168.107.15:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:14b"
# 12288 keeps qwen2.5:14b at 100% GPU offload on a 12GB card (measured ~10.8GB used);
# large enough for the PT tool's health-data prompts plus a multi-thousand-token
# structured response. Going to 16384 spills part of the model onto the CPU.
OLLAMA_NUM_CTX = 12288


def get_ollama_base_url() -> str:
    """Returns the configured Ollama base URL (settings key 'freja_ollama_base_url'),
    falling back to the project default."""
    return (get_api_key("freja_ollama_base_url") or DEFAULT_OLLAMA_BASE_URL).rstrip("/")


def get_ollama_model() -> str:
    """Returns the configured Ollama model name (settings key 'freja_ollama_model'),
    falling back to the project default."""
    return get_api_key("freja_ollama_model") or DEFAULT_OLLAMA_MODEL


_GEMINI_TO_JSON_SCHEMA_TYPES = {
    "OBJECT": "object", "STRING": "string", "ARRAY": "array",
    "NUMBER": "number", "INTEGER": "integer", "BOOLEAN": "boolean",
}


def _to_json_schema(node):
    """Converts a Gemini-dialect responseSchema (uppercase `type` values) into the
    standard lowercase JSON Schema Ollama's `format` field expects. The rest of the
    codebase already authors its schemas in Gemini's dialect, so callers pass those
    straight through instead of maintaining two copies of every schema."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "type" and isinstance(v, str):
                out[k] = _GEMINI_TO_JSON_SCHEMA_TYPES.get(v, v.lower())
            else:
                out[k] = _to_json_schema(v)
        return out
    if isinstance(node, list):
        return [_to_json_schema(v) for v in node]
    return node


async def check_health(timeout: float = 4.0) -> dict:
    """Probes the configured Ollama server and reports whether it can serve requests.
    Never raises - the admin portal's indicator needs a red/green answer, not a 500 - so
    every failure comes back as ok=False with the reason in `detail`.

    `models` lists what is actually installed on that server, which the portal uses to
    populate its model picker."""
    base_url = get_ollama_base_url()
    model = get_ollama_model()
    status = {"ok": False, "detail": "", "model": model, "base_url": base_url, "models": []}

    try:
        async with shared_client() as client:
            resp = await client.get(f"{base_url}/api/tags", timeout=timeout)
            resp.raise_for_status()
            installed = resp.json().get("models", [])
    except Exception as e:
        status["detail"] = f"Server unreachable: {e}"
        return status

    status["models"] = sorted(m.get("name", "") for m in installed if m.get("name"))
    # Ollama reports fully-qualified names ("qwen2.5:14b"); a configured name without a
    # tag means the implicit ":latest" tag, so compare against that form.
    wanted = model if ":" in model else f"{model}:latest"
    if wanted not in status["models"]:
        status["detail"] = f"Server is online but the model '{model}' is not installed on it."
        return status

    status["ok"] = True
    status["detail"] = f"Online at {base_url}, serving {model}."
    return status


async def generate_text(prompt: str, system_instruction: str = "",
                         temperature: float = 0.2, timeout: float = 60.0) -> str:
    """Sends a single-turn prompt to the local Ollama server and returns the reply text."""
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": get_ollama_model(),
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature, "num_ctx": OLLAMA_NUM_CTX},
    }
    url = f"{get_ollama_base_url()}/api/chat"
    async with shared_client() as client:
        resp = await client.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        resp_json = resp.json()

    return resp_json.get("message", {}).get("content", "")


async def generate_json(prompt: str, schema: dict = None, system_instruction: str = "",
                         temperature: float = 0.3, max_tokens: int = 3000,
                         timeout: float = 60.0) -> dict:
    """Sends a prompt to Ollama constrained to JSON output and returns the parsed object.
    `schema` uses Gemini's responseSchema dialect (see `_to_json_schema`); pass None for
    freeform JSON (the model is only told to answer with valid JSON, not a fixed shape)."""
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": get_ollama_model(),
        "messages": messages,
        "format": _to_json_schema(schema) if schema else "json",
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_ctx": OLLAMA_NUM_CTX,
            "num_predict": max_tokens,
        },
    }
    url = f"{get_ollama_base_url()}/api/chat"
    async with shared_client() as client:
        resp = await client.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        resp_json = resp.json()

    text = resp_json.get("message", {}).get("content", "")
    if not text:
        raise Exception("Ollama returned an empty response.")
    return json.loads(text.replace("```json", "").replace("```", "").strip())
