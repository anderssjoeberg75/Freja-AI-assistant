"""Settings API route router using FastAPI."""

from fastapi import APIRouter, HTTPException, Request
from backend.database import get_db_connection

router = APIRouter()

@router.get("/api/keys")
async def get_keys():
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT key_name, key_value FROM api_keys')
            rows = cursor.fetchall()
        
        result = {}
        for row in rows:
            name, value = row[0], row[1]
            result[name] = value.strip() if value else ""
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/keys")
async def post_keys(request: Request):
    try:
        data = await request.json()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            for key_name, key_value in data.items():
                # Skip saving if client sends back masked placeholder
                if key_value in ("[MASKED]", "configured") or (key_value and key_value.startswith("••••")):
                    continue
                cursor.execute('''
                    INSERT INTO api_keys (key_name, key_value)
                    VALUES (?, ?)
                    ON CONFLICT(key_name) DO UPDATE SET key_value = excluded.key_value
                ''', (key_name, key_value))
            conn.commit()
        return {'status': 'success'}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


