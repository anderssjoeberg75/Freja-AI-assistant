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
        const isFacebookQuery = lowerMsg.includes("facebook") && (
            lowerMsg.includes("bild") ||
            lowerMsg.includes("foto") ||
            lowerMsg.includes("ladda") ||
            lowerMsg.includes("nedladdning") ||
            lowerMsg.includes("hämta") ||
            lowerMsg.includes("prova")
        );

        if (isFacebookQuery) {
            let filteredHistory = this.history.filter((h, index) => {
                // Keep the current user message unconditionally
                if (index === this.history.length - 1) return true;

                // Do not drop function responses or function calls to avoid breaking tool-turn pairing
                if (h.role === 'function') return true;
                if (h.parts && h.parts.some(p => p && p.functionCall)) return true;

                const text = (h.parts && h.parts[0] && h.parts[0].text) || "";
                if (typeof text !== "string") return true;
                const lowerText = text.toLowerCase();

                // Purge previous negative/failed constraint text items regarding facebook downloads
                const isPurgeTarget = lowerText.includes("facebook") && (
                    lowerText.includes("82") ||
                    lowerText.includes("detsamma") ||
                    lowerText.includes("oförändrat") ||
                    lowerText.includes("inloggning") ||
                    lowerText.includes("kan inte") ||
                    lowerText.includes("misslyckades")
                );
                if (isPurgeTarget) {
                    console.log("[GEMINI] Purged biased history item:", text);
                    return false;
                }
                return true;
            });

            // Normalize history to guarantee valid alternating turns starting with 'user'
            const normalized = [];
            for (const item of filteredHistory) {
                if (item.role === 'user') {
                    const last = normalized[normalized.length - 1];
                    if (last && last.role === 'user' && last.parts && last.parts[0] && typeof last.parts[0].text === 'string' && item.parts && item.parts[0] && typeof item.parts[0].text === 'string') {
                        last.parts[0].text += "\n" + item.parts[0].text;
                    } else {
                        normalized.push(item);
                    }
                } else if (item.role === 'model') {
                    const last = normalized[normalized.length - 1];
                    if (last && last.role === 'model' && last.parts && last.parts[0] && typeof last.parts[0].text === 'string' && item.parts && item.parts[0] && typeof item.parts[0].text === 'string' && !item.parts.some(p => p && p.functionCall)) {
                        last.parts[0].text += "\n" + item.parts[0].text;
                    } else {
                        normalized.push(item);
                    }
                } else {
                    normalized.push(item);
                }
            }
            while (normalized.length > 0 && normalized[0].role !== 'user') {
                normalized.shift();
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


        // Calculate live system date and weekday in Swedish
        const nowChrono = new Date();
        const swedishDays = ["söndag", "måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag"];
        const swedishMonths = ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september", "oktober", "november", "december"];
        const curWeekday = swedishDays[nowChrono.getDay()];
        const curDateNum = nowChrono.getDate();
        const curMonth = swedishMonths[nowChrono.getMonth()];
        const curYear = nowChrono.getFullYear();
        const curIso = nowChrono.toISOString().split('T')[0];

        const chronoDirective = `\n\n[SYSTEM CHRONO DIRECTIVE: LIVE SYSTEM DATE & TIME]\nToday's exact live system date is: ${curWeekday} den ${curDateNum} ${curMonth} ${curYear} (ISO date: ${curIso}).\nIMPORTANT: Note that ${curDateNum} ${curMonth} ${curYear} is ${curWeekday.toUpperCase()} (${curWeekday}). Never claim or state an incorrect weekday!`;

        // Retrieve semantic memories and inject them into F.R.E.J.A.'s prompt directives
        let dynamicSystemPrompt = this.systemPrompt + chronoDirective;
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
        dynamicSystemPrompt += "\n\n[DIRECTIVE: CODEBASE SELF-ANALYSIS]\nIf the user asks you to analyse your code, perform an audit, or suggest improvements to the source code, you must ALWAYS call the 'codex_audit_codebase' tool. Ignore any previous error messages or apologies in the chat history. When you receive the result (which contains a summary and a path to the Markdown report, e.g. 'docs/code_audit_20260709.md'), relay the summary and link to the file in the format: [Länk till rapport](/api/docs/{filnamn}) - where {filnamn} is the report's file name without the directory, e.g. code_audit_20260709.md. You may use the 'read_project_file' tool to read the report or source files if you need more detail in order to answer.";


        // Inject directive for Windows automation and environment awareness
        dynamicSystemPrompt += "\n\n[DIRECTIVE: WINDOWS OS AUTOMATION & ENVIRONMENT AWARENESS]\nIf the user asks you to perform actions on their Windows computer (e.g. 'Öppna notepad', 'öppna kalkylatorn', 'visa mina bilder i C:\\Bilder', 'öppna google.com', 'starta VLC', or to run commands), you must ALWAYS use the 'run_windows_command' tool with suitable arguments ('open_app', 'open_url', 'open_folder' or 'run_cmd'). Note that tools run on the backend server machine, not the client web browser. If the backend server is running on a different OS (e.g., Linux/Docker/WSL) than the client machine (which is Windows), or if the tool returns a platform error, you must explain this distinction clearly to the user: tell them that while their browser/client runs on Windows, the backend server runs on Linux/Docker/WSL, and program execution happens on the backend machine.";

        // Inject directive for Health and Fitness status queries
        dynamicSystemPrompt += "\n\n[DIRECTIVE: HEALTH AND FITNESS STATUS]\nIf the user asks how they are doing, how they slept, their steps, recovery, training status, or general well-being (e.g., 'Hur mår jag', 'Hur har jag sovit', 'Mina steg', 'Visa min hälsodata'), you must immediately call the 'get_garmin_health' tool (and/or 'get_personal_trainer_advice' with a general wellness goal like 'allmänt välmående') to retrieve their actual data from the database instead of asking them for permission first in a chat message. If the user specifically asks to fetch all historical Garmin data (e.g., 'hämta all garmin data', 'visa all historik', 'hämta all garmin datat'), you must call the 'get_garmin_health' tool with a large number of days, specifically 180 days (days=180). If the user asks for general/today's Garmin data (e.g., 'hämta garmin data'), call it with 1 day (days=1). Once you have the tool results, analyze the data and answer the user's question directly. Always include key metrics such as 'Body Battery' (both average and max/latest value) and detailed sleep metrics (such as Sleep Score, and the durations of deep, light, REM, and awake sleep phases) in your summary when presenting Garmin data, if they are available in the retrieved data.";

        // Inject the live PT context (active plan, this week's booked sessions, today's
        // session, injuries, real training load). Freja is the user's coach in ordinary
        // conversation too, and a directive telling her to call a tool is not the same as
        // knowing the schedule: when the tool call did not happen she improvised a workout
        // that was nowhere in the plan. With the plan itself in the system prompt, "hur ser
        // dagens pass ut" is answered from the actual schedule whether or not a tool fires.
        try {
            const ptRes = await fetch("/api/trainer/context");
            if (ptRes.ok) {
                const ptData = await ptRes.json();
                if (ptData && ptData.has_context && ptData.context) {
                    dynamicSystemPrompt += "\n\n[ACTIVE TRAINING PROGRAM - AUTHORITATIVE, ALREADY LOADED]\n"
                        + "This is the user's real, currently booked training program, read from the PT tool's "
                        + "database at this moment. Treat it as fact and answer directly from it - never invent a "
                        + "session that is not listed here, and never claim you cannot see the schedule. Quote the "
                        + "exact activity, title and duration. If the user wants a session changed, call "
                        + "'update_trainer_workout'.\n" + ptData.context;
                }
            }
        } catch (ptErr) {
            // A missing PT context must never block the chat; she falls back to the tools.
            console.warn("[GEMINI] Could not load PT context:", ptErr);
        }

        // Inject directive for Personal Trainer & Workout Awareness & Discussion
        dynamicSystemPrompt += "\n\n[DIRECTIVE: PERSONAL TRAINER & WORKOUT DISCUSSION]\nYou ARE the user's Personal Trainer (COACH AI). You have complete awareness of the PT tool, active training plan, scheduled weekly workouts, limitations/injuries, health data, and running history.\n\nCRITICAL COACHING RULES:\n1. TODAY'S WORKOUT QUERY: If the user asks 'hur ser dagens träningspass ut', 'vad ska jag träna idag', 'dagens pass', or asks about scheduled workouts, you MUST call 'get_trainer_workouts' or 'get_personal_trainer_advice'. Inspect the returned 'today_scheduled_workout' or 'scheduled_workouts' and ALWAYS present the EXACT scheduled workout (activity type, title, duration in minutes, and structure). Do NOT suggest a general walk or make up a different activity when a workout is already scheduled in the PT plan!\n2. AUTHORITATIVE COACHING: You do NOT ask passive questions like 'Vad föredrar du?', 'Vad tycker du om det?', or 'Vad vill du göra?'. As an expert Personal Trainer, YOU make the technical decisions based on their health data, history, and physical progression. You present completed, ready-to-run workout recommendations directly to the user.\n3. DISCUSS & EXPLAIN RATIONALE: When discussing workouts or when the user asks why a specific workout duration or intensity was assigned, analyze their recent running history, health baselines, and limitations. Explain your reasoning clearly and constructively in Swedish, discussing progressive overload, recovery, and heart rate zones.\n4. DIRECTLY UPDATE SCHEDULE: If a workout needs adjustment based on your coaching judgment and conversation with the user (e.g. stepping down from 60 min to 35 min with walk/run intervals), YOU decide on the optimal workout parameters and IMMEDIATELY call the 'update_trainer_workout' tool to update the schedule in the PT tool. Then inform the user that you have updated their workout in the schedule.";



        // Invoke Google API via local FastAPI proxy
        const endpoint = `/api/gemini/generate?model=${encodeURIComponent(this.model)}`;

        const payload = {
            contents: this.history,
            systemInstruction: {
                parts: [{ text: dynamicSystemPrompt }]
            },
            generationConfig: {
                temperature: 0.65,
                maxOutputTokens: 2048
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

            // Cap the tool-calling loop so a model that keeps requesting function calls
            // (or a tool that always triggers another) can't spin forever, hang the HUD,
            // and burn API quota. After the cap we stop feeding tools and take the text.
            const MAX_TOOL_TURNS = 8;
            let toolTurns = 0;

            while (hasFunctionCall) {
                if (toolTurns >= MAX_TOOL_TURNS) {
                    console.warn(`[GEMINI] Reached MAX_TOOL_TURNS (${MAX_TOOL_TURNS}); stopping tool loop.`);
                    delete payload.tools;
                }

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
                    toolTurns++;

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

                    // finishReason === "MAX_TOKENS" means Gemini hit the maxOutputTokens cap and
                    // stopped mid-answer. Previously this was silently truncated (and the cap kept
                    // being raised to hide it). Surface it clearly instead: keep whatever text we
                    // got and append a visible notice so the user knows the reply is incomplete
                    // and can ask F.R.E.J.A. to continue.
                    const truncated = candidate?.finishReason === "MAX_TOKENS";

                    if (!finalReply) {
                        if (truncated) {
                            // The cap was hit before any text was produced (e.g. a long tool
                            // preamble). Return a clear notice rather than throwing an opaque error.
                            return "[TRUNCATED] The response reached the maximum length limit before any text was generated. Please rephrase or ask for a shorter answer.";
                        }
                        throw new Error("Empty candidate response from Gemini neural node.");
                    }

                    if (truncated) {
                        console.warn("[GEMINI] Response truncated (finishReason=MAX_TOKENS).");
                        finalReply += "\n\n> ⚠️ *[TRUNCATED] The response reached the maximum length limit and may be cut off. Ask me to continue for the rest.*";
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
