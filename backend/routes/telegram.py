"""Telegram integration API route router using FastAPI."""

from fastapi import APIRouter, HTTPException, Request
from backend.database import set_api_key
from backend.services.telegram_service import get_telegram_config, recent_messages

router = APIRouter()

@router.get("/api/telegram/status")
async def get_telegram_status():
    """Gets current status and recent processed messages for the Telegram integration."""
    try:
        token, chat_id = get_telegram_config()
        is_active = bool(token and chat_id)
        return {
            "active": is_active,
            "is_active": is_active,
            "token_configured": bool(token),
            "chat_id_configured": bool(chat_id),
            "chat_id": chat_id,
            "token_masked": (token[:6] + "..." + token[-6:]) if len(token) > 12 else ("Configured" if token else ""),
            "recent_messages": recent_messages
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/telegram/config")
async def post_telegram_config(request: Request):
    """Saves Telegram bot token and authorized chat ID to SQLite database."""
    try:
        data = await request.json()
        token = data.get("token", "").strip()
        chat_id = data.get("chat_id", "").strip()

        # Insert or update keys
        set_api_key('freja_telegram_bot_token', token)
        set_api_key('freja_telegram_chat_id', chat_id)

        return {"status": "success", "message": "Telegram inställningar sparade."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

