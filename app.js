/**
 * F.R.E.J.A. - Central Orchestrator & UI Controller
 * 
 * Binds all visual HUD controls, panels toggling, sound chimes, text/voice queries,
 * webcam optical scans, long-term memory vault interfaces, and accent color themes together.
 * Extends the five modular nodes: sound.js, visualizer.js, speech.js, memory.js, and gemini.js.
 */

class FrejaUIController {
    constructor() {
        // Instantiate the core cognitive sub-modules
        this.gemini = new GeminiClient();
        this.memory = new FrejaMemoryEngine(this.gemini);
        this.speech = new FrejaSpeechEngine();
        
        this.initializeUI();
        this.bindEvents();
        this.startDiagnosticSimulation();
        this.updateTimeAndDate();
        
        // Keep systems kronometer clocks in sync
        setInterval(() => this.updateTimeAndDate(), 1000);
    }

    /**
     * Pulls previously cached configuration values from LocalStorage.
     */
    initializeUI() {
        // Load voice speech rates
        const rate = localStorage.getItem("freja_speech_rate") || "1.0";
        const pitch = localStorage.getItem("freja_speech_pitch") || "1.0";
        const persona = localStorage.getItem("freja_speech_persona") || document.getElementById('textarea-persona').value;
        const autospeak = localStorage.getItem("freja_autospeak") !== "false";
        const lang = localStorage.getItem("freja_lang") || "sv-SE";
        const theme = localStorage.getItem("freja_theme") || "cyan";

        document.getElementById('slider-rate').value = rate;
        document.getElementById('val-rate').textContent = rate;
        this.speech.rate = parseFloat(rate);

        document.getElementById('slider-pitch').value = pitch;
        document.getElementById('val-pitch').textContent = pitch;
        this.speech.pitch = parseFloat(pitch);

        document.getElementById('textarea-persona').value = persona;
        this.gemini.systemPrompt = persona;

        document.getElementById('chk-autospeak').checked = autospeak;
        this.speech.autoSpeak = autospeak;

        document.getElementById('select-lang-quick').value = lang;
        this.speech.setLanguage(lang);

        // Load ElevenLabs API keys & voice configs
        const elevenKey = localStorage.getItem("freja_eleven_apikey") || "e4984cf824dd4f39f489d3dd4ed6f22518700d4ad0f9a8077a7915a85b23b81d";
        const elevenVoice = localStorage.getItem("freja_eleven_voice") || "21m00Tcm4TlvDq8ikWAM";
        const elevenCustomVoice = localStorage.getItem("freja_eleven_custom_voice") || "";

        document.getElementById('input-eleven-key').value = elevenKey;
        this.speech.elevenApiKey = elevenKey;

        document.getElementById('select-eleven-voice').value = elevenVoice;
        this.speech.elevenVoice = elevenVoice;

        document.getElementById('input-eleven-custom-voice').value = elevenCustomVoice;
        this.speech.elevenCustomVoice = elevenCustomVoice;

        // Toggle custom ElevenLabs voice input group dynamically
        if (elevenVoice === 'custom') {
            document.getElementById('group-eleven-custom').style.display = 'block';
        } else {
            document.getElementById('group-eleven-custom').style.display = 'none';
        }

        // Load Mem0 credentials
        const mem0Key = localStorage.getItem("freja_mem0_apikey") || "";
        const mem0Enabled = localStorage.getItem("freja_mem0_enabled") !== "false";
        
        document.getElementById('input-mem0-key').value = mem0Key;
        document.getElementById('chk-use-mem0').checked = mem0Enabled;
        
        this.memory.apiKey = mem0Key;
        this.memory.enabled = mem0Enabled;
        this.memory.updateCapBadge();

        // Load camera settings
        const savedCam = localStorage.getItem("freja_camera_device_id") || "off";
        const autoOptics = localStorage.getItem("freja_auto_optics") !== "false";
        
        const chkAutoOptics = document.getElementById('chk-auto-optics');
        if (chkAutoOptics) {
            chkAutoOptics.checked = autoOptics;
        }
        this.savedCameraId = savedCam;

        // Load tool permissions
        const weatherAllowed = localStorage.getItem("freja_tool_get_weather_allowed") === "true";
        const chkWeather = document.getElementById('chk-tool-get_weather');
        if (chkWeather) {
            chkWeather.checked = weatherAllowed;
        }

        this.applyTheme(theme);
    }

    /**
     * Configures DOM button click bindings, forms triggers, and inputs listeners.
     */
    bindEvents() {
        const self = this;

        // Boot-Up Button & Holographic Overlay release
        const shield = document.getElementById('interaction-shield');
        const initAudioBtn = document.getElementById('btn-initialize-audio');
        
        const removeShield = () => {
            soundSynth.init();
            soundSynth.playStartupSweep();
            shield.classList.add('fade-out');
            
            self.writeLog("COGNITIVE SERVICES LINKING ACTIVE", "sys");
            self.writeLog("SPEECH RECOGNITION ONLINE [SV-SE]", "sys");
            
            // Populate camera selection inputs dropdown
            self.loadCameraDevices();
            
            // Initiate canvas visualizer animations
            window.visualizer = new ArcReactorVisualizer('arc-canvas');
            window.visualizer.setThemeHue(self.getCurrentThemeHue());
            window.visualizer.startAnimation();
            
            // Ask permission for microphone in background
            soundSynth.getMicrophoneStream().then(() => {
                self.writeLog("MICROPHONE CORE ACQUIRED. DYNAMIC EQUALIZER LINKED", "sys");
            }).catch(() => {
                self.writeLog("MICROPHONE DENIED. RUNNING SPEECH VIA MANUAL WRITING", "warn");
            });

            // Greet User post boot-up sequence
            setTimeout(() => {
                const sv = document.getElementById('select-lang-quick').value === 'sv-SE';
                const startMsg = sv 
                    ? "System aktiverat. Alla nätverksprotokoll online. Hur kan jag hjälpa dig idag, sir?"
                    : "Systems fully engaged. AI diagnostic matrix secure. How may I assist you today, sir?";
                
                self.appendChatMessage("assistant", startMsg);
                self.speech.speak(startMsg);
            }, 1000);
        };
        
        initAudioBtn.addEventListener('click', removeShield);
        
        // Voice Microphone Activation Toggle Button
        const btnMic = document.getElementById('btn-mic');
        btnMic.addEventListener('click', () => {
            soundSynth.playClick();
            if (self.speech.isListening) {
                self.speech.stopListening();
                btnMic.classList.remove('listening');
                document.getElementById('vocal-status').textContent = "STANDBY";
                document.getElementById('vocal-status').classList.remove('active');
                if (window.visualizer) window.visualizer.state = 'SLEEPING';
                document.getElementById('voice-hint').textContent = "Röststyrning avstängd";
                document.getElementById('voice-bars').classList.remove('active');
                self.writeLog("VOICE COGNITION DEACTIVATED", "warn");
            } else {
                soundSynth.getMicrophoneStream().then(() => {
                    self.speech.startListening();
                    btnMic.classList.add('listening');
                    document.getElementById('vocal-status').textContent = "LISTENING";
                    document.getElementById('vocal-status').classList.add('active');
                    if (window.visualizer) window.visualizer.state = 'LISTENING';
                    
                    const sv = self.speech.lang === 'sv-SE';
                    document.getElementById('voice-hint').textContent = sv ? "Jag lyssnar... Prata nu" : "Listening... Speak now";
                    document.getElementById('voice-bars').classList.add('active');
                    self.writeLog("VOICE INTERFACE COGNITION SECURED", "sys");
                }).catch(() => {
                    self.writeLog("CANNOT INITIALIZE VOICE ENGINE: NO MICROPHONE ACCESS", "err");
                    soundSynth.playError();
                });
            }
        });

        // Speech transcript callback trigger
        this.speech.transcriptCallback = (text) => {
            self.writeLog(`HEARD: "${text}"`, "user");
            self.appendChatMessage("user", text);
            self.processUserQuery(text);
        };

        // Text query send form actions
        const textQueryInput = document.getElementById('text-query');
        const btnSendText = document.getElementById('btn-send-text');

        const submitTextQuery = () => {
            const query = textQueryInput.value.trim();
            if (!query) return;
            
            textQueryInput.value = "";
            soundSynth.playClick();
            
            self.writeLog(`QUERY SUBMITTED: "${query}"`, "user");
            self.appendChatMessage("user", query);
            self.processUserQuery(query);
        };

        btnSendText.addEventListener('click', submitTextQuery);
        textQueryInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') submitTextQuery();
        });

        // Language select triggers
        const langQuick = document.getElementById('select-lang-quick');
        langQuick.addEventListener('change', () => {
            const selected = langQuick.value;
            self.speech.setLanguage(selected);
            localStorage.setItem("freja_lang", selected);
            self.writeLog(`V.O.C.A.L. SYSTEM LANGUAGE SWITCHED TO: ${selected}`, "sys");
            soundSynth.playClick();
        });

        // Auto-Speak checkbox triggers
        const chkAutospeak = document.getElementById('chk-autospeak');
        chkAutospeak.addEventListener('change', () => {
            self.speech.autoSpeak = chkAutospeak.checked;
            localStorage.setItem("freja_autospeak", chkAutospeak.checked);
            soundSynth.playClick();
        });

        // Webcam Camera input select options changes
        const selectCamera = document.getElementById('select-camera');
        if (selectCamera) {
            selectCamera.addEventListener('focus', () => {
                self.loadCameraDevices();
            });
            selectCamera.addEventListener('change', () => {
                const val = selectCamera.value;
                self.startCameraStream(val);
                localStorage.setItem("freja_camera_device_id", val);
            });
        }

        // Auto optics checkbox event listener
        const chkAutoOptics = document.getElementById('chk-auto-optics');
        if (chkAutoOptics) {
            chkAutoOptics.addEventListener('change', () => {
                localStorage.setItem("freja_auto_optics", chkAutoOptics.checked);
                soundSynth.playClick();
                self.writeLog(`OPTICS AUTO-STREAM: ${chkAutoOptics.checked ? "ON" : "OFF"}`, "sys");
            });
        }

        // Clear Chat button click trigger
        const btnClearChat = document.getElementById('btn-clear-chat');
        if (btnClearChat) {
            btnClearChat.addEventListener('click', () => {
                soundSynth.playError();
                if (confirm("Vill du rensa chatthistoriken? Detta tar bort meddelandena från skärmen och nollställer samtalskontexten.")) {
                    self.gemini.clearHistory();
                    const chatHistory = document.getElementById('chat-history');
                    chatHistory.innerHTML = `
                        <div class="chat-msg system-msg">
                            <div class="msg-sender">[SYS]</div>
                            <div class="msg-content">Samtalskontext återställd. Chatten rensad.</div>
                        </div>
                    `;
                    self.writeLog("NEURAL CONTEXT RESET & CHAT CLEARED", "sys");
                }
            });
        }

        // Test Voice button click trigger
        const btnTestVoice = document.getElementById('btn-test-voice');
        if (btnTestVoice) {
            btnTestVoice.addEventListener('click', () => {
                soundSynth.playClick();
                const sv = self.speech.lang === 'sv-SE';
                const testMsg = sv 
                    ? "Detta är en testsekvens för Frejas röstgränssnitt."
                    : "This is a diagnostic vocal sequence for Freya.";
                self.speech.speak(testMsg);
            });
        }

        // Open settings modal
        const btnSettings = document.getElementById('btn-settings');
        const modalSettings = document.getElementById('modal-settings');
        const btnCloseSettings = document.getElementById('btn-close-settings');
        
        btnSettings.addEventListener('click', () => {
            soundSynth.playClick();
            modalSettings.classList.add('active');
        });
        
        btnCloseSettings.addEventListener('click', () => {
            soundSynth.playClick();
            modalSettings.classList.remove('active');
        });

        // Toggle API Keys visibility masks
        const btnToggleKey = document.getElementById('btn-toggle-key');
        const inputApiKey = document.getElementById('input-api-key');
        btnToggleKey.addEventListener('click', () => {
            soundSynth.playClick();
            if (inputApiKey.type === 'password') {
                inputApiKey.type = 'text';
                btnToggleKey.innerHTML = '<i class="fa-solid fa-eye-slash"></i>';
            } else {
                inputApiKey.type = 'password';
                btnToggleKey.innerHTML = '<i class="fa-solid fa-eye"></i>';
            }
        });

        const btnToggleElevenKey = document.getElementById('btn-toggle-eleven-key');
        const inputElevenKey = document.getElementById('input-eleven-key');
        btnToggleElevenKey.addEventListener('click', () => {
            soundSynth.playClick();
            if (inputElevenKey.type === 'password') {
                inputElevenKey.type = 'text';
                btnToggleElevenKey.innerHTML = '<i class="fa-solid fa-eye-slash"></i>';
            } else {
                inputElevenKey.type = 'password';
                btnToggleElevenKey.innerHTML = '<i class="fa-solid fa-eye"></i>';
            }
        });

        const btnToggleMem0Key = document.getElementById('btn-toggle-mem0-key');
        const inputMem0Key = document.getElementById('input-mem0-key');
        btnToggleMem0Key.addEventListener('click', () => {
            soundSynth.playClick();
            if (inputMem0Key.type === 'password') {
                inputMem0Key.type = 'text';
                btnToggleMem0Key.innerHTML = '<i class="fa-solid fa-eye-slash"></i>';
            } else {
                inputMem0Key.type = 'password';
                btnToggleMem0Key.innerHTML = '<i class="fa-solid fa-eye"></i>';
            }
        });

        // Toggle Memory Vault Overlay modal
        const btnMemory = document.getElementById('btn-memory');
        const modalMemory = document.getElementById('modal-memory');
        const btnCloseMemory = document.getElementById('btn-close-memory');
        
        btnMemory.addEventListener('click', () => {
            soundSynth.playClick();
            modalMemory.classList.add('active');
            self.loadMemoryVaultUI();
        });
        
        btnCloseMemory.addEventListener('click', () => {
            soundSynth.playClick();
            modalMemory.classList.remove('active');
        });

        // Insert new engram cards manually
        const btnAddMemoryManual = document.getElementById('btn-add-memory-manual');
        const inputNewMemory = document.getElementById('input-new-memory');
        
        btnAddMemoryManual.addEventListener('click', async () => {
            const text = inputNewMemory.value.trim();
            if (!text) return;
            soundSynth.playClick();
            inputNewMemory.value = "";
            self.writeLog("ENCODING MANUAL MEMORY ENGRAM", "sys");
            
            const success = await self.memory.addMemoryManual(text);
            if (success) {
                self.writeLog("MANUAL ENGRAM SECURED", "sys");
                soundSynth.playNotify();
                self.loadMemoryVaultUI();
            } else {
                self.writeLog("ENGRAM COGNITION FAILURE", "err");
                soundSynth.playError();
            }
        });
        
        inputNewMemory.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') btnAddMemoryManual.click();
        });
        
        // Synchronize engrams list
        const btnRefreshMemory = document.getElementById('btn-refresh-memory');
        btnRefreshMemory.addEventListener('click', () => {
            soundSynth.playClick();
            self.writeLog("SYNCHRONIZING NEURAL ENGRAMS", "sys");
            self.loadMemoryVaultUI();
        });
        
        // Core Memory wipe-out button
        const btnWipeMemory = document.getElementById('btn-wipe-memory');
        btnWipeMemory.addEventListener('click', async () => {
            soundSynth.playError();
            if (confirm("VIKTIGT: Är du säker på att du vill radera alla samlade minnen och engram? Detta kan inte ångras.")) {
                self.writeLog("INITIATING CORE MEMORY WIPE", "warn");
                const success = await self.memory.deleteAllMemories();
                if (success) {
                    self.writeLog("NEURAL PATHWAYS COMPLETELY CLEARED", "sys");
                    soundSynth.playStartupSweep();
                    self.loadMemoryVaultUI();
                } else {
                    self.writeLog("MEMORY CLEARANCE ABORTED OR FAILED", "err");
                }
            }
        });

        // Local Speech Synthesizer voice selectors changes
        const selectVoice = document.getElementById('select-voice');
        selectVoice.addEventListener('change', () => {
            self.speech.voiceIndex = selectVoice.value ? parseInt(selectVoice.value) : null;
        });

        // ElevenLabs voice dropdown selection triggers
        const selectElevenVoice = document.getElementById('select-eleven-voice');
        const groupElevenCustom = document.getElementById('group-eleven-custom');
        selectElevenVoice.addEventListener('change', () => {
            if (selectElevenVoice.value === 'custom') {
                groupElevenCustom.style.display = 'block';
            } else {
                groupElevenCustom.style.display = 'none';
            }
        });

        // Sliders change bindings
        const sliderRate = document.getElementById('slider-rate');
        sliderRate.addEventListener('input', () => {
            document.getElementById('val-rate').textContent = sliderRate.value;
            self.speech.rate = parseFloat(sliderRate.value);
        });

        const sliderPitch = document.getElementById('slider-pitch');
        sliderPitch.addEventListener('input', () => {
            document.getElementById('val-pitch').textContent = sliderPitch.value;
            self.speech.pitch = parseFloat(sliderPitch.value);
        });

        // Save settings form actions
        const btnSaveSettings = document.getElementById('btn-save-settings');
        btnSaveSettings.addEventListener('click', () => {
            soundSynth.playClick();
            
            // Save API Keys
            const apiKey = inputApiKey.value.trim();
            self.gemini.setApiKey(apiKey);
            
            localStorage.setItem("freja_speech_rate", sliderRate.value);
            localStorage.setItem("freja_speech_pitch", sliderPitch.value);
            localStorage.setItem("freja_speech_persona", document.getElementById('textarea-persona').value);
            self.gemini.systemPrompt = document.getElementById('textarea-persona').value;

            if (selectVoice.value) {
                localStorage.setItem("freja_speech_voiceidx", selectVoice.value);
            }

            const elevenKey = document.getElementById('input-eleven-key').value.trim();
            const elevenVoice = document.getElementById('select-eleven-voice').value;
            const elevenCustomVoice = document.getElementById('input-eleven-custom-voice').value.trim();

            localStorage.setItem("freja_eleven_apikey", elevenKey);
            self.speech.elevenApiKey = elevenKey;

            localStorage.setItem("freja_eleven_voice", elevenVoice);
            self.speech.elevenVoice = elevenVoice;

            localStorage.setItem("freja_eleven_custom_voice", elevenCustomVoice);
            self.speech.elevenCustomVoice = elevenCustomVoice;

            // Commit Mem0 configs
            const mem0Key = document.getElementById('input-mem0-key').value.trim();
            const mem0Enabled = document.getElementById('chk-use-mem0').checked;
            
            self.memory.saveSettings(mem0Key, mem0Enabled);

            // Save tool permissions
            const chkWeather = document.getElementById('chk-tool-get_weather');
            if (chkWeather) {
                localStorage.setItem("freja_tool_get_weather_allowed", chkWeather.checked);
            }

            modalSettings.classList.remove('active');
            self.writeLog("INTERFACE NETWORK CONFIGURATIONS SECURED", "sys");
            soundSynth.playNotify();
        });

        // Reset Settings button triggers
        const btnResetSettings = document.getElementById('btn-reset-settings');
        btnResetSettings.addEventListener('click', () => {
            soundSynth.playError();
            if (confirm("Vill du återställa alla inställningar till grundutförande?")) {
                localStorage.clear();
                self.gemini.clearHistory();
                location.reload();
            }
        });

        // Open Accent Themes selector modal
        const btnThemeToggle = document.getElementById('btn-theme-toggle');
        const modalTheme = document.getElementById('modal-theme');
        const btnCloseTheme = document.getElementById('btn-close-theme');
        
        btnThemeToggle.addEventListener('click', () => {
            soundSynth.playClick();
            modalTheme.classList.add('active');
        });
        
        btnCloseTheme.addEventListener('click', () => {
            soundSynth.playClick();
            modalTheme.classList.remove('active');
        });

        // Selecting accent palettes cards
        const themeChoiceCards = document.querySelectorAll('.theme-choice-card');
        themeChoiceCards.forEach(card => {
            card.addEventListener('click', () => {
                soundSynth.playClick();
                themeChoiceCards.forEach(c => c.classList.remove('active'));
                card.classList.add('active');
                
                const selectedTheme = card.getAttribute('data-theme');
                self.applyTheme(selectedTheme);
                modalTheme.classList.remove('active');
                self.writeLog(`THEME SCHEME CONFIGURED: ${selectedTheme.toUpperCase()}`, "sys");
            });
        });

        // Speech engine dynamic local voices lists filler callback
        this.speech.voiceUpdateCallback = (voices) => {
            selectVoice.innerHTML = '<option value="">Standard Röst (Automatisk)</option>';
            voices.forEach((voice, index) => {
                const option = document.createElement('option');
                option.value = index;
                option.textContent = `${voice.name} (${voice.lang})`;
                
                const savedVoiceIdx = localStorage.getItem("freja_speech_voiceidx");
                if (savedVoiceIdx && parseInt(savedVoiceIdx) === index) {
                    option.selected = true;
                    self.speech.voiceIndex = index;
                }
                
                selectVoice.appendChild(option);
            });
        };
    }

    /**
     * Switches the page styling accent themes classes.
     */
    applyTheme(theme) {
        document.body.className = `theme-${theme}`;
        localStorage.setItem("freja_theme", theme);
        
        const cards = document.querySelectorAll('.theme-choice-card');
        cards.forEach(card => {
            if (card.getAttribute('data-theme') === theme) {
                card.classList.add('active');
            } else {
                card.classList.remove('active');
            }
        });

        const hue = this.getCurrentThemeHue();
        if (window.visualizer) {
            window.visualizer.setThemeHue(hue);
        }
    }

    /**
     * Resolves the canvas accent hue angle based on the selected CSS theme.
     */
    getCurrentThemeHue() {
        if (document.body.classList.contains('theme-amber')) return 38;
        if (document.body.classList.contains('theme-crimson')) return 355;
        if (document.body.classList.contains('theme-emerald')) return 145;
        return 185; // Default Cyan
    }

    /**
     * Synchronizes and draws engram cards list inside the Memory Vault UI overlay.
     */
    async loadMemoryVaultUI() {
        const memoriesList = document.getElementById('memories-list');
        const memoryCount = document.getElementById('memory-count');
        const statusVal = document.getElementById('memory-engine-status');
        
        const isSandbox = this.memory.isSandboxMode();
        statusVal.textContent = this.memory.getEngineStatusText();
        if (isSandbox) {
            statusVal.className = "status-val status-sandbox";
        } else {
            statusVal.className = "status-val status-online";
        }
        
        memoriesList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">Laddar minnesengram...</div>';
        
        try {
            const memories = await this.memory.getAllMemories();
            memoryCount.textContent = memories.length;
            
            if (memories.length === 0) {
                memoriesList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[INGA MINNESFRAGMENT UPPTÄCKTA]</div>';
                return;
            }
            
            memoriesList.innerHTML = "";
            memories.forEach(m => {
                const card = document.createElement('div');
                card.className = "memory-engram-card";
                
                card.innerHTML = `
                    <div class="memory-engram-text">${this.escapeHTML(m.memory)}</div>
                    <button class="memory-engram-delete-btn" data-id="${m.id}" title="Radera detta engram">
                        <i class="fa-solid fa-trash-can"></i>
                    </button>
                `;
                
                const delBtn = card.querySelector('.memory-engram-delete-btn');
                delBtn.addEventListener('click', async () => {
                    soundSynth.playClick();
                    const memId = delBtn.getAttribute('data-id');
                    card.style.opacity = '0.5';
                    const success = await this.memory.deleteMemory(memId);
                    if (success) {
                        this.writeLog("ENGRAM PURGED SECURELY", "sys");
                        card.remove();
                        const currentCount = parseInt(memoryCount.textContent) - 1;
                        memoryCount.textContent = currentCount;
                        if (currentCount === 0) {
                            memoriesList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[INGA MINNESFRAGMENT UPPTÄCKTA]</div>';
                        }
                    } else {
                        card.style.opacity = '1';
                        this.writeLog("PURGE SYSTEM REFUSED OPERATIONAL DIRECTIVE", "err");
                        soundSynth.playError();
                    }
                });
                
                memoriesList.appendChild(card);
            });
            
        } catch (e) {
            console.error("[MEM0] UI Load error:", e);
            memoriesList.innerHTML = '<div style="color: #ff3b30; text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[ALLVARLIGT FEL: KUNDE INTE SYNKRONISERA MINNE]</div>';
        }
    }

    /**
     * Executes conversational transactions, drawing replies and managing long-term memory encodes.
     */
    async processUserQuery(text) {
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
                // Heuristic vision keywords check
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
        this.appendChatMessage("assistant", response);
        
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
    appendChatMessage(sender, text) {
        const chatHistory = document.getElementById('chat-history');
        const msgDiv = document.createElement('div');
        msgDiv.className = `chat-msg ${sender}-msg`;
        
        const senderTag = sender === 'user' ? '[USER]' : '[FREJA]';
        
        // Quick custom regex parser for markdown elements
        const formattedText = this.parseMarkdown(text);
        
        msgDiv.innerHTML = `
            <div class="msg-sender">${senderTag}</div>
            <div class="msg-content">${formattedText}</div>
        `;
        
        chatHistory.appendChild(msgDiv);
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    /**
     * Translates custom bold lists, ticks, and code block formatting to raw HTML tags.
     */
    parseMarkdown(text) {
        // Temporary store for code blocks
        const codeBlocks = [];
        let html = text;
        
        // 1. Extract and escape code blocks
        html = html.replace(/```([\s\S]*?)```/g, (match, p1) => {
            const id = `__CODE_BLOCK_${codeBlocks.length}__`;
            const escaped = this.escapeHTML(p1.trim());
            codeBlocks.push(`<pre><code>${escaped}</code><button class="copy-code-btn" title="Kopiera kod" onclick="window.uiController.copyCode(this)"><i class="fa-solid fa-copy"></i></button></pre>`);
            return id;
        });
        
        // 2. Parse inline code ticks
        html = html.replace(/`([^`]+)`/g, (match, p1) => {
            return `<code>${this.escapeHTML(p1)}</code>`;
        });
        
        // 3. Parse bold symbols
        html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        
        // 4. Parse italics
        html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
        
        // 5. Parse links: [label](url)
        html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" class="hud-link">$1 <i class="fa-solid fa-arrow-up-right-from-square" style="font-size: 8px;"></i></a>');
        
        // 6. Parse list items (lines starting with * or - or •)
        html = html.replace(/^[-*•]\s+(.+)$/gm, '• $1');
        
        // 7. Replace newlines with <br>
        html = html.replace(/\n/g, '<br>');
        
        // 8. Restore code blocks
        codeBlocks.forEach((block, index) => {
            html = html.replace(`__CODE_BLOCK_${index}__`, block);
        });
        
        return html;
    }

    /**
     * Copies code content from code blocks to user clipboard.
     */
    copyCode(button) {
        const pre = button.parentElement;
        const code = pre.querySelector('code');
        if (!code) return;
        
        navigator.clipboard.writeText(code.innerText).then(() => {
            soundSynth.playNotify();
            const originalHTML = button.innerHTML;
            button.innerHTML = '<i class="fa-solid fa-check" style="color: var(--color-primary);"></i>';
            this.writeLog("CODE COPIED TO SYSTEM CLIPBOARD", "sys");
            setTimeout(() => {
                button.innerHTML = originalHTML;
            }, 2000);
        }).catch(err => {
            console.error("Failed to copy code: ", err);
            soundSynth.playError();
        });
    }

    /**
     * Handles tool calls requested by Gemini.
     * Checks permissions, prompts the user if permission is missing, and executes the tool.
     */
    async handleToolCall(call) {
        this.writeLog(`TOOL CALL REQUESTED: ${call.name}`, "sys");
        
        const tool = window.FrejaTools ? window.FrejaTools[call.name] : null;
        if (!tool) {
            this.writeLog(`ERROR: Tool '${call.name}' not registered in systems`, "err");
            return { error: `Tool '${call.name}' not registered.` };
        }
        
        // Check permission (either true/false from localStorage)
        const isAllowed = localStorage.getItem(tool.permissionKey) === "true";
        
        if (isAllowed) {
            this.writeLog(`EXECUTING TOOL: ${tool.name}`, "sys");
            try {
                const result = await tool.execute(call.args);
                this.writeLog(`TOOL EXECUTION SUCCESS: ${tool.name}`, "sys");
                return result;
            } catch (err) {
                this.writeLog(`TOOL EXECUTION ERROR: ${err.message}`, "err");
                return { error: `Execution failed: ${err.message}` };
            }
        } else {
            // Permission is not granted, ask the user!
            this.writeLog(`PERMISSION REQUIRED FOR TOOL: ${tool.name}`, "warn");
            
            // We return a Promise that resolves when the user allows or denies
            const allowed = await new Promise((resolve) => {
                this.appendPermissionRequest(tool, call.args, resolve);
            });
            
            if (allowed) {
                this.writeLog(`EXECUTING TOOL POST-APPROVAL: ${tool.name}`, "sys");
                try {
                    const result = await tool.execute(call.args);
                    this.writeLog(`TOOL EXECUTION SUCCESS: ${tool.name}`, "sys");
                    return result;
                } catch (err) {
                    this.writeLog(`TOOL EXECUTION ERROR: ${err.message}`, "err");
                    return { error: `Execution failed: ${err.message}` };
                }
            } else {
                this.writeLog(`TOOL ACCESS DENIED BY USER: ${tool.name}`, "warn");
                return { error: "User denied permission to run this tool." };
            }
        }
    }

    /**
     * Renders a warning gateway permission request message in the chat.
     */
    appendPermissionRequest(tool, args, resolvePromise) {
        const chatHistory = document.getElementById('chat-history');
        const msgDiv = document.createElement('div');
        msgDiv.className = 'chat-msg system-msg permission-request-msg';
        
        const argsStr = JSON.stringify(args, null, 2);
        
        msgDiv.innerHTML = `
            <div class="msg-sender">[SÄKERHETS-GATEWAY]</div>
            <div class="msg-content glass-morphic" style="border-color: #fdd663; padding: 12px; margin-top: 5px; background: rgba(25, 20, 10, 0.45);">
                <h4 style="color: #fdd663; margin-top: 0; font-family: var(--font-display); font-size: 11px; letter-spacing: 1px;">
                    <i class="fa-solid fa-shield-halved"></i> BEHÖRIGHETSBEGÄRAN KRÄVS
                </h4>
                <p style="font-size: 11px; margin: 6px 0; line-height: 1.4; color: #f8f9fa;">
                    FREJA begär åtkomst till verktyget <strong>${tool.displayName || tool.name}</strong> för att slutföra din begäran.
                </p>
                <div style="background: rgba(0,0,0,0.6); border: 1px solid rgba(253, 214, 99, 0.2); border-radius: 4px; padding: 6px; font-family: var(--font-mono); font-size: 10px; color: #fdd663; margin-bottom: 10px; white-space: pre-wrap;">Argument: ${argsStr}</div>
                <div style="display: flex; gap: 8px;">
                    <button class="hud-btn btn-primary btn-allow-once" style="background: #fdd663; border-color: #fdd663; color: #000; font-size: 10px; padding: 4px 10px;">Tillåt denna gång</button>
                    <button class="hud-btn btn-secondary btn-allow-always" style="font-size: 10px; padding: 4px 10px;">Tillåt alltid</button>
                    <button class="hud-btn btn-secondary btn-deny" style="border-color: #ff3b30; color: #ff3b30; font-size: 10px; padding: 4px 10px;">Neka</button>
                </div>
            </div>
        `;
        
        chatHistory.appendChild(msgDiv);
        chatHistory.scrollTop = chatHistory.scrollHeight;
        
        // Speak warning notification sound or synthesize a voice warning
        soundSynth.playNotify();
        
        const self = this;
        
        // Button Event Listeners
        const btnAllowOnce = msgDiv.querySelector('.btn-allow-once');
        const btnAllowAlways = msgDiv.querySelector('.btn-allow-always');
        const btnDeny = msgDiv.querySelector('.btn-deny');
        
        btnAllowOnce.addEventListener('click', () => {
            soundSynth.playClick();
            msgDiv.remove();
            self.writeLog(`TOOL PERMISSION GRANTED: ${tool.name} (ONCE)`, "sys");
            resolvePromise(true);
        });
        
        btnAllowAlways.addEventListener('click', () => {
            soundSynth.playClick();
            msgDiv.remove();
            // Save always allowed
            localStorage.setItem(tool.permissionKey, "true");
            // Sync UI checkbox if settings modal is open or loaded
            const chk = document.getElementById(`chk-tool-${tool.name}`);
            if (chk) chk.checked = true;
            
            self.writeLog(`TOOL PERMISSION GRANTED: ${tool.name} (ALWAYS)`, "sys");
            resolvePromise(true);
        });
        
        btnDeny.addEventListener('click', () => {
            soundSynth.playError();
            msgDiv.remove();
            self.writeLog(`TOOL PERMISSION DENIED: ${tool.name}`, "sys");
            resolvePromise(false);
        });
    }

    /**
     * Sanitizes strings to prevent XSS injection.
     */
    escapeHTML(text) {
        return text
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    /**
     * Appends a glowing operational tag row into the console logs console terminal.
     */
    writeLog(msg, type = 'sys') {
        const logContainer = document.getElementById('terminal-log');
        const now = new Date();
        const timeStr = now.toTimeString().split(' ')[0];
        
        const line = document.createElement('div');
        line.className = 'log-line';
        
        let tag = "[SYS]";
        if (type === 'user') tag = "[USER]";
        if (type === 'gemini') tag = "[GMNI]";
        if (type === 'warn') tag = "[WARN]";
        if (type === 'err') tag = "[ERR ]";
        
        line.innerHTML = `
            <span class="log-time">${timeStr}</span>
            <span class="log-tag tag-${type}">${tag}</span>
            ${msg.toUpperCase()}
        `;
        
        logContainer.appendChild(line);
        logContainer.scrollTop = logContainer.scrollHeight;
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
        const selectCam = document.getElementById('select-camera');
        if (!selectCam) return;
        
        try {
            const devices = await navigator.mediaDevices.enumerateDevices();
            const videoDevices = devices.filter(d => d.kind === 'videoinput');
            
            selectCam.innerHTML = '<option value="off">Scanner avstängd</option>';
            
            if (videoDevices.length === 0) {
                console.warn("[CAMERA] No camera devices found.");
                return;
            }
            
            videoDevices.forEach((device, index) => {
                const option = document.createElement('option');
                option.value = device.deviceId;
                option.textContent = device.label || `Kamera ${index + 1}`;
                
                if (this.savedCameraId && device.deviceId === this.savedCameraId) {
                    option.selected = true;
                }
                
                selectCam.appendChild(option);
            });
            
            // Auto start camera if we have a saved, active camera stream
            if (this.savedCameraId && this.savedCameraId !== 'off' && !this.cameraStream) {
                if (videoDevices.some(d => d.deviceId === this.savedCameraId)) {
                    selectCam.value = this.savedCameraId;
                    this.startCameraStream(this.savedCameraId);
                }
            }
            
            console.log("[CAMERA] Enumerated video input devices:", videoDevices);
        } catch (e) {
            console.error("[CAMERA] Failed to enumerate devices:", e);
        }
    }

    /**
     * Binds camera video media streams to HUD visual feed boxes.
     */
    async startCameraStream(deviceId) {
        const video = document.getElementById('webcam-video');
        const status = document.getElementById('scanner-status');
        const capCamera = document.getElementById('cap-camera');
        
        this.stopCameraStream();
        
        if (deviceId === 'off') {
            return;
        }

        this.writeLog("ESTABLISHING OPTICAL LINK...", "sys");
        soundSynth.playClick();
        
        try {
            // Resilient constraints using 'ideal' instead of 'exact' to prevent OverconstrainedError
            const constraints = {
                video: {
                    deviceId: deviceId ? { ideal: deviceId } : undefined,
                    width: { ideal: 640 },
                    height: { ideal: 480 }
                }
            };
            
            let stream;
            try {
                stream = await navigator.mediaDevices.getUserMedia(constraints);
            } catch (innerErr) {
                console.warn("[CAMERA] Detailed constraints failed, trying basic video fallback...", innerErr);
                // Hard fallback: request standard video without device constraints
                stream = await navigator.mediaDevices.getUserMedia({ video: true });
            }
            
            this.cameraStream = stream;
            
            video.srcObject = stream;
            video.classList.add('active');
            
            if (status) {
                status.textContent = "SCANNING: SUBJECT ACTIVE";
            }
            
            if (capCamera) {
                capCamera.classList.add('active');
            }
            
            this.writeLog("OPTICAL CHANNEL SECURED", "sys");
            soundSynth.playNotify();
            
            // Re-enumerate to retrieve friendly camera labels now that permission has been granted
            setTimeout(() => this.loadCameraDevices(), 500);
            
        } catch (e) {
            console.error("[CAMERA] Failed to acquire stream:", e);
            this.writeLog("OPTICAL CAPTURE DENIED OR FAILED", "err");
            soundSynth.playError();
            
            document.getElementById('select-camera').value = 'off';
            this.stopCameraStream();
        }
    }

    /**
     * Stops the camera webcam streams and clears hardware binding feeds.
     */
    stopCameraStream() {
        const video = document.getElementById('webcam-video');
        const status = document.getElementById('scanner-status');
        const capCamera = document.getElementById('cap-camera');
        
        if (this.cameraStream) {
            this.cameraStream.getTracks().forEach(track => track.stop());
            this.cameraStream = null;
        }
        
        if (video) {
            video.srcObject = null;
            video.classList.remove('active');
        }
        
        if (status) {
            status.textContent = "OPTICS OFFLINE";
        }
        
        if (capCamera) {
            capCamera.classList.remove('active');
        }
    }

    /**
     * Triggers dynamic diagnostic values simulation metrics fluctuations inside HUD cards.
     */
    startDiagnosticSimulation() {
        const cpuVal = document.getElementById('val-cpu');
        const cpuBar = document.getElementById('bar-cpu');
        const tempVal = document.getElementById('val-temp');
        const tempBar = document.getElementById('bar-temp');
        const ramVal = document.getElementById('val-ram');
        const ramBar = document.getElementById('bar-ram');
        const pingVal = document.getElementById('val-ping');
        const pingBar = document.getElementById('bar-ping');

        let ramUsage = 6.2;

        setInterval(() => {
            const cpu = Math.floor(Math.random() * 20) + 12; // 12-32% CPU usage
            cpuVal.textContent = `${cpu}%`;
            cpuBar.style.width = `${cpu}%`;

            const temp = 40.5 + (cpu * 0.15) + (Math.random() * 0.4);
            tempVal.textContent = `${temp.toFixed(1)} °C`;
            tempBar.style.width = `${Math.min(temp, 100)}%`;

            ramUsage += (Math.random() * 0.1 - 0.05);
            ramUsage = Math.max(5.8, Math.min(6.8, ramUsage));
            const ramPercent = (ramUsage / 16) * 100;
            ramVal.textContent = `${ramUsage.toFixed(1)} GB / 16 GB`;
            ramBar.style.width = `${ramPercent}%`;

            const ping = Math.floor(Math.random() * 8) + 10; // 10-18ms network latency
            pingVal.textContent = `${ping} ms`;
            pingBar.style.width = `${ping * 4}%`;

        }, 3000);
    }
}

// Instantiates the UI controller once the DOM elements have loaded successfully
window.addEventListener('DOMContentLoaded', () => {
    window.uiController = new FrejaUIController();
});
