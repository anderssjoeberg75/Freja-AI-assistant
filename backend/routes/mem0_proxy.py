"""Mem0 API secure proxy route."""

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, Query
from backend.database import get_db_connection

router = APIRouter()

def get_mem0_api_key():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key_value FROM api_keys WHERE key_name = 'freja_mem0_apikey'")
        row = cursor.fetchone()
    return row[0].strip() if row else ""

@router.post("/api/mem0/add")
async def proxy_mem0_add(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    api_key = get_mem0_api_key()
    if not api_key:
        raise HTTPException(status_code=400, detail="Mem0 API key is not configured on the server.")

    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json"
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post("https://api.mem0.ai/v3/memories/add/", json=payload, headers=headers, timeout=30.0)
            return Response(content=response.content, status_code=response.status_code, media_type="application/json")
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"Failed to communicate with Mem0 API: {str(e)}")

@router.post("/api/mem0/search")
async def proxy_mem0_search(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    api_key = get_mem0_api_key()
    if not api_key:
        raise HTTPException(status_code=400, detail="Mem0 API key is not configured on the server.")

    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json"
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post("https://api.mem0.ai/v3/memories/search/", json=payload, headers=headers, timeout=30.0)
            return Response(content=response.content, status_code=response.status_code, media_type="application/json")
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"Failed to communicate with Mem0 API: {str(e)}")

@router.post("/api/mem0/all")
async def proxy_mem0_all(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    api_key = get_mem0_api_key()
    if not api_key:
        raise HTTPException(status_code=400, detail="Mem0 API key is not configured on the server.")

    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json"
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post("https://api.mem0.ai/v3/memories/", json=payload, headers=headers, timeout=30.0)
            return Response(content=response.content, status_code=response.status_code, media_type="application/json")
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"Failed to communicate with Mem0 API: {str(e)}")

@router.delete("/api/mem0/delete/{memory_id}")
async def proxy_mem0_delete(memory_id: str):
    api_key = get_mem0_api_key()
    if not api_key:
        raise HTTPException(status_code=400, detail="Mem0 API key is not configured on the server.")

    headers = {
        "Authorization": f"Token {api_key}"
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.delete(f"https://api.mem0.ai/v3/memories/{memory_id}/", headers=headers, timeout=30.0)
            return Response(content=response.content, status_code=response.status_code, media_type="application/json")
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"Failed to communicate with Mem0 API: {str(e)}")

@router.delete("/api/mem0/wipe")
async def proxy_mem0_wipe(user_id: str = Query(..., description="User ID to wipe memories for")):
    api_key = get_mem0_api_key()
    if not api_key:
        raise HTTPException(status_code=400, detail="Mem0 API key is not configured on the server.")

    headers = {
        "Authorization": f"Token {api_key}"
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.delete(f"https://api.mem0.ai/v1/memories/?user_id={user_id}", headers=headers, timeout=30.0)
            return Response(content=response.content, status_code=response.status_code, media_type="application/json")
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"Failed to communicate with Mem0 API: {str(e)}")

