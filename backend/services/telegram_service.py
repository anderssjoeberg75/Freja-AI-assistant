"""Telegram Bot integration service for F.R.E.J.A."""

import asyncio
import html
import os
import re
import datetime
import httpx
from backend.database import get_db_connection, get_api_key
from backend.config import PROJECT_ROOT
from backend.services.tool_registry import TOOL_DECLARATIONS, execute_tool
if os.name == 'nt':
    import msvcrt
else:
    import fcntl

# Active conversation history cache mapped by telegram chat_id
chat_histories = {}
# Recent message logs cache (up to 10 entries)
recent_messages = []

def get_telegram_config():
    """Fetches Telegram bot token and authorized chat_id from environment or SQLite database."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    
    try:
        if not token:
            token = get_api_key('freja_telegram_bot_token') or token
        if not chat_id:
            chat_id = get_api_key('freja_telegram_chat_id') or chat_id
    except Exception as e:
        print(f"[TELEGRAM] Config fetch error: {e}")
        
    return token, chat_id

def get_gemini_api_key():
    """Retrieves the Gemini API key from the database."""
    try:
        return get_api_key('freja_gemini_apikey') or ""
    except Exception:
        return ""

async def send_telegram_message(token, chat_id, text):
    """Sends an HTML-formatted message to the specified Telegram Chat ID."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, json=payload, timeout=10.0)
            if res.status_code != 200:
                print(f"[TELEGRAM] Send message error: HTTP {res.status_code}: {res.text}")
    except Exception as e:
        print(f"[TELEGRAM] Send message exception: {e}")

def markdown_to_html(text):
    """Safely escapes HTML tags and translates standard Markdown bold/italic/code tags to HTML for Telegram."""
    escaped = html.escape(text)
    # Match double asterisks for bold -> <b>
    escaped = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', escaped)
    # Match single asterisks/underscores for italic -> <i>
    escaped = re.sub(r'\*(.*?)\*', r'<i>\1</i>', escaped)
    # Match backticks for code -> <code>
    escaped = re.sub(r'`(.*?)`', r'<code>\1</code>', escaped)
    return escaped

async def query_gemini_with_tools(contents, api_key, system_prompt):
    """Executes the conversational loop with Gemini including Python-side tools."""
    tools = [{"functionDeclarations": TOOL_DECLARATIONS}]
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    
    # Copy contents so we don't pollute the long-term cache memory with raw function payloads
    local_contents = list(contents)
    
    payload = {
        "contents": local_contents,
        "systemInstruction": {
            "parts": [{"text": system_prompt}]
        },
        "tools": tools,
        "generationConfig": {
            "temperature": 0.5,
            "maxOutputTokens": 1000
        }
    }
    
    async with httpx.AsyncClient() as client:
        for iteration in range(5):
            res = await client.post(url, json=payload, timeout=30.0)
            if res.status_code != 200:
                raise Exception(f"Gemini API error (HTTP {res.status_code}): {res.text}")
                
            res_json = res.json()
            candidates = res_json.get("candidates", [])
            if not candidates:
                return "Inget svar returnerades från Gemini."
                
            candidate = candidates[0]
            content = candidate.get("content", {})
            parts = content.get("parts", [])
            
            # Append response to contents history
            payload["contents"].append(content)
            
            func_calls = [p.get("functionCall") for p in parts if p.get("functionCall")]
            if not func_calls:
                # We got a text response
                text_parts = [p.get("text", "") for p in parts if p.get("text")]
                return "".join(text_parts)
                
            # Handle function call
            func_call = func_calls[0]
            func_name = func_call.get("name")
            func_args = func_call.get("args", {})
            
            print(f"[TELEGRAM BOT] Gemini calling tool: {func_name} with args: {func_args}")
            
            result = await execute_tool(func_name, func_args)
                
            # Append function response to payload contents
            payload["contents"].append({
                "role": "function",
                "parts": [{
                    "functionResponse": {
                        "name": func_name,
                        "response": {"result": result}
                    }
                }]
            })
            
    return "F.R.E.J.A. avbröt konversationen: för många funktionsanrop (loop limit nådd)."

telegram_lock_file = None

async def telegram_worker_loop():
    """Asynchronous Telegram Bot update polling loop."""
    global telegram_lock_file
    
    # Try to acquire process-level file lock to prevent multi-worker conflicts
    lock_file_path = os.path.join(PROJECT_ROOT, ".telegram_bot.lock")
    try:
        telegram_lock_file = open(lock_file_path, "w")
        if os.name == 'nt':
            telegram_lock_file.seek(0)
            msvcrt.locking(telegram_lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(telegram_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, PermissionError, OSError):
        print("[TELEGRAM] Another server process is already running the Telegram Bot. Skipping polling thread.")
        return
    except Exception as e:
        print(f"[TELEGRAM] Lock file initialization failed: {e}")
        
    print("[TELEGRAM] Background polling worker thread initialized.")
    offset = 0
    
    async with httpx.AsyncClient() as client:
        while True:
            try:
                token, auth_chat_id = get_telegram_config()
                if not token:
                    await asyncio.sleep(10)
                    continue
                    
                if not auth_chat_id:
                    print("[TELEGRAM] Warning: 'freja_telegram_chat_id' is missing. Locking Telegram bot for security.")
                    await asyncio.sleep(10)
                    continue
                    
                # Fetch Telegram messages
                url = f"https://api.telegram.org/bot{token}/getUpdates"
                params = {"offset": offset, "timeout": 20}
                
                response = await client.get(url, params=params, timeout=25.0)
                
                if response.status_code != 200:
                    print(f"[TELEGRAM] Polling failed: HTTP {response.status_code}")
                    await asyncio.sleep(5)
                    continue
                    
                res_json = response.json()
                updates = res_json.get("result", [])
                
                for update in updates:
                    update_id = update.get("update_id")
                    offset = update_id + 1
                    
                    message = update.get("message")
                    if not message:
                        continue
                        
                    chat = message.get("chat", {})
                    chat_id = str(chat.get("id"))
                    text = message.get("text", "")
                    
                    if not text:
                        continue
                        
                    print(f"[TELEGRAM] Message received from {chat_id}: '{text}'")
                    
                    # Append to recent message cache
                    current_time = datetime.datetime.now().strftime("%H:%M:%S")
                    recent_messages.append({
                        "time": current_time,
                        "chat_id": chat_id,
                        "text": text[:60] + ("..." if len(text) > 60 else ""),
                        "authorized": chat_id == auth_chat_id
                    })
                    if len(recent_messages) > 10:
                        recent_messages.pop(0)
                    
                    # Enforce chat ID authorization check
                    if chat_id != auth_chat_id:
                        print(f"[TELEGRAM] Unauthorized access attempt from chat_id {chat_id} (Configured: {auth_chat_id})")
                        # Send one-time access denied message
                        await send_telegram_message(
                            token,
                            chat_id,
                            "Access Denied: This F.R.E.J.A. bot instance is locked to the owner's account."
                        )
                        continue
                        
                    # Save user message to persistent DB history
                    try:
                        with get_db_connection() as conn:
                            cursor = conn.cursor()
                            cursor.execute('''
                                INSERT INTO chat_history (sender, content, timestamp, channel)
                                VALUES (?, ?, ?, ?)
                            ''', ("user", text, datetime.datetime.now().isoformat(), "telegram"))
                            conn.commit()
                    except Exception as db_err:
                        print(f"[TELEGRAM] Error saving user message to database: {db_err}")

                    gemini_key = get_gemini_api_key()
                    if not gemini_key:
                        await send_telegram_message(
                            token,
                            chat_id,
                            "Error: Gemini API-nyckel saknas i serverns databas. Konfigurera den i Inställningar."
                        )
                        continue
                        
                    # Load conversation history
                    if chat_id not in chat_histories:
                        chat_histories[chat_id] = []
                        
                    # Trim history to 15 messages max
                    if len(chat_histories[chat_id]) > 15:
                        chat_histories[chat_id] = chat_histories[chat_id][-15:]
                        while chat_histories[chat_id] and chat_histories[chat_id][0].get("role") != "user":
                            chat_histories[chat_id].pop(0)
                            
                    # Append user prompt
                    chat_histories[chat_id].append({
                        "role": "user",
                        "parts": [{"text": text}]
                    })
                    
                    system_prompt = (
                        "Du är FREJA, en intelligent och artig AI-assistent för hälsa och träning. "
                        "Du kommunicerar med användaren via Telegram. Svara kortfattat och personligt på svenska. "
                        "Formatera svaren med ren HTML (t.ex. <b>fet</b>, <i>kursiv</i>, <code>kod</code>, inga ogiltiga taggar).\n\n"
                        "[DIRECTIVE: SYSTEM UPDATE]\n"
                        "Om användaren ber dig att uppdatera dig, installera uppdateringar eller ladda ner ny kod från GitHub, ska du anropa verktyget 'system_update'. Berätta för användaren att du påbörjar uppdateringen och startar om.\n\n"
                        "[DIRECTIVE: CODEBASE SELF-ANALYSIS]\n"
                        "Om användaren ber dig att analysera din kod, göra en granskning (audit) eller komma med förbättringsförslag på källkoden, ska du anropa verktyget 'codex_audit_codebase'. När du får resultatet (som innehåller en sammanfattning och sökväg till Markdown-rapporten), kan du använda verktyget 'read_project_file' för att läsa rapporten eller källkodsfiler om du behöver mer detaljer för att svara.\n\n"
                        "[DIRECTIVE: WINDOWS OS AUTOMATION]\n"
                        "Om användaren ber dig att utföra saker på sin Windows-dator (t.ex. starta ett program som Notepad eller kalkylatorn, öppna en webbadress, utforska en mapp eller köra cmd-kommandon), ska du använda verktyget 'run_windows_command' med lämpliga argument (open_app, open_url, open_folder eller run_cmd)."
                    )


                    
                    try:
                        reply_text = await query_gemini_with_tools(
                            chat_histories[chat_id],
                            gemini_key,
                            system_prompt
                        )
                    except Exception as e:
                        print(f"[TELEGRAM] Response generation failed: {e}")
                        reply_text = f"Ett fel uppstod vid generering av svar: {str(e)}"
                        
                    # Cache model response
                    chat_histories[chat_id].append({
                        "role": "model",
                        "parts": [{"text": reply_text}]
                    })
                    
                    # Save assistant response to persistent DB history
                    try:
                        with get_db_connection() as conn:
                            cursor = conn.cursor()
                            cursor.execute('''
                                INSERT INTO chat_history (sender, content, timestamp, channel)
                                VALUES (?, ?, ?, ?)
                            ''', ("assistant", reply_text, datetime.datetime.now().isoformat(), "telegram"))
                            conn.commit()
                    except Exception as db_err:
                        print(f"[TELEGRAM] Error saving assistant response to database: {db_err}")

                    # Format response and transmit to Telegram
                    html_reply = markdown_to_html(reply_text)
                    await send_telegram_message(token, chat_id, html_reply)
                    
            except Exception as err:
                print(f"[TELEGRAM] Exception in loop: {err}")
                await asyncio.sleep(5)

