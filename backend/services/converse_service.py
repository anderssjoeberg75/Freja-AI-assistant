"""Channel-neutral Freja conversation core.

The Telegram worker (backend/services/telegram_service.py) already implements the
provider-neutral, tool-calling turn loops (query_gemini_with_tools and
_telegram_tool_loop_ollama). This module reuses those loop functions and adds a
channel-neutral entry point, generate_freja_reply(), so the Android
/api/chat/converse endpoint runs one full Freja turn server-side without holding an
LLM key or duplicating the agent loop. User-facing reply text is Swedish;
everything else is English.

The web HUD (client/gemini.js) and the Telegram bot are intentionally left
untouched - this module only adds a new server-side surface.
"""

import datetime
import logging

from backend.database import get_db_connection
from backend.services import gemini_client, llm_client
from backend.services.telegram_service import (
    query_gemini_with_tools,
    _telegram_tool_loop_ollama,
)
from backend.routes.trainer.plans import build_chat_context_block

logger = logging.getLogger("freja")

# How many prior messages to carry into the model (mirrors the web/Telegram cap of 15).
HISTORY_LIMIT = 15

# Channel-neutral behavioural directives. Persona intro and any channel-specific
# formatting rules are prepended by the caller, not stored here.
FREJA_SYSTEM_DIRECTIVES = (
    "[DIRECTIVE: CODEBASE SELF-ANALYSIS]\n"
    "If the user asks you to analyse your code, perform an audit or suggest improvements to "
    "the source code, you must ALWAYS call the 'codex_audit_codebase' tool. When you get the "
    "result (a summary plus a path to a Markdown report), you may use the 'read_project_file' "
    "tool to read the report or source files if you need more detail.\n\n"
    "[DIRECTIVE: WINDOWS OS AUTOMATION & ENVIRONMENT AWARENESS]\n"
    "If the user asks you to do things on their Windows computer (launch a program, open a web "
    "address, browse a folder or run cmd commands), use the 'run_windows_command' tool with the "
    "appropriate arguments (open_app, open_url, open_folder or run_cmd). Tools run on the backend "
    "server machine, not the client. If the tool returns a platform error, explain that the "
    "backend runs on a different OS than the client.\n\n"
    "[DIRECTIVE: HEALTH AND FITNESS STATUS]\n"
    "If the user asks how they are doing, how they slept, their steps, recovery, or training "
    "status, immediately call 'get_garmin_health' (and/or 'get_personal_trainer_advice') to "
    "retrieve their actual data rather than asking for permission first. Include key metrics "
    "such as Body Battery and sleep phases when presenting Garmin data.\n\n"
    "[DIRECTIVE: PERSONAL TRAINER & WORKOUT DISCUSSION]\n"
    "You ARE the user's Personal Trainer (COACH AI), with full awareness of the active plan, "
    "scheduled workouts, limitations, health data and running history. Make authoritative "
    "coaching decisions rather than asking passive questions. When the user asks about today's "
    "workout or why an intensity was chosen, call 'get_trainer_workouts' or "
    "'get_personal_trainer_advice' and explain your reasoning in Swedish. If a workout needs "
    "adjusting, call 'update_trainer_workout' and tell the user you updated the schedule."
)

# Voice-first persona: no Markdown, phrased for text-to-speech.
VOICE_PERSONA_INTRO = (
    "You are FREJA, Anders' intelligent, polite and witty personal AI assistant for health and "
    "training. You are FREJA - never claim to be an AI developed by Google. Answer concisely and "
    "conversationally in plain spoken Swedish suitable for text-to-speech: no Markdown, no bullet "
    "lists, no code blocks, no emoji. Always answer in Swedish unless the user writes in another "
    "language.\n\n"
)


class ProviderUnavailable(Exception):
    """Raised when neither Gemini nor Ollama can serve the turn."""


def _load_recent_contents(limit: int = HISTORY_LIMIT) -> list:
    """Reads the most recent chat_history rows and returns them as Gemini-shaped contents
    ([{role, parts:[{text}]}]), oldest first. 'user' maps to role 'user', anything else to
    'model'. Cross-channel, so Freja remembers context from any surface."""
    with get_db_connection() as conn:
        rows = conn.cursor().execute(
            "SELECT sender, content FROM chat_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    rows.reverse()
    contents = []
    for sender, content in rows:
        role = "user" if sender == "user" else "model"
        contents.append({"role": role, "parts": [{"text": content}]})
    return contents


def _persist(sender: str, content: str, channel: str) -> None:
    """Appends one message to chat_history. Never raises into the turn - a history write
    failure must not lose the user's reply."""
    try:
        with get_db_connection() as conn:
            conn.cursor().execute(
                "INSERT INTO chat_history (sender, content, timestamp, channel) VALUES (?, ?, ?, ?)",
                (sender, content, datetime.datetime.now().isoformat(), channel),
            )
            conn.commit()
    except Exception as db_err:
        logger.warning("converse: failed to persist %s message: %s", sender, db_err)


def _build_system_prompt(persona_intro: str) -> str:
    """persona_intro + shared directives + today's PT context (when there is any)."""
    prompt = persona_intro + FREJA_SYSTEM_DIRECTIVES
    try:
        pt_context = build_chat_context_block()
    except Exception as ctx_err:
        logger.warning("converse: PT context unavailable: %s", ctx_err)
        pt_context = ""
    if pt_context:
        prompt += "\n\n[PERSONAL TRAINER CONTEXT]\n" + pt_context
    return prompt


async def generate_freja_reply(user_text: str, *, channel: str = "android") -> str:
    """Runs one full Freja turn and returns her Swedish reply. Persists both the user's
    message and the reply to chat_history under `channel`. Raises ProviderUnavailable when no
    LLM provider is configured/reachable.

    History is loaded before the user's message is persisted, so the current turn is appended
    exactly once (never duplicated by the history read)."""
    gemini_key = gemini_client.get_gemini_api_key()
    if not gemini_key:
        status = await llm_client.get_provider_status()
        ollama_ok = bool((status.get("providers", {}).get("ollama") or {}).get("ok"))
        if not ollama_ok:
            raise ProviderUnavailable(
                "No LLM provider available: no Gemini key and Ollama is unreachable."
            )

    contents = _load_recent_contents()
    contents.append({"role": "user", "parts": [{"text": user_text}]})
    system_prompt = _build_system_prompt(VOICE_PERSONA_INTRO)

    async def _gemini_arm():
        return await query_gemini_with_tools(contents, gemini_key, system_prompt)

    async def _ollama_arm():
        return await _telegram_tool_loop_ollama(contents, system_prompt)

    reply = await llm_client._dispatch("android converse", _ollama_arm, _gemini_arm)

    _persist("user", user_text, channel)
    _persist("assistant", reply, channel)
    return reply
