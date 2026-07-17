/**
 * F.R.E.J.A. Client HUD - browser-side orchestration.
 *
 * This file does three separate jobs, in order:
 *   1. A global `fetch` wrapper that rewrites relative /api/ URLs to the configured backend
 *      and attaches the X-Freja-Token header. It runs before any module loads, so every
 *      later `fetch('/api/...')` in the codebase is authenticated without extra plumbing.
 *   2. `sendMessage()` - the path a user utterance takes: cancel-command detection, then
 *      vision-keyword detection (should a webcam frame be attached?), then Gemini.
 *   3. Boot-time wiring of the UI controller, speech engine, camera and visualizer.
 *
 * Note: the cancel/vision keyword lists are Swedish because the user speaks Swedish to Freja.
 * They match user input, not UI copy - see the comment at each list.
 */

// GET /api/keys masks every sensitive value as bullets (see get_all_api_keys in
// backend/database.py), so a mask must never be cached or sent as if it were a real secret.
// Mirrors the guard in POST /api/keys (backend/routes/settings.py).
const FREJA_MASK_PREFIX = "••••";
function isMaskedValue(value) {
    return typeof value === "string" && value.startsWith(FREJA_MASK_PREFIX);
}

// Intercept all fetch requests to inject X-Freja-Token automatically for F.R.E.J.A. API endpoints.
window.originalFetch = window.fetch;
window.fetch = async function(url, options = {}) {
    let urlStr = typeof url === 'string' ? url : (url instanceof Request ? url.url : '');
    
    // Get backend base URL from localStorage (strip trailing slash if present)
    // Default to port 8000 if client is running standalone on port 5000
    let backendUrl = (localStorage.getItem('freja_backend_url') || '').replace(/\/$/, '').trim();
    if (backendUrl && !backendUrl.startsWith('http://') && !backendUrl.startsWith('https://') && !backendUrl.startsWith('//')) {
        backendUrl = window.location.protocol + '//' + backendUrl;
    }
    if (!backendUrl && window.location.port === '5000') {
        backendUrl = window.location.protocol + '//' + window.location.hostname + ':8000';
    }
    
    // Rewrite relative /api/ URLs to point to the backend if backendUrl is configured or defaulted
    if (backendUrl && typeof url === 'string' && url.startsWith('/api/')) {
        url = backendUrl + url;
        urlStr = url;
    }
    
    // Append header only for F.R.E.J.A. backend api endpoints, excluding external URLs
    const isBackendApi = urlStr.includes('/api/') && (
        (backendUrl && urlStr.startsWith(backendUrl + '/api/')) ||
        urlStr.startsWith('/api/') ||
        (!urlStr.startsWith('http') || urlStr.startsWith(window.location.origin + '/api/'))
    );
    
    if (isBackendApi) {
        // No fallback to a legacy default: the backend seeds a random token per-install
        // and rejects unknown/missing tokens, so an empty value here just surfaces a 401
        // until the real token (shown in the server console on first run) is entered in Settings.
        let token = localStorage.getItem('freja_access_token') || '';
        if (isMaskedValue(token)) {
            // Self-heal installs that cached the mask before this was guarded: the bullet is
            // U+2022, which is not ISO-8859-1, so sending it throws before the request leaves
            // the browser. Dropping it yields an actionable 401 login prompt instead.
            localStorage.removeItem('freja_access_token');
            token = '';
        }
        options.headers = options.headers || {};
        
        if (options.headers instanceof Headers) {
            options.headers.set('X-Freja-Token', token);
        } else if (Array.isArray(options.headers)) {
            const index = options.headers.findIndex(([k]) => k.toLowerCase() === 'x-freja-token');
            if (index !== -1) {
                options.headers[index][1] = token;
            } else {
                options.headers.push(['X-Freja-Token', token]);
            }
        } else {
            options.headers['X-Freja-Token'] = token;
        }
    }
    
    try {
        const response = await window.originalFetch(url, options);
        if (response.status === 401 && isBackendApi) {
            console.warn("[AUTH] Backend 401 Unauthorized for URL:", urlStr);
            const loginModal = document.getElementById('modal-auth-login');
            if (loginModal) loginModal.classList.add('active');
        }
        return response;
    } catch (err) {
        throw err;
    }
};

// PKCE Helpers for OAuth 2.0 (e.g. Google Calendar)
function dec2hex(dec) {
    return ('0' + dec.toString(16)).substr(-2);
}
function generateCodeVerifier() {
    var array = new Uint32Array(56 / 2);
    window.crypto.getRandomValues(array);
    return Array.from(array, dec2hex).join('');
}
function sha256(plain) {
    const encoder = new TextEncoder();
    const data = encoder.encode(plain);
    return window.crypto.subtle.digest('SHA-256', data);
}
function base64urlencode(a) {
    var str = "";
    var bytes = new Uint8Array(a);
    var len = bytes.byteLength;
    for (var i = 0; i < len; i++) {
        str += String.fromCharCode(bytes[i]);
    }
    return btoa(str)
        .replace(/\+/g, "-")
        .replace(/\//g, "_")
        .replace(/=+$/, "");
}
async function generateCodeChallenge(v) {
    var hashed = await sha256(v);
    return base64urlencode(hashed);
}

/**
 * F.R.E.J.A. - Central Orchestrator & UI Controller
 */
class FrejaUIController {
    constructor() {
        // Instantiate the core cognitive sub-modules
        this.gemini = new GeminiClient();
        this.memory = new FrejaMemoryEngine(this.gemini);
        this.speech = new FrejaSpeechEngine();
        
        // Asynchronously load keys from SQLite and then initialize UI
        this.initAsync();
        
        // Keep systems kronometer clocks in sync
        setInterval(() => this.updateTimeAndDate(), 1000);
    }

    async initAsync() {
        // Initialize UI and bind event listeners immediately so the interface (including the start button)
        // is functional right away without waiting for any network/API responses.
        this.initializeUI();
        this.bindEvents();

        try {
            await this.loadKeysFromServer();
            // Re-run UI initialization to populate the configuration fields with the retrieved keys.
            this.initializeUI();
        } catch (e) {
            console.error("[FREJA] Failed to load initial keys:", e);
        }

        this.startHeartbeatLoop();
        await this.loadChatHistory();
        this.startDiagnosticSimulation();
        this.updateTimeAndDate();
        await this.checkActiveSyncs();
    }

    async checkActiveSyncs() {
        try {
            const res = await fetch('/api/sync/status');
            if (res.ok) {
                const statusData = await res.json();
                ['garmin', 'strava', 'withings', 'google_calendar'].forEach(provider => {
                    if (statusData.states && statusData.states[provider] === 'syncing') {
                        this.pollSyncStatus(provider);
                    }
                });
            }
        } catch (err) {
            console.error("Error checking active syncs:", err);
        }
    }

    /**
     * Fetches API keys from the secure SQLite database server and saves them to local cache.
     */
    async loadKeysFromServer() {
        try {
            this.writeLog("CONNECTING TO SECURE DATABASE...", "sys");
            const response = await fetch('/api/keys?unmask=true');
            if (response.ok) {
                const keys = await response.json();
                // A sensitive key comes back masked, and a mask must not overwrite the real
                // cached value — for freja_access_token that would break every later
                // authenticated fetch, since the bullet is not an ISO-8859-1 header value.
                const MIRRORED_KEYS = [
                    "freja_access_token",
                    "freja_gemini_apikey",
                    "freja_eleven_apikey",
                    "freja_mem0_apikey",
                    "freja_garmin_email",
                    "freja_garmin_password",
                    "freja_strava_client_id",
                    "freja_strava_client_secret",
                    "freja_strava_refresh_token",
                    "freja_withings_client_id",
                    "freja_withings_client_secret",
                    "freja_withings_refresh_token",
                    "freja_google_calendar_client_id",
                    "freja_google_calendar_client_secret",
                    "freja_google_calendar_refresh_token"
                ];
                for (const name of MIRRORED_KEYS) {
                    const value = keys[name];
                    if (value === undefined || isMaskedValue(value)) continue;
                    localStorage.setItem(name, value);
                    
                    // Also populate the corresponding UI input field if it exists
                    const inputId = name.replace(/^freja_/, 'input-').replace(/_/g, '-');
                    const inputEl = document.getElementById(inputId);
                    if (inputEl) {
                        inputEl.value = value;
                        inputEl.dispatchEvent(new Event('input'));
                        inputEl.dispatchEvent(new Event('change'));
                    }
                }

                // Refresh components keys if already instantiated
                if (this.gemini) this.gemini.loadApiKey();
                if (this.memory) this.memory.loadSettings();
                if (this.speech) this.speech.elevenApiKey = localStorage.getItem("freja_eleven_apikey") || "";
                
                this.writeLog("API KEYS SYNCHRONIZED WITH DATABASE", "sys");
            } else {
                this.writeLog("SECURE UPLINK ERROR: FALLING BACK TO LOCAL CACHE", "warn");
            }
        } catch (e) {
            console.error("[FREJA] Failed to load keys from server:", e);
            this.writeLog("SECURE UPLINK ERROR: FALLING BACK TO LOCAL CACHE", "warn");
        }
    }

    /**
     * Saves API keys to the secure SQLite database server.
     */
    async saveKeysToServer(keys) {
        try {
            this.writeLog("SAVING API KEYS TO SECURE DATABASE...", "sys");
            const response = await fetch('/api/keys', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(keys)
            });
            if (response.ok) {
                this.writeLog("API KEYS SECURED IN DATABASE", "sys");
                return true;
            } else {
                this.writeLog("API KEYS SAVE ERROR: DATABASE OFFLINE", "err");
                return false;
            }
        } catch (e) {
            console.error("[FREJA] Failed to save keys to server:", e);
            this.writeLog("API KEYS SAVE ERROR: CONNECTION FAILED", "err");
            return false;
        }
    }

    /**
     * Switches the page styling accent themes classes.
     */
    applyTheme(theme) {
        window.FrejaTheme.applyTheme(theme);
    }

    /**
     * Resolves the canvas accent hue angle based on the selected CSS theme.
     */
    getCurrentThemeHue() {
        return window.FrejaTheme.getCurrentThemeHue();
    }

    /**
     * Executes conversational transactions, drawing replies and managing long-term memory encodes.
     */
    async processUserQuery(text) {
        const cleanText = text.trim().toLowerCase();
        const cleanTextNoPunct = cleanText.replace(/[.,\/#!$%\^&\*;:{}=\-_`~()?]/g, "");
        // Swedish stop-words the user actually speaks: "avbryt" = cancel, "stoppa"/"sluta" = stop.
        // "av bryt" catches the speech recognizer splitting the word into two tokens. These match
        // user input, so they must stay Swedish.
        const containsCancelWord =
            cleanTextNoPunct.includes("avbryt") ||
            cleanTextNoPunct.includes("av bryt") ||
            cleanTextNoPunct.includes("stoppa") ||
            cleanTextNoPunct.includes("sluta");

        const isCancelCommand = 
            /^(avbryt|av\s+bryt|stoppa|sluta)(\s+(nedladdning(en)?|ladd(a|ning)?(\s+ner)?|hämtning(en)?|bild(er|erna)?|facebook))?$/i.test(cleanTextNoPunct) ||
            cleanTextNoPunct.includes("avbryt nedladdning") ||
            cleanTextNoPunct.includes("av bryt nedladdning") ||
            cleanTextNoPunct.includes("avbryt nedladdningen") ||
            cleanTextNoPunct.includes("av bryt nedladdningen") ||
            cleanTextNoPunct.includes("avbryt bildnedladdning") ||
            cleanTextNoPunct.includes("av bryt bildnedladdning") ||
            cleanTextNoPunct.includes("avbryt bildnedladdningen") ||
            cleanTextNoPunct.includes("av bryt bildnedladdningen") ||
            cleanTextNoPunct.includes("sluta ladda ner") ||
            cleanTextNoPunct.includes("stoppa nedladdning") ||
            cleanTextNoPunct.includes("stoppa nedladdningen") ||
            (this.facebookDownloadInterval && containsCancelWord);

        if (isCancelCommand) {
            this.writeLog("USER COMMAND DETECTED: CANCEL DOWNLOAD", "sys");
            try {
                const res = await fetch("/api/tools/cancel_download", { method: "POST" });
                if (res.ok) {
                    this.writeLog("DOWNLOAD CANCELLED BY USER", "sys");
                    const reply = "Nedladdningen har avbrutits.";
                    this.appendChatMessage("assistant", reply, true);
                    await this.speech.speak(reply);
                    if (window.visualizer) {
                        window.visualizer.state = 'SLEEPING';
                    }
                    return;
                }
            } catch (err) {
                console.error("Failed to cancel download:", err);
            }
        }

        if (window.visualizer) {
            window.visualizer.state = 'PROCESSING';
        }
        
        this.writeLog("NEURAL COGNITION UPLINK ENGAGED", "gemini");
        
        // Determine whether to attach webcam snapshot
        let attachImage = false;
        const selectCam = document.getElementById('select-camera');
        if (selectCam && selectCam.value !== 'off') {
            const chkAutoOptics = document.getElementById('chk-auto-optics');
            const autoOptics = chkAutoOptics ? chkAutoOptics.checked : true;
            
            if (autoOptics) {
                attachImage = true;
            } else {
                // Heuristic vision keyword check. The user speaks Swedish to Freja, so the
                // Swedish triggers are matched alongside the English ones. These are user input,
                // not UI copy - translating them would break voice control.
                const visionKeywords = [
                    'se', 'titta', 'kamera', 'bild', 'vad är det', 'vem är det',
                    'look', 'see', 'camera', 'picture', 'photo', 'what is this', 'who is this',
                    'scanna', 'scan', 'analysera bild', 'analyze picture'
                ];
                const cleanText = text.toLowerCase();
                attachImage = visionKeywords.some(keyword => cleanText.includes(keyword));
                if (attachImage) {
                    this.writeLog("OPTICS KEYWORD DETECTED. ATTACHING CAMERA FRAME", "sys");
                }
            }
        }
        
        // Request response from Google Gemini Client
        const response = await this.gemini.generateResponse(text, attachImage);
        
        this.writeLog("RESPONSE SECURED. INITIATING AUDIO SYNTHESIS", "gemini");
        this.appendChatMessage("assistant", response, true);
        
        // Synthesize response speech audio
        await this.speech.speak(response);

        // Add exchange to memory store asynchronously in background
        if (this.memory && this.memory.enabled) {
            const self = this;
            this.memory.addMemory(text, response).then((res) => {
                if (res) {
                    self.writeLog("NEURAL ENGRAM ENCODED SECURELY", "sys");
                }
            }).catch(e => {
                console.warn("[MEM0] Background memory extraction failed:", e);
            });
        }
    }

    /**
     * Draws chat message nodes on the central panels chat log container.
     */
    async saveChatMessage(sender, content) {
        try {
            await fetch('/api/chat/message', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sender, content, channel: 'web' })
            });
        } catch (e) {
            console.error("Failed to save chat message:", e);
        }
    }

    async loadChatHistory() {
        try {
            const response = await fetch('/api/chat/history');
            if (response.ok) {
                const history = await response.json();
                if (history && history.length > 0) {
                    const chatHistory = document.getElementById('chat-history');
                    chatHistory.innerHTML = "";
                    this.gemini.history = []; // Clear current session history to avoid duplicate entries
                    history.forEach(msg => {
                        this.appendChatMessage(msg.sender, msg.content, false);
                        const role = msg.sender === 'user' ? 'user' : 'model';
                        this.gemini.history.push({
                            role: role,
                            parts: [{ text: msg.content }]
                        });
                    });
                    this.hasLoadedHistory = true;
                } else {
                    this.hasLoadedHistory = false;
                }
            }
        } catch (e) {
            console.error("Failed to load chat history:", e);
        }
    }

    appendChatMessage(sender, text, saveToDb = false) {
        const chatHistory = document.getElementById('chat-history');
        const msgDiv = document.createElement('div');
        msgDiv.className = `chat-msg ${sender}-msg`;
        
        const senderTag = sender === 'user' ? '[USER]' : '[FREJA]';
        
        // Quick custom regex parser for markdown elements
        const formattedText = this.parseMarkdown(text);
        
        if (sender === 'assistant') {
            msgDiv.innerHTML = `
                <div class="msg-sender">${senderTag}</div>
                <div class="msg-content" style="position: relative; padding-right: 28px;">
                    ${formattedText}
                    <button class="btn-copy-msg" title="Kopiera svar" style="position: absolute; top: 8px; right: 8px; background: transparent; border: none; color: var(--color-text-muted); cursor: pointer; font-size: 11px; transition: color 0.2s;" onmouseover="this.style.color='var(--color-primary)'" onmouseout="this.style.color='var(--color-text-muted)'">
                        <i class="fa-regular fa-copy"></i>
                    </button>
                </div>
            `;
            const copyBtn = msgDiv.querySelector('.btn-copy-msg');
            if (copyBtn) {
                copyBtn.addEventListener('click', () => {
                    navigator.clipboard.writeText(text);
                    if (window.soundSynth) {
                        window.soundSynth.playClick();
                    }
                    const icon = copyBtn.querySelector('i');
                    icon.className = 'fa-solid fa-check';
                    copyBtn.style.color = '#00ff66';
                    setTimeout(() => {
                        icon.className = 'fa-regular fa-copy';
                        copyBtn.style.color = 'var(--color-text-muted)';
                    }, 2000);
                });
            }
        } else {
            msgDiv.innerHTML = `
                <div class="msg-sender">${senderTag}</div>
                <div class="msg-content">${formattedText}</div>
            `;
        }
        
        chatHistory.appendChild(msgDiv);
        chatHistory.scrollTop = chatHistory.scrollHeight;

        if (saveToDb) {
            this.saveChatMessage(sender, text);
        }
    }

    /**
     * Translates custom bold lists, ticks, and code block formatting to raw HTML tags.
     */
    parseMarkdown(text) {
        return window.FrejaMarkdown.parseMarkdown(text);
    }

    /**
     * Copies code content from code blocks to user clipboard.
     */
    copyCode(button) {
        window.FrejaMarkdown.copyCode(button);
    }

    /**
     * Sanitizes strings to prevent XSS injection.
     */
    escapeHTML(text) {
        return window.FrejaMarkdown.escapeHTML(text);
    }

    /**
     * Appends a glowing operational tag row into the console logs console terminal.
     */
    writeLog(msg, type = 'sys') {
        window.FrejaDiagnostics.writeLog(msg, type);
    }

    /**
     * Updates top header chronometers clocks time and dates labels.
     */
    updateTimeAndDate() {
        const now = new Date();
        const hh = String(now.getHours()).padStart(2, '0');
        const mm = String(now.getMinutes()).padStart(2, '0');
        const ss = String(now.getSeconds()).padStart(2, '0');
        
        document.getElementById('system-time').textContent = `${hh}:${mm}:${ss}`;
        
        const yyyy = now.getFullYear();
        const mMonth = String(now.getMonth() + 1).padStart(2, '0');
        const dd = String(now.getDate()).padStart(2, '0');
        
        document.getElementById('system-date').textContent = `${yyyy}-${mMonth}-${dd}`;
    }

    /**
     * Queries the hardware for video camera input devices, populating options lists.
     */
    async loadCameraDevices() {
        await window.FrejaCamera.loadCameraDevices();
    }

    /**
     * Binds camera video media streams to HUD visual feed boxes.
     */
    async startCameraStream(deviceId) {
        await window.FrejaCamera.startCameraStream(deviceId);
    }

    /**
     * Stops the camera webcam streams and clears hardware binding feeds.
     */
    stopCameraStream() {
        window.FrejaCamera.stopCameraStream();
    }

    /**
     * Triggers dynamic diagnostic values simulation metrics fluctuations inside HUD cards.
     */
    startDiagnosticSimulation() {
        window.FrejaDiagnostics.startDiagnosticSimulation();
    }

    /**
     * Periodically reports client HUD activity to the backend server.
     */
    async startHeartbeatLoop() {
        let clientHostname = "Unknown";
        if (window.location.port === "5000") {
            try {
                const localUrl = window.location.protocol + "//" + window.location.hostname + ":" + window.location.port + "/local-hostname";
                const res = await fetch(localUrl);
                if (res.ok) {
                    const data = await res.json();
                    clientHostname = data.hostname || "Unknown";
                }
            } catch (err) {
                console.warn("Could not retrieve local client hostname:", err);
            }
        }

        const sendHeartbeat = async () => {
            try {
                const token = localStorage.getItem("freja_access_token") || "";
                if (!token) return;
                const headers = { "Content-Type": "application/json" };
                headers["X-Freja-Token"] = token;
                
                await fetch("/api/client/heartbeat", {
                    method: "POST",
                    headers: headers,
                    body: JSON.stringify({ hostname: clientHostname })
                });
            } catch (e) {
                // Fail silently
            }
        };
        // Initial trigger
        sendHeartbeat();
        // Send heartbeat every 15 seconds
        setInterval(sendHeartbeat, 15000);
    }
}

// Instantiates the UI controller once the DOM elements have loaded successfully
window.addEventListener('DOMContentLoaded', () => {
    window.uiController = new FrejaUIController();
});
