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
        this.systemPrompt = "Du är FREJA, en intelligent och artig AI-assistent. Svara kortfattat.";
        this.loadApiKey();
    }

    /**
     * Grabs a raw JPEG frame from the active webcam video element, compressing it to base64.
     */
    captureWebcamSnapshot() {
        const video = document.getElementById('webcam-video');
        if (!video || !video.classList.contains('active')) return null;
        
        try {
            // Draw visual frame to hidden canvas
            const canvas = document.createElement('canvas');
            canvas.width = 400; // Optimal small width to protect token payload limits
            canvas.height = 300;
            const ctx = canvas.getContext('2d');
            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
            
            // Encode as compressed JPEG (70% quality)
            const dataUrl = canvas.toDataURL('image/jpeg', 0.7);
            return dataUrl.split(',')[1];
        } catch (e) {
            console.error("[GEMINI] Failed to capture webcam snapshot:", e);
            return null;
        }
    }

    /**
     * Loads the Gemini API credential key from LocalStorage.
     */
    loadApiKey() {
        const stored = localStorage.getItem("freja_gemini_apikey");
        if (stored) {
            this.apiKey = stored;
            document.getElementById('input-api-key').value = stored;
            document.getElementById('cap-gemini').classList.add('active');
            console.log("[GEMINI] Loaded API key from LocalStorage");
        } else {
            console.warn("[GEMINI] No API key set. Running in Offline Mock mode.");
            document.getElementById('cap-gemini').classList.remove('active');
        }
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

        // Run mock offline generator if API key is empty
        if (!this.apiKey) {
            return this.generateMockOfflineResponse(userMessage);
        }

        // Retrieve semantic memories and inject them into F.R.E.J.A.'s prompt directives
        let dynamicSystemPrompt = this.systemPrompt;
        if (window.uiController && window.uiController.memory && window.uiController.memory.enabled) {
            try {
                const memories = await window.uiController.memory.searchMemory(userMessage);
                if (memories && memories.length > 0) {
                    const facts = memories.map(m => `- ${m.memory}`).join("\n");
                    dynamicSystemPrompt = `${this.systemPrompt}\n\n[NEURAL MEMORY CHIPS RECOVERED]\nFreja minns följande om användaren:\n${facts}`;
                    console.log("[GEMINI] Dynamic System Prompt Injected with memories:\n", dynamicSystemPrompt);
                }
            } catch (memErr) {
                console.warn("[GEMINI] Could not retrieve memories for prompt injection:", memErr);
            }
        }

        // Inject directive to explicitly cite Google when using the web search tool
        dynamicSystemPrompt += "\n\n[DIRECTIVE: WEB SEARCH CITATION]\nNär du använder verktyget 'google_search' för att söka efter information eller fakta på webben, MÅSTE du alltid uttryckligen ange i ditt svar att källan är Google (t.ex. genom att skriva 'Källa: Google' eller 'Enligt sökresultat på Google' i slutet av din förklaring).";

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

        // Attach registered tools to the payload
        const toolsPayload = [];
        if (window.FrejaTools) {
            const declarations = Object.values(window.FrejaTools).map(t => t.declaration);
            if (declarations.length > 0) {
                toolsPayload.push({ functionDeclarations: declarations });
            }
        }
        if (toolsPayload.length > 0) {
            payload.tools = toolsPayload;
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
            
            return `[ANOMALY] Neural Uplink Failed. Det gick inte att kontakta Gemini. Fel: ${e.message}. Kontrollera din internetanslutning eller din API-nyckel i inställningarna.`;
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
                        ? "God dag, sir. Jag är för närvarande offline eftersom ingen Gemini API-nyckel är konfigurerad. Mitt röstgränssnitt är fullt aktivt, men min kognitiva länk kräver en API-nyckel."
                        : "Greetings, sir. I am currently operating offline as no Gemini API key is configured. My vocal systems are online, but my cognitive processor requires an API link.";
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
                        ? `Klockan är exakt ${timeStr}, sir. Alla kronometrar är synkroniserade.`
                        : `Chronometers indicate precisely ${timeStr}, sir. All units are fully aligned.`;
                } else {
                    reply = sv
                        ? `Jag hörde: "${msg}". Utan en Gemini-länk är min kapacitet begränsad till offline-direktiv, sir. Klicka på kugghjulsikonen uppe till höger för att mata in din API-nyckel.`
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
