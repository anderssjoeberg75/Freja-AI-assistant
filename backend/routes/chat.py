"""Chat history routes using FastAPI."""

import datetime
import sqlite3
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from backend.config import DB_FILE

router = APIRouter()

class ChatMessage(BaseModel):
    sender: str
    content: str
    channel: str = "web"

@router.get("/api/chat/history")
async def get_chat_history(limit: int = Query(50, description="Number of messages to fetch")):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT sender, content, timestamp, channel 
            FROM chat_history 
            ORDER BY id DESC 
            LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        conn.close()
        
        # Format in chronological order
        rows.reverse()
        
        results = []
        for row in rows:
            results.append({
                "sender": row[0],
                "content": row[1],
                "timestamp": row[2],
                "channel": row[3]
            })
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.post("/api/chat/message")
async def save_chat_message(message: ChatMessage):
    try:
        timestamp = datetime.datetime.now().isoformat()
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO chat_history (sender, content, timestamp, channel)
            VALUES (?, ?, ?, ?)
        ''', (message.sender, message.content, timestamp, message.channel))
        conn.commit()
        conn.close()
        return {"status": "success", "message": "Message saved to history."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.post("/api/chat/clear")
async def clear_chat_history():
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM chat_history')
        conn.commit()
        conn.close()
        return {"status": "success", "message": "Chat history cleared."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
