"""Learning Service for background topic synthesis using Google Search, Playwright scraping, and Gemini API."""

import os
import re
import json
import datetime
import urllib.parse
import httpx
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright
from backend.config import PROJECT_ROOT
from backend.database import get_db_connection, get_api_key
from backend.services.search_service import perform_search

ABORT_LEARNING = False
ACTIVE_PAGE = None

def cancel_learning():
    """Cancels the active learning background task."""
    global ABORT_LEARNING, ACTIVE_PAGE
    ABORT_LEARNING = True
    print("[Learning Service] Abort signal received.")
    if ACTIVE_PAGE:
        print("[Learning Service] Closing active page to abort scraping.")
        try:
            asyncio.create_task(ACTIVE_PAGE.close())
        except Exception as e:
            print(f"[Learning Service] Error closing active page: {e}")

def get_domain_credentials(domain: str):
    """Retrieves username and password for a given domain from database."""
    clean_domain = re.sub(r'[^a-zA-Z0-9]', '_', domain).lower()
    username_key = f"login_user_{clean_domain}"
    password_key = f"login_pass_{clean_domain}"

    user = get_api_key(username_key)
    pwd = get_api_key(password_key)
    return user, pwd

async def call_gemini_learning_api(prompt: str, system_instruction: str = "") -> str:
    """Helper to query official Gemini API for learning synthesis."""
    api_key = get_api_key('freja_gemini_apikey') or ""
    if not api_key:
        raise Exception("The Gemini API key is missing from the database. Configure it in Settings.")
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json"
        }
    }
    if system_instruction:
        payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, timeout=60.0)
        resp.raise_for_status()
        resp_json = resp.json()
        
    text = resp_json.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
    return text

async def learn_topic_impl(topic: str, progress_callback=None) -> dict:
    """
    Learns about a topic by searching Google/DuckDuckGo, scraping top pages (supporting credentials),
    synthesizing with Gemini, and persisting the knowledge in SQLite.
    """
    global ABORT_LEARNING, ACTIVE_PAGE
    ABORT_LEARNING = False
    
    if progress_callback:
        progress_callback(5, 100, f"Searching the web for '{topic}'...")
        
    # 1. Search the web
    search_results = await perform_search(topic)
    if isinstance(search_results, dict) and "error" in search_results:
        raise Exception(f"Search failed: {search_results['error']}")
        
    if not search_results:
        raise Exception("Found no web search results for this topic.")
        
    top_results = search_results[:3]
    scraped_data = []
    sources = []
    
    # 2. Scrape top results using Playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        for idx, res in enumerate(top_results):
            if ABORT_LEARNING:
                await browser.close()
                return {"status": "cancelled"}
                
            title = res.get("title", "Source")
            link = res.get("link", "")
            if not link:
                continue
                
            domain = urllib.parse.urlparse(link).netloc
            sources.append({"title": title, "url": link})
            
            if progress_callback:
                progress_callback(20 + idx * 20, 100, f"Reading: {domain}...")
                
            try:
                page = await browser.new_page()
                ACTIVE_PAGE = page
                
                await page.goto(link, timeout=25000, wait_until="domcontentloaded")
                
                # Check for login fields
                password_field = await page.query_selector("input[type='password']")
                if password_field or "login" in page.url.lower() or "signin" in page.url.lower():
                    # Attempt login using stored credentials
                    user, pwd = get_domain_credentials(domain)
                    if user and pwd:
                        print(f"[Learning Service] Auto-logging into {domain}...")
                        user_field = await page.query_selector("input[type='email'], input[type='text'], input[name*='user'], input[name*='login']")
                        if user_field:
                            await user_field.fill(user)
                            await password_field.fill(pwd)
                            
                            # Find and click login button
                            submit_btn = await page.query_selector("button[type='submit'], input[type='submit'], button:has-text('Logga in'), button:has-text('Login')")
                            if submit_btn:
                                await submit_btn.click()
                                await page.wait_for_load_state("networkidle", timeout=15000)
                    else:
                        print(f"[Learning Service] Site requires login but no credentials found for {domain}")
                
                # Extract text cap to 8000 characters
                text_content = await page.evaluate("() => document.body.innerText")
                clean_text = re.sub(r'\s+', ' ', text_content).strip()
                scraped_data.append(f"SOURCE: {title} ({link})\nCONTENT:\n{clean_text[:8000]}")
                
                await page.close()
                ACTIVE_PAGE = None
                
            except Exception as scrape_err:
                print(f"[Learning Service] Failed to scrape {link}: {scrape_err}")
                
        await browser.close()
        
    if ABORT_LEARNING:
        return {"status": "cancelled"}
        
    if not scraped_data:
        raise Exception("Could not read or extract text from any of the search results.")
        
    # 3. Call Gemini to synthesize
    if progress_callback:
        progress_callback(80, 100, "Synthesizing information with Gemini AI...")
        
    # The notes are stored and later read back to the user, so the model must write Swedish.
    # The response is parsed with json.loads(), hence the strict "valid JSON object" wording.
    system_prompt = """You are Freja's learning module. Analyse the following text collected from the web
about the topic. Synthesize the information and produce a high-quality, well-structured summary and
detailed notes, written in SWEDISH.

You MUST answer with a valid JSON object having exactly the following fields:
{
  "summary": "A short, dense summary (2-4 sentences), in Swedish.",
  "detailed_notes": "Detailed structured notes with tips, advice, step-by-step instructions and key facts (Markdown format, in Swedish)."
}
"""

    prompt = f"Topic: {topic}\n\nCollected source information:\n\n" + "\n\n---\n\n".join(scraped_data)
    
    try:
        response_text = await call_gemini_learning_api(prompt, system_prompt)
        
        # Clean potential markdown wrapping if returned
        if response_text.startswith("```json"):
            response_text = response_text.split("```json", 1)[1]
        if response_text.startswith("```"):
            response_text = response_text.split("```", 1)[1]
        if response_text.endswith("```"):
            response_text = response_text.rsplit("```", 1)[0]
            
        data = json.loads(response_text.strip())
        summary = data.get("summary", "")
        detailed_notes = data.get("detailed_notes", "")
    except Exception as gemini_err:
        raise Exception(f"Gemini analysis failed: {gemini_err}")
        
    # 4. Save to Database
    if progress_callback:
        progress_callback(95, 100, "Saving learned knowledge to the database...")
        
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sources_json = json.dumps(sources)
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO learned_knowledge (topic, summary, detailed_notes, sources, timestamp)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(topic) DO UPDATE SET
                summary = excluded.summary,
                detailed_notes = excluded.detailed_notes,
                sources = excluded.sources,
                timestamp = excluded.timestamp
        ''', (topic, summary, detailed_notes, sources_json, timestamp))
        conn.commit()
        
    if progress_callback:
        progress_callback(100, 100, "Learning complete.")
        
    return {
        "status": "success",
        "topic": topic,
        "summary": summary,
        "detailed_notes": detailed_notes,
        "sources": sources
    }
