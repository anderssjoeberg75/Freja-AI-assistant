import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from backend.database import get_db_session
from backend.models import User, ChatHistory
from backend.routes.auth import get_current_user
from backend.services import converse_service

router = APIRouter()

class ChatMessage(BaseModel):
    sender: str
    content: str = Field(max_length=50_000)
    channel: str = "web"

class ConverseRequest(BaseModel):
    text: str = Field(min_length=1, max_length=50_000)
    channel: str = "android"
    image_base64: str | None = Field(default=None, max_length=12_000_000)

@router.get("/api/chat/history")
async def get_chat_history(
    limit: int = Query(50, ge=1, le=500, description="Number of messages to fetch"),
    current_user: User = Depends(get_current_user)
):
    try:
        with get_db_session() as db:
            messages = db.query(ChatHistory)\
                .filter((ChatHistory.user_id == current_user.id) | (ChatHistory.user_id.is_(None)))\
                .order_by(ChatHistory.id.desc())\
                .limit(limit)\
                .all()

        messages.reverse()
        return [
            {
                "sender": m.sender,
                "content": m.content,
                "timestamp": m.timestamp,
                "channel": m.channel
            }
            for m in messages
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.post("/api/chat/message")
async def save_chat_message(
    message: ChatMessage,
    current_user: User = Depends(get_current_user)
):
    try:
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_db_session() as db:
            msg = ChatHistory(
                user_id=current_user.id,
                sender=message.sender,
                content=message.content,
                timestamp=timestamp,
                channel=message.channel
            )
            db.add(msg)
            db.commit()
        return {"status": "success", "message": "Message saved to history."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.post("/api/chat/converse")
async def converse(
    req: ConverseRequest,
    current_user: User = Depends(get_current_user)
):
    try:
        reply = await converse_service.generate_freja_reply(
            req.text, channel=req.channel, image_base64=req.image_base64
        )
        return {"reply": reply}
    except converse_service.ProviderUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        msg = str(e)
        low = msg.lower()
        if "429" in msg or "resource_exhausted" in low or "spending cap" in low or "quota" in low:
            raise HTTPException(
                status_code=503,
                detail="Gemini-kvoten är slut just nu (spending cap). Bildanalys pausad tills kvoten återställs.",
            )
        raise HTTPException(status_code=500, detail=f"Converse failed: {msg}")

@router.post("/api/chat/clear")
async def clear_chat_history(current_user: User = Depends(get_current_user)):
    try:
        with get_db_session() as db:
            db.query(ChatHistory).filter(ChatHistory.user_id == current_user.id).delete()
            db.commit()
        return {"status": "success", "message": "Chat history cleared."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

