"""Telegram integration API route router using FastAPI."""

from fastapi import APIRouter, HTTPException, Request
from backend.database import get_db_connection
from backend.services.telegram_service import get_telegram_config, recent_messages

router = APIRouter()

@router.get("/api/telegram/status")
async def get_telegram_status():
    """Gets current status and recent processed messages for the Telegram integration."""
    try:
        token, chat_id = get_telegram_config()
        is_active = bool(token and chat_id)
        return {
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
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Insert or update keys
            cursor.execute('''
                INSERT INTO api_keys (key_name, key_value)
                VALUES ('freja_telegram_bot_token', ?)
                ON CONFLICT(key_name) DO UPDATE SET key_value = excluded.key_value
            ''', (token,))
            
            cursor.execute('''
                INSERT INTO api_keys (key_name, key_value)
                VALUES ('freja_telegram_chat_id', ?)
                ON CONFLICT(key_name) DO UPDATE SET key_value = excluded.key_value
            ''', (chat_id,))
            
            conn.commit()
        
        return {"status": "success", "message": "Telegram inställningar sparade."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

