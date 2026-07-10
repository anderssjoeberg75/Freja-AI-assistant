/**
 * F.R.E.J.A. - Google Gemini API Client Node
 * 
 * Communicates with the Google Gemini API (gemini-2.5-flash) to obtain conversational
 * replies. Dynamically binds long-term memory chips into prompt systems, encodes webcam
 * frame snapshots to JPEGs for real-time Synoptic Scanner feeds, and handles mock offline
 * diagnostic responses if no active API key is configured.
 */

class GeminiClient {
    constructor() {
        this.apiKey = "";
        this.model = "gemini-2.5-flash";
        this.history = [];
        // Overridden by the persona textarea in Settings; see client/index.html #textarea-persona.
        this.systemPrompt = "You are FREJA, an intelligent and polite AI assistant. Answer concisely, and always answer in Swedish.";
        this.lastFrameBuffer = null;
        this.lastWebcamCaptureTime = 0;
        this.loadApiKey();
    }

    /**
     * Grabs a raw JPEG frame from the active webcam video element, compressing it to base64.
     */
    captureWebcamSnapshot() {
        const video = document.getElementById('webcam-video');
        if (!video || !video.classList.contains('active')) return null;

        const now = Date.now();
        if (this.lastWebcamCaptureTime && (now - this.lastWebcamCaptureTime < 8000)) {
            console.log("[GEMINI] Throttling webcam capture to protect tokens.");
            return null;
        }

        try {
            // Check pixel difference using a downscaled 40x30 canvas
            const downscaledCanvas = document.createElement('canvas');
            downscaledCanvas.width = 40;
            downscaledCanvas.height = 30;
            const dsCtx = downscaledCanvas.getContext('2d');
            dsCtx.drawImage(video, 0, 0, 40, 30);
            const imgData = dsCtx.getImageData(0, 0, 40, 30);
            const data = imgData.data;

            let isStatic = false;
            if (this.lastFrameBuffer) {
                let changedPixels = 0;
                const totalPixels = 40 * 30;
                const threshold = 15; // intensity difference threshold (0-255)
                for (let i = 0; i < data.length; i += 4) {
                    const rDiff = Math.abs(data[i] - this.lastFrameBuffer[i]);
                    const gDiff = Math.abs(data[i + 1] - this.lastFrameBuffer[i + 1]);
                    const bDiff = Math.abs(data[i + 2] - this.lastFrameBuffer[i + 2]);
                    if (rDiff > threshold || gDiff > threshold || bDiff > threshold) {
                        changedPixels++;
                    }
                }
                const diffRatio = changedPixels / totalPixels;
                console.log(`[GEMINI WEB snapshot] Frame change ratio: ${(diffRatio * 100).toFixed(2)}%`);
                if (diffRatio < 0.015) { // less than 1.5% of pixels changed
                    isStatic = true;
                }
            }

            this.lastFrameBuffer = data;

            if (isStatic) {
                console.log("[GEMINI] Webcam stream is static. Bypassing image payload to prevent token bloat.");
                return null;
            }

            // Draw visual frame to hidden canvas
            const canvas = document.createElement('canvas');
            canvas.width = 400; // Optimal small width to protect token payload limits
            canvas.height = 300;
            const ctx = canvas.getContext('2d');
            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

            // Encode as compressed JPEG (70% quality)
            const dataUrl = canvas.toDataURL('image/jpeg', 0.7);

            // Update last capture time since we succeeded
            this.lastWebcamCaptureTime = now;

            return dataUrl.split(',')[1];
        } catch (e) {
            console.error("[GEMINI] Failed to capture webcam snapshot:", e);
            return null;
        }
    }

    /**
     * Loads the Gemini API credential key from LocalStorage.
     */
    async loadApiKey() {
        const stored = localStorage.getItem("freja_gemini_apikey");
        const inputApiKeyEl = document.getElementById('input-api-key');
        const capGeminiEl = document.getElementById('cap-gemini');

        if (stored && stored.trim() !== "") {
            this.apiKey = stored;
            if (inputApiKeyEl) inputApiKeyEl.value = stored;
            if (capGeminiEl) capGeminiEl.classList.add('active');
            console.log("[GEMINI] Loaded API key from LocalStorage");
            return;
        }

        // Check backend server keys database via API
        try {
            const res = await fetch("/api/keys");
            if (res.ok) {
                const keys = await res.json();
                const serverKey = keys.freja_gemini_apikey || keys.gemini_api_key;
                if (serverKey && serverKey.trim() !== "") {
                    this.apiKey = "configured";
                    if (inputApiKeyEl) inputApiKeyEl.value = "•••••••• (Configured on the backend)";
                    if (capGeminiEl) capGeminiEl.classList.add('active');
                    console.log("[GEMINI] Loaded active Gemini API key from backend database.");
                    return;
                }
            } else if (res.status === 401) {
                console.warn("[GEMINI] Backend auth 401: Access token invalid or missing.");
            }
        } catch (e) {
            console.warn("[GEMINI] Backend key check failed:", e);
        }

        console.warn("[GEMINI] No API key set. Running in Offline Mock mode.");
        this.apiKey = null;
        if (capGeminiEl) capGeminiEl.classList.remove('active');
    }

    /**
     * Saves the new API key to storage and lights up the grid capability indicator badge.
     */
    setApiKey(key) {
        this.apiKey = key;
        if (key) {
            localStorage.setItem("freja_gemini_apikey", key);
            document.getElementById('cap-gemini').classList.add('active');
        } else {
            localStorage.removeItem("freja_gemini_apikey");
            document.getElementById('cap-gemini').classList.remove('active');
        }
    }

    /**
     * Wipes the active conversation context history memory.
     */
    clearHistory() {
        this.history = [];
        console.log("[GEMINI] Neural context cleared.");
    }

    /**
     * Sends the prompt along with the latest webcam snapshots (if optics is active) to Gemini.
     */
    async generateResponse(userMessage, attachImage = false) {
        console.log("[GEMINI] Requesting response for message:", userMessage);

        // Capture snapshot if requested and scanner is active
        const webcamBase64 = attachImage ? this.captureWebcamSnapshot() : null;

        // Clean older multimodal images from context to avoid token bloat and API quota exhaustion
        this.history.forEach(h => {
            if (h.role === 'user' && Array.isArray(h.parts)) {
                h.parts = h.parts.filter(p => !p.inlineData);
            }
        });

        // Assemble the user payload parts
        const userParts = [{ text: userMessage }];
        if (webcamBase64) {
            userParts.push({
                inlineData: {
                    mimeType: "image/jpeg",
                    data: webcamBase64
                }
            });
            console.log("[GEMINI] Attached webcam optics frame to payload");
        }

        // Trim history to keep only the latest 15 messages, starting cleanly with a user message
        if (this.history.length > 15) {
            this.history = this.history.slice(-15);
            while (this.history.length > 0 && this.history[0].role !== 'user') {
                this.history.shift();
            }
        }

        // Cache user input in history
        this.history.push({
            role: "user",
            parts: userParts
        });

        // Clean history of negative constraints when requesting a Facebook download.
        // These keyword lists match what the *user* types, and the user speaks Swedish to Freja,
        // so the keywords are Swedish. They are input data, not UI copy.
        const lowerMsg = userMessage.toLowerCase();
        const isFacebookQuery = lowerMsg.includes("facebook") ||
            lowerMsg.includes("bilder") ||
            lowerMsg.includes("foton") ||
            lowerMsg.includes("nedladdning") ||
            lowerMsg.includes("prova") ||
            lowerMsg.includes("samma") ||
            lowerMsg.includes("hämta");

        if (isFacebookQuery) {
            let filteredHistory = this.history.filter((h, index) => {
                // Keep the current user message unconditionally
                if (index === this.history.length - 1) return true;

                const text = (h.parts && h.parts[0] && h.parts[0].text) || "";
                const lowerText = text.toLowerCase();
                // If it relates to facebook, photos, downloads, or limits, purge it!
                const isPurgeTarget = lowerText.includes("facebook") ||
                    lowerText.includes("bild") ||
                    lowerText.includes("foto") ||
                    lowerText.includes("nedladdning") ||
                    lowerText.includes("82") ||
                    lowerText.includes("detsamma") ||
                    lowerText.includes("oförändrat") ||
                    lowerText.includes("inloggning");
                if (isPurgeTarget) {
                    console.log("[GEMINI] Purged biased history item:", text);
                    return false;
                }
                return true;
            });

            // Normalize history to guarantee alternating roles starting with 'user'
            const normalized = [];
            let lastRole = null;
            for (const item of filteredHistory) {
                if (item.role === 'user') {
                    if (lastRole === 'user') {
                        normalized[normalized.length - 1].parts[0].text += "\n" + item.parts[0].text;
                    } else {
                        normalized.push(item);
                        lastRole = 'user';
                    }
                } else if (item.role === 'model') {
                    if (lastRole === 'model') {
                        normalized[normalized.length - 1].parts[0].text += "\n" + item.parts[0].text;
                    } else if (lastRole === 'user') {
                        normalized.push(item);
                        lastRole = 'model';
                    }
                }
            }
            this.history = normalized;
        }

        // Run mock offline generator if API key is empty
        if (!this.apiKey) {
            await this.loadApiKey();
            if (!this.apiKey) {
                return this.generateMockOfflineResponse(userMessage);
            }
        }


        // Retrieve semantic memories and inject them into F.R.E.J.A.'s prompt directives
        let dynamicSystemPrompt = this.systemPrompt;
        if (window.uiController && window.uiController.memory && window.uiController.memory.enabled) {
            try {
                const memories = await window.uiController.memory.searchMemory(userMessage);
                if (memories && memories.length > 0) {
                    const facts = memories.map(m => `- ${m.memory}`).join("\n");
                    dynamicSystemPrompt = `${this.systemPrompt}\n\n[NEURAL MEMORY CHIPS RECOVERED]\nFreja remembers the following about the user:\n${facts}`;
                    console.log("[GEMINI] Dynamic System Prompt Injected with memories:\n", dynamicSystemPrompt);
                }
            } catch (memErr) {
                console.warn("[GEMINI] Could not retrieve memories for prompt injection:", memErr);
            }
        }

        // The directives below are written in English, but Freja still answers the user in
        // Swedish - that is set by the persona system prompt. Where a directive quotes text Freja
        // must literally emit (the "Källa: Google" citation) or literal user phrasing it should
        // recognise, that Swedish is kept verbatim on purpose.

        // Inject directive to explicitly cite Google when using the web search tool
        dynamicSystemPrompt += "\n\n[DIRECTIVE: WEB SEARCH CITATION]\nWhen you use the 'google_search' tool to look up information or facts on the web, you MUST always state explicitly in your answer that the source is Google (for example by writing 'Källa: Google' or 'Enligt sökresultat på Google' at the end of your explanation).";

        // Inject directive to force calling download_facebook_photos and ignore past negative history constraints
        dynamicSystemPrompt += "\n\n[DIRECTIVE: FACEBOOK PHOTO DOWNLOAD]\nIf the user asks you to download, search for, retry or fetch photos from Facebook (e.g. with commands such as 'Prova igen' or 'ladda ner'), you must ALWAYS call the 'download_facebook_photos' tool to run the process again. Ignore any earlier messages in the chat history claiming that you cannot log in, or that there are no more than 82 photos. You now have full access to the login and can fetch all the photos by running the tool.";

        // Inject directive to trigger system updates remotely
        dynamicSystemPrompt += "\n\n[DIRECTIVE: SYSTEM UPDATE]\nIf the user asks you to update yourself, download updates or fetch new code from GitHub (e.g. 'uppdatera dig', 'uppdatera från GitHub'), you must ALWAYS call the 'system_update' tool. Tell the user that you are starting the update and restarting the server.";

        // Inject directive for codebase self-analysis
        dynamicSystemPrompt += "\n\n[DIRECTIVE: CODEBASE SELF-ANALYSIS]\nIf the user asks you to analyse your code, perform an audit, or suggest improvements to the source code, call the 'codex_audit_codebase' tool. When you receive the result (which contains a summary and a path to the Markdown report, e.g. 'docs/code_audit_20260709.md'), relay the summary and link to the file in the format: [Länk till rapport](/api/docs/{filnamn}) - where {filnamn} is the report's file name without the directory, e.g. code_audit_20260709.md. You may use the 'read_project_file' tool to read the report or source files if you need more detail in order to answer.";

        // Inject directive for Windows automation
        dynamicSystemPrompt += "\n\n[DIRECTIVE: WINDOWS OS AUTOMATION]\nIf the user asks you to perform actions on their Windows computer (e.g. 'Öppna notepad', 'öppna kalkylatorn', 'visa mina bilder i C:\\Bilder', 'öppna google.com', or to run commands), you must ALWAYS use the 'run_windows_command' tool with suitable arguments ('open_app', 'open_url', 'open_folder' or 'run_cmd').";



        // Invoke Google API via local FastAPI proxy
        const endpoint = `/api/gemini/generate?model=${encodeURIComponent(this.model)}`;

        const payload = {
            contents: this.history,
            systemInstruction: {
                parts: [{ text: dynamicSystemPrompt }]
            },
            generationConfig: {
                temperature: 0.65,
                maxOutputTokens: 800
            }
        };

        // Attach registered tools to the payload from the backend
        try {
            const toolsResponse = await fetch("/api/tools/declarations");
            if (toolsResponse.ok) {
                const declarations = await toolsResponse.json();
                if (declarations && declarations.length > 0) {
                    payload.tools = [{ functionDeclarations: declarations }];
                }
            }
        } catch (toolErr) {
            console.warn("[GEMINI] Failed to fetch backend tool declarations:", toolErr);
        }

        try {
            let hasFunctionCall = true;
            let finalReply = "";

            while (hasFunctionCall) {
                const response = await fetch(endpoint, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify(payload)
                });

                if (!response.ok) {
                    const errData = await response.json();
                    throw new Error(errData.detail || errData.error?.message || `HTTP ${response.status}`);
                }

                const data = await response.json();
                const candidate = data.candidates?.[0];
                const parts = candidate?.content?.parts || [];
                const functionCallPart = parts.find(p => p.functionCall);

                if (functionCallPart && functionCallPart.functionCall) {
                    const call = functionCallPart.functionCall;
                    console.log("[GEMINI] Function Call Requested:", call);

                    // 1. Append model functionCall message to history
                    this.history.push(candidate.content);

                    // 2. Execute local tool function (checks permission or prompts user)
                    const result = await window.uiController.handleToolCall(call);

                    // 3. Append functionResponse message to history
                    this.history.push({
                        role: "function",
                        parts: [{
                            functionResponse: {
                                name: call.name,
                                response: result
                            }
                        }]
                    });

                    // Update content in payload for next iteration
                    payload.contents = this.history;
                } else {
                    hasFunctionCall = false;
                    const textPart = parts.find(p => p.text);
                    finalReply = textPart?.text;
                    if (!finalReply) {
                        throw new Error("Empty candidate response from Gemini neural node.");
                    }

                    // Append final text model response to history
                    this.history.push(candidate.content);
                }
            }

            return finalReply;
        } catch (e) {
            console.error("[GEMINI] API Request failure:", e);
            soundSynth.playError();

            // Clean up the failed context states by removing history back to the user prompt
            while (this.history.length > 0) {
                const last = this.history[this.history.length - 1];
                this.history.pop();
                if (last.role === 'user') {
                    break;
                }
            }

            return `[ANOMALY] Neural Uplink Failed. Could not reach Gemini. Error: ${e.message}. Check your internet connection, or your API key in the settings.`;
        }
    }

    /**
     * Standard local diagnostic responder used during offline mode simulation.
     */
    generateMockOfflineResponse(msg) {
        soundSynth.playClick();
        return new Promise((resolve) => {
            setTimeout(() => {
                const cleanMsg = msg.toLowerCase().trim();
                let reply = "";

                // Prevent history size growth in offline demo mode
                if (this.history.length > 8) {
                    this.history.shift();
                    this.history.shift();
                }

                const sv = document.getElementById('select-lang-quick').value === 'sv-SE';

                if (cleanMsg.includes('hej') || cleanMsg.includes('hello') || cleanMsg.includes('tjena')) {
                    reply = sv
                        ? "God dag Jag är för närvarande offline eftersom ingen Gemini API-nyckel är konfigurerad. Mitt röstgränssnitt är fullt aktivt, men min kognitiva länk kräver en API-nyckel."
                        : "Greetings. I am currently operating offline as no Gemini API key is configured. My vocal systems are online, but my cognitive processor requires an API link.";
                } else if (cleanMsg.includes('vem är du') || cleanMsg.includes('who are you') || cleanMsg.includes('namn')) {
                    reply = sv
                        ? "Jag är F.R.E.J.A. (Fully Responsive Electronic Judicial Assistant), din kognitiva nätverksassistent."
                        : "I am F.R.E.J.A., standing for: Fully Responsive Electronic Judicial Assistant, your cognitive assistant.";
                } else if (cleanMsg.includes('väder') || cleanMsg.includes('weather')) {
                    reply = sv
                        ? "Mina sensorsystem indikerar stabila atmosfäriska förhållanden i din lokala sektor. Ute: 19°C med svaga vindar."
                        : "Sensor nodes report stable atmospheric configurations in your local sector. Temp: 19°C, clear skies.";
                } else if (cleanMsg.includes('tid') || cleanMsg.includes('time') || cleanMsg.includes('klockan')) {
                    const now = new Date();
                    const timeStr = now.toLocaleTimeString(sv ? 'sv-SE' : 'en-US');
                    reply = sv
                        ? `Klockan är exakt ${timeStr}.Alla kronometrar är synkroniserade.`
                        : `Chronometers indicate precisely ${timeStr},All units are fully aligned.`;
                } else {
                    reply = sv
                        ? `Jag hörde: "${msg}". Utan en Gemini-länk är min kapacitet begränsad till offline-direktiv. Klicka på kugghjulsikonen uppe till höger för att mata in din API-nyckel.`
                        : `Captured input: "${msg}". Operating in offline diagnostic mode. To enable full cognitive processing, please insert your Gemini API Key in the settings gear.`;
                }

                // Add to history
                this.history.push({
                    role: "model",
                    parts: [{ text: reply }]
                });

                resolve(reply);
            }, 600);
        });
    }
}
