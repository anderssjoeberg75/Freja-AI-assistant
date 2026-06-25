"""FastAPI routes for Freja's learning module and domain credentials management."""

import re
import json
from fastapi import APIRouter, HTTPException, Request
from backend.database import get_db_connection

router = APIRouter()

@router.get("/api/learning/list")
async def get_learned_list():
    """Returns a list of all learned knowledge entries."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, topic, summary, detailed_notes, sources, timestamp 
                FROM learned_knowledge 
                ORDER BY timestamp DESC
            ''')
            rows = cursor.fetchall()
            
        results = []
        for row in rows:
            sources_list = []
            try:
                if row[4]:
                    sources_list = json.loads(row[4])
            except Exception:
                pass
            results.append({
                "id": row[0],
                "topic": row[1],
                "summary": row[2],
                "detailed_notes": row[3],
                "sources": sources_list,
                "timestamp": row[4]
            })
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/api/learning/delete/{knowledge_id}")
async def delete_learned_entry(knowledge_id: int):
    """Deletes a learned knowledge entry by ID."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM learned_knowledge WHERE id = ?", (knowledge_id,))
            conn.commit()
        return {"status": "success", "message": "Kunskap raderad."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/learning/credentials")
async def get_learning_credentials():
    """Lists all domains configured with login credentials (passwords masked)."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key_name, key_value FROM api_keys WHERE key_name LIKE 'login_domain_%'")
            rows = cursor.fetchall()
            
        credentials = []
        for row in rows:
            domain_key = row[0]
            domain_name = row[1]
            clean_domain = domain_key.replace("login_domain_", "")
            
            # Fetch user for this domain
            user_key = f"login_user_{clean_domain}"
            with get_db_connection() as conn2:
                cur2 = conn2.cursor()
                cur2.execute("SELECT key_value FROM api_keys WHERE key_name = ?", (user_key,))
                user_row = cur2.fetchone()
                
            username = user_row[0] if user_row else ""
            credentials.append({
                "domain": domain_name,
                "clean_domain": clean_domain,
                "username": username,
                "password": "[MASKED]"
            })
        return credentials
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/learning/credentials")
async def post_learning_credentials(request: Request):
    """Saves or updates login credentials for a specific domain."""
    try:
        body = await request.json()
        domain = body.get("domain", "").strip()
        username = body.get("username", "").strip()
        password = body.get("password", "").strip()
        
        if not domain or not username or not password:
            raise HTTPException(status_code=400, detail="Domän, användarnamn och lösenord krävs.")
            
        # Normalize domain for database key naming
        clean_domain = re.sub(r'[^a-zA-Z0-9]', '_', domain).lower()
        
        domain_key = f"login_domain_{clean_domain}"
        user_key = f"login_user_{clean_domain}"
        pass_key = f"login_pass_{clean_domain}"
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO api_keys (key_name, key_value) VALUES (?, ?)
                ON CONFLICT(key_name) DO UPDATE SET key_value = excluded.key_value
            ''', (domain_key, domain))
            cursor.execute('''
                INSERT INTO api_keys (key_name, key_value) VALUES (?, ?)
                ON CONFLICT(key_name) DO UPDATE SET key_value = excluded.key_value
            ''', (user_key, username))
            cursor.execute('''
                INSERT INTO api_keys (key_name, key_value) VALUES (?, ?)
                ON CONFLICT(key_name) DO UPDATE SET key_value = excluded.key_value
            ''', (pass_key, password))
            conn.commit()
            
        return {"status": "success", "message": f"Autentiseringsuppgifter för {domain} sparade."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/api/learning/credentials/{clean_domain}")
async def delete_learning_credentials(clean_domain: str):
    """Deletes login credentials for a specific domain."""
    try:
        domain_key = f"login_domain_{clean_domain}"
        user_key = f"login_user_{clean_domain}"
        pass_key = f"login_pass_{clean_domain}"
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM api_keys WHERE key_name IN (?, ?, ?)", (domain_key, user_key, pass_key))
            conn.commit()
            
        return {"status": "success", "message": "Autentiseringsuppgifter raderade."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
