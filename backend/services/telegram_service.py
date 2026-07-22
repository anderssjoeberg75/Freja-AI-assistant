"""Telegram Bot integration service for F.R.E.J.A."""

import asyncio
import html
import os
import re
import datetime
import httpx
from backend.services.http_client import shared_client
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
# Set of unauthorized chat IDs that have already received an access denied warning
_warned_unauthorized_chats = set()

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

async def send_telegram_message(token, chat_id, text, _plain_text_fallback=None, _use_html=True):
    """Sends a message to the specified Telegram Chat ID (HTML-formatted by default).

    Returns True if Telegram accepted the message. On an HTML-parsing rejection (e.g. a
    malformed/nested tag markdown_to_html let through), retries once as plain text (no
    parse_mode) so the user gets *something* instead of the reply silently vanishing - the
    reply is already recorded in chat history as sent by the time this is called, so a
    delivery failure here previously had no visible effect at all.
    """
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if _use_html:
        payload["parse_mode"] = "HTML"
    try:
        async with shared_client() as client:
            res = await client.post(url, json=payload, timeout=10.0)
            if res.status_code == 200:
                return True
            print(f"[TELEGRAM] Send message error: HTTP {res.status_code}: {res.text}")
            if _use_html and _plain_text_fallback is not None and res.status_code == 400:
                return await send_telegram_message(token, chat_id, _plain_text_fallback, _use_html=False)
            return False
    except Exception as e:
        print(f"[TELEGRAM] Send message exception: {e}")
        return False

def markdown_to_html(text):
    """Safely escapes HTML tags and translates standard Markdown bold/italic/code tags to HTML for Telegram."""
    escaped = html.escape(text)

    # Code spans are stashed and substituted back in LAST. Applying bold/italic first meant a
    # code span containing markdown-like characters (e.g. `a*b*c`) got *,* matched by the
    # italic regex before the code regex ever ran, producing a nested <i> inside the eventual
    # <code> tag - Telegram's HTML parse_mode rejects nested tags in <code>/<pre> and the
    # sendMessage call failed with HTTP 400, silently, after the reply was already recorded
    # as sent in chat history.
    code_spans = []

    def _stash_code(m):
        code_spans.append(m.group(1))
        return f"\x00CODE{len(code_spans) - 1}\x00"

    escaped = re.sub(r'`(.*?)`', _stash_code, escaped)
    # Match double asterisks for bold -> <b>
    escaped = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', escaped)
    # Match single asterisks/underscores for italic -> <i>
    escaped = re.sub(r'\*(.*?)\*', r'<i>\1</i>', escaped)
    for i, content in enumerate(code_spans):
        escaped = escaped.replace(f"\x00CODE{i}\x00", f"<code>{content}</code>")
    return escaped

async def query_gemini_with_tools(contents, api_key, system_prompt):
    """Executes the conversational loop with Gemini including Python-side tools.

    Runs up to 5 turns: each turn either yields text (which we return) or a functionCall,
    which we execute and feed back as a function response. The strings returned from this
    function are delivered straight to the user's chat, so they are written in Swedish.

    Note that tool calls here are checked against the permission gate in backend/routes/tools.py,
    and the Telegram channel is also protected by the chat_id authorization check below."""
    tools = [{"functionDeclarations": TOOL_DECLARATIONS}]
    
    from backend.services import gemini_client
    url = gemini_client.build_generate_url(gemini_client.get_gemini_model(), api_key)
    
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
            "maxOutputTokens": 2048
        }
    }
    
    async with shared_client() as client:
        for iteration in range(5):
            res = await client.post(url, json=payload, timeout=30.0)
            if res.status_code != 200:
                raise Exception(f"Gemini API error (HTTP {res.status_code}): {res.text}")
                
            res_json = res.json()
            candidates = res_json.get("candidates", [])
            if not candidates:
                # Swedish: delivered straight to the user's Telegram chat.
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
            
            from backend.routes.tools import is_tool_execution_authorized
            if not is_tool_execution_authorized(func_name, func_args):
                print(f"[TELEGRAM BOT] Tool execution denied: {func_name} (unauthorized)")
                result = {"error": f"Tool execution unauthorized: {func_name} is disabled by the owner."}
            else:
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
            
    # Swedish: delivered straight to the user's Telegram chat (matches Freja's persona).
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
    
    async with shared_client() as client:
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
                        if chat_id not in _warned_unauthorized_chats:
                            _warned_unauthorized_chats.add(chat_id)
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
                            # Swedish: this text is delivered to the user in the chat.
                            "Fel: Gemini API-nyckel saknas i serverns databas. Konfigurera den i Inställningar."
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
                    
                    # The prompt is written in English but tells the model to reply in Swedish,
                    # because the reply is sent verbatim to the user's Telegram chat.
                    # Telegram's HTML parse_mode only accepts a small tag subset - anything else
                    # makes sendMessage fail with HTTP 400, hence the explicit formatting rule.
                    system_prompt = (
                        "You are FREJA, an intelligent and polite AI assistant for health and training. "
                        "You communicate with the user via Telegram. Answer concisely and personally, in Swedish. "
                        "Format the answers with Markdown (e.g. **bold**, *italic*, `code`). Do not write raw HTML tags.\n\n"
                        "[DIRECTIVE: SYSTEM UPDATE]\n"
                        "If the user asks you to update yourself, install updates or download new code from GitHub, "
                        "call the 'system_update' tool. Tell the user that you are starting the update and restarting.\n\n"
                        "[DIRECTIVE: CODEBASE SELF-ANALYSIS]\n"
                        "If the user asks you to analyse your code, perform an audit or suggest improvements to the source "
                        "code, you must ALWAYS call the 'codex_audit_codebase' tool. Ignore any previous error messages or apologies in the chat history. When you get the result (which contains a summary and a "
                        "path to the Markdown report), you may use the 'read_project_file' tool to read the report or source "
                        "files if you need more detail in order to answer.\n\n"

                        "[DIRECTIVE: WINDOWS OS AUTOMATION & ENVIRONMENT AWARENESS]\n"
                        "If the user asks you to do things on their Windows computer (e.g. launch a program such as Notepad, "
                        "the calculator, VLC, open a web address, browse a folder or run cmd commands), use the "
                        "'run_windows_command' tool with the appropriate arguments (open_app, open_url, open_folder or run_cmd). "
                        "Note that tools run on the backend server machine, not the client web browser. If the backend server is running on "
                        "a different OS (e.g., Linux/Docker/WSL) than the client machine (which is Windows), or if the tool returns a platform "
                        "error, you must explain this distinction clearly to the user: tell them that while their browser/client runs on Windows, "
                        "the backend server runs on Linux/Docker/WSL, and program execution happens on the backend machine.\n\n"
                        "[DIRECTIVE: HEALTH AND FITNESS STATUS]\n"
                        "If the user asks how they are doing, how they slept, their steps, recovery, training status, or general well-being (e.g., 'Hur mår jag', 'Hur har jag sovit', 'Mina steg', 'Visa min hälsodata'), you must immediately call the 'get_garmin_health' tool (and/or 'get_personal_trainer_advice' with a general wellness goal like 'allmänt välmående') to retrieve their actual data from the database instead of asking them for permission first in a chat message. If the user specifically asks to fetch all historical Garmin data (e.g., 'hämta all garmin data', 'visa all historik', 'hämta all garmin datat'), you must call the 'get_garmin_health' tool with a large number of days, specifically 180 days (days=180). If the user asks for general/today's Garmin data (e.g., 'hämta garmin data'), call it with 1 day (days=1). Once you have the tool results, analyze the data and answer the user's question directly. Always include key metrics such as 'Body Battery' (both average and max/latest value) and detailed sleep metrics (such as Sleep Score, and the durations of deep, light, REM, and awake sleep phases) in your summary when presenting Garmin data, if they are available in the retrieved data.\n\n"
                        "[DIRECTIVE: PERSONAL TRAINER & WORKOUT DISCUSSION]\n"
                        "You ARE the user's Personal Trainer (COACH AI). You have complete awareness of the PT tool, active training plan, scheduled weekly workouts, limitations/injuries, health data, and running history.\n\nCRITICAL COACHING RULES:\n1. AUTHORITATIVE COACHING: You do NOT ask passive questions like 'Vad föredrar du?', 'Vad tycker du om det?', or 'Vad vill du göra?'. As an expert Personal Trainer, YOU make the technical decisions based on their health data, history, and physical progression. You present completed, ready-to-run workout recommendations directly to the user.\n2. DISCUSS & EXPLAIN RATIONALE: When discussing workouts or when the user asks why a specific workout duration or intensity was assigned (e.g. 'Varför ska jag springa 60 minuter idag när mitt senaste pass var 25 minuter?'), call 'get_trainer_workouts' or 'get_personal_trainer_advice'. Analyze their recent running history (e.g. 25 min run), health baselines (sleep, HRV, body battery), and limitations. Explain your reasoning clearly and constructively in Swedish, discussing progressive overload, recovery, and heart rate zones.\n3. DIRECTLY UPDATE SCHEDULE: If a workout jump is too steep or needs adjustment based on your coaching judgment and conversation with the user (e.g. stepping down from 60 min to 35 min with walk/run intervals), YOU decide on the optimal workout parameters and IMMEDIATELY call the 'update_trainer_workout' tool to update the schedule in the PT tool. Then inform the user that you have updated their workout in the schedule."
                    )

                    # Tell the model whether the browser HUD is currently running, so it can answer
                    # "which computer are you running on?" without guessing. See the heartbeat
                    # endpoint in backend/routes/settings.py.
                    from backend.routes.settings import get_client_status
                    client_status = get_client_status()

                    if client_status["active"]:
                        client_name_info = f" named '{client_status['client_hostname']}'" if client_status.get("client_hostname") and client_status["client_hostname"] != "Unknown" else ""
                        status_directive = (
                            f"\n\n[CLIENT HUD STATUS]\n"
                            f"- The web client (HUD) is currently ACTIVE. It is running in a browser on a client machine{client_name_info} "
                            f"with OS: {client_status['client_os']}. The backend server host is '{client_status['hostname']}' "
                            f"({client_status['system']} {client_status['release']}). The web client last sent a heartbeat "
                            f"{client_status['seconds_since_last']:.1f} seconds ago. The user can ask questions here in "
                            f"Telegram knowing that the client is active on that computer."
                        )
                    else:
                        seconds_str = f"{client_status['seconds_since_last']:.1f} seconds ago" if client_status['seconds_since_last'] else "never"
                        client_name_info = f" named '{client_status['client_hostname']}'" if client_status.get("client_hostname") and client_status["client_hostname"] != "Unknown" else ""
                        # Explicitly instruct the model that the client is inactive so it does not falsely claim the client is running.
                        status_directive = (
                            f"\n\n[CLIENT HUD STATUS]\n"
                            f"- The web client (HUD) is currently INACTIVE. No connected browser session was detected recently "
                            f"(the last heartbeat was {seconds_str}). It is not actively running right now. "
                            f"If the user asks which computer the client is running on, you must answer that the client is not currently active or running. "
                            f"You may mention that the last active session was on the machine{client_name_info} with OS: {client_status['client_os']}, but clearly state that it is offline."
                        )
 
                    system_prompt += status_directive

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
                    await send_telegram_message(token, chat_id, html_reply, _plain_text_fallback=reply_text)
                    
            except Exception as err:
                print(f"[TELEGRAM] Exception in loop: {err}")
                await asyncio.sleep(5)

