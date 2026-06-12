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
        
        // Asynchronously load keys from SQLite and then initialize UI
        this.initAsync();
        
        // Keep systems kronometer clocks in sync
        setInterval(() => this.updateTimeAndDate(), 1000);
    }

    async initAsync() {
        await this.loadKeysFromServer();
        this.initializeUI();
        this.bindEvents();
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
                ['garmin', 'strava', 'withings'].forEach(provider => {
                    if (statusData.states && statusData.states[provider] === 'syncing') {
                        this.pollSyncStatus(provider);
                    }
                });
            }
        } catch (err) {
            console.error("Error checking active syncs:", err);
        }
    }

    async pollSyncStatus(provider) {
        if (this[`syncInterval_${provider}`]) return; // already polling
        
        const self = this;
        const btn = document.getElementById(`btn-sync-${provider}-dashboard`);
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> SYNKAR...`;
        }
        
        const capItem = document.getElementById(`cap-${provider}`);
        if (capItem) {
            capItem.classList.add('syncing-blink');
        }

        self.writeLog(`SYNC BACKGROUND TASK STARTED FOR ${provider.toUpperCase()}`, "sys");

        this[`syncInterval_${provider}`] = setInterval(async () => {
            try {
                const res = await fetch('/api/sync/status');
                if (res.ok) {
                    const statusData = await res.json();
                    const state = statusData.states[provider];
                    const error = statusData.errors[provider];
                    
                    if (state === 'success') {
                        clearInterval(self[`syncInterval_${provider}`]);
                        self[`syncInterval_${provider}`] = null;
                        
                        if (btn) {
                            btn.disabled = false;
                            btn.innerHTML = `<i class="fa-solid fa-arrows-rotate"></i> SYNKRONISERA ENHET`;
                        }
                        if (capItem) {
                            capItem.classList.remove('syncing-blink');
                        }
                        
                        self.writeLog(`BACKGROUND SYNCHRONIZATION COMPLETED FOR ${provider.toUpperCase()}`, "sys");
                        soundSynth.playNotify();
                        
                        if (provider === 'garmin') self.loadGarminDashboardUI();
                        if (provider === 'strava') self.loadStravaDashboardUI();
                        if (provider === 'withings') self.loadWithingsDashboardUI();
                        
                    } else if (state === 'error') {
                        clearInterval(self[`syncInterval_${provider}`]);
                        self[`syncInterval_${provider}`] = null;
                        
                        if (btn) {
                            btn.disabled = false;
                            btn.innerHTML = `<i class="fa-solid fa-arrows-rotate"></i> SYNKRONISERA ENHET`;
                        }
                        if (capItem) {
                            capItem.classList.remove('syncing-blink');
                        }
                        
                        self.writeLog(`${provider.toUpperCase()} SYNC ERROR: ${error}`, "err");
                        soundSynth.playError();
                    }
                }
            } catch (err) {
                console.error(`Error polling sync status for ${provider}:`, err);
            }
        }, 2000);
    }

    /**
     * Fetches API keys from the secure SQLite database server and saves them to local cache.
     */
    async loadKeysFromServer() {
        try {
            this.writeLog("CONNECTING TO SECURE DATABASE...", "sys");
            const response = await fetch('/api/keys');
            if (response.ok) {
                const keys = await response.json();
                if (keys.freja_gemini_apikey !== undefined) {
                    localStorage.setItem("freja_gemini_apikey", keys.freja_gemini_apikey);
                }
                if (keys.freja_eleven_apikey !== undefined) {
                    localStorage.setItem("freja_eleven_apikey", keys.freja_eleven_apikey);
                }
                if (keys.freja_mem0_apikey !== undefined) {
                    localStorage.setItem("freja_mem0_apikey", keys.freja_mem0_apikey);
                }
                if (keys.freja_garmin_email !== undefined) {
                    localStorage.setItem("freja_garmin_email", keys.freja_garmin_email);
                }
                if (keys.freja_garmin_password !== undefined) {
                    localStorage.setItem("freja_garmin_password", keys.freja_garmin_password);
                }
                if (keys.freja_strava_client_id !== undefined) {
                    localStorage.setItem("freja_strava_client_id", keys.freja_strava_client_id);
                }
                if (keys.freja_strava_client_secret !== undefined) {
                    localStorage.setItem("freja_strava_client_secret", keys.freja_strava_client_secret);
                }
                if (keys.freja_strava_refresh_token !== undefined) {
                    localStorage.setItem("freja_strava_refresh_token", keys.freja_strava_refresh_token);
                }
                if (keys.freja_withings_client_id !== undefined) {
                    localStorage.setItem("freja_withings_client_id", keys.freja_withings_client_id);
                }
                if (keys.freja_withings_client_secret !== undefined) {
                    localStorage.setItem("freja_withings_client_secret", keys.freja_withings_client_secret);
                }
                if (keys.freja_withings_refresh_token !== undefined) {
                    localStorage.setItem("freja_withings_refresh_token", keys.freja_withings_refresh_token);
                }
                
                // Refresh components keys if already instantiated
                if (this.gemini) this.gemini.loadApiKey();
                if (this.memory) this.memory.loadSettings();
                if (this.speech) this.speech.elevenApiKey = keys.freja_eleven_apikey || "";
                
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
            } else {
                this.writeLog("API KEYS SAVE ERROR: DATABASE OFFLINE", "err");
            }
        } catch (e) {
            console.error("[FREJA] Failed to save keys to server:", e);
            this.writeLog("API KEYS SAVE ERROR: CONNECTION FAILED", "err");
        }
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
        
        const garminEmail = localStorage.getItem("freja_garmin_email") || "";
        const garminPassword = localStorage.getItem("freja_garmin_password") || "";
        const inputGarminEmail = document.getElementById('input-garmin-email');
        if (inputGarminEmail) inputGarminEmail.value = garminEmail;
        const inputGarminPassword = document.getElementById('input-garmin-password');
        if (inputGarminPassword) inputGarminPassword.value = garminPassword;
        
        const stravaClientId = localStorage.getItem("freja_strava_client_id") || "";
        const stravaClientSecret = localStorage.getItem("freja_strava_client_secret") || "";
        const stravaRefreshToken = localStorage.getItem("freja_strava_refresh_token") || "";
        const inputStravaClientId = document.getElementById('input-strava-client-id');
        if (inputStravaClientId) inputStravaClientId.value = stravaClientId;
        const inputStravaClientSecret = document.getElementById('input-strava-client-secret');
        if (inputStravaClientSecret) inputStravaClientSecret.value = stravaClientSecret;
        const inputStravaRefreshToken = document.getElementById('input-strava-refresh-token');
        if (inputStravaRefreshToken) inputStravaRefreshToken.value = stravaRefreshToken;
        
        // Dynamically build and update Strava authorize link
        const updateStravaLink = () => {
            const clientId = inputStravaClientId ? inputStravaClientId.value.trim() : "";
            const authLink = document.getElementById('lnk-strava-authorize');
            console.log("[DEBUG STRAVA LINK] clientId:", clientId, "authLink:", authLink);
            if (authLink) {
                if (clientId) {
                    const redirectUri = window.location.origin + '/api/strava/callback';
                    authLink.href = `https://www.strava.com/oauth/authorize?client_id=${clientId}&redirect_uri=${encodeURIComponent(redirectUri)}&response_type=code&scope=activity:read,activity:read_all`;
                    authLink.style.display = 'block';
                    console.log("[DEBUG STRAVA LINK] Updated link: display block, href:", authLink.href);
                } else {
                    authLink.style.display = 'none';
                    console.log("[DEBUG STRAVA LINK] Hidden link: display none");
                }
            }
        };
        if (inputStravaClientId) {
            inputStravaClientId.addEventListener('input', updateStravaLink);
        }
        updateStravaLink();
        
        const withingsClientId = localStorage.getItem("freja_withings_client_id") || "";
        const withingsClientSecret = localStorage.getItem("freja_withings_client_secret") || "";
        const withingsRefreshToken = localStorage.getItem("freja_withings_refresh_token") || "";
        const inputWithingsClientId = document.getElementById('input-withings-client-id');
        if (inputWithingsClientId) inputWithingsClientId.value = withingsClientId;
        const inputWithingsClientSecret = document.getElementById('input-withings-client-secret');
        if (inputWithingsClientSecret) inputWithingsClientSecret.value = withingsClientSecret;
        const inputWithingsRefreshToken = document.getElementById('input-withings-refresh-token');
        if (inputWithingsRefreshToken) inputWithingsRefreshToken.value = withingsRefreshToken;
        
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

        const searchAllowed = localStorage.getItem("freja_tool_google_search_allowed") === "true";
        const chkSearch = document.getElementById('chk-tool-google_search');
        if (chkSearch) {
            chkSearch.checked = searchAllowed;
        }

        const garminAllowed = localStorage.getItem("freja_tool_get_garmin_health_allowed") === "true";
        const chkGarmin = document.getElementById('chk-tool-get_garmin_health');
        if (chkGarmin) {
            chkGarmin.checked = garminAllowed;
        }

        const capGarmin = document.getElementById('cap-garmin');
        if (capGarmin) {
            if (garminAllowed) {
                capGarmin.classList.add('active');
            } else {
                capGarmin.classList.remove('active');
            }
        }

        const stravaAllowed = localStorage.getItem("freja_tool_get_strava_data_allowed") === "true";
        const chkStrava = document.getElementById('chk-tool-get_strava_data');
        if (chkStrava) {
            chkStrava.checked = stravaAllowed;
        }

        const stravaAnalysisAllowed = localStorage.getItem("freja_tool_get_strava_activity_analysis_allowed") === "true";
        const chkStravaAnalysis = document.getElementById('chk-tool-get_strava_activity_analysis');
        if (chkStravaAnalysis) {
            chkStravaAnalysis.checked = stravaAnalysisAllowed;
        }

        const stravaStatsAllowed = localStorage.getItem("freja_tool_get_strava_athlete_stats_allowed") === "true";
        const chkStravaStats = document.getElementById('chk-tool-get_strava_athlete_stats');
        if (chkStravaStats) {
            chkStravaStats.checked = stravaStatsAllowed;
        }

        const capStrava = document.getElementById('cap-strava');
        if (capStrava) {
            if (stravaAllowed || stravaAnalysisAllowed || stravaStatsAllowed) {
                capStrava.classList.add('active');
            } else {
                capStrava.classList.remove('active');
            }
        }

        const withingsAllowed = localStorage.getItem("freja_tool_get_withings_health_allowed") === "true";
        const chkWithings = document.getElementById('chk-tool-get_withings_health');
        if (chkWithings) {
            chkWithings.checked = withingsAllowed;
        }

        const capWithings = document.getElementById('cap-withings');
        if (capWithings) {
            if (withingsAllowed) {
                capWithings.classList.add('active');
            } else {
                capWithings.classList.remove('active');
            }
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
            self.appendChatMessage("user", text, true);
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
            self.appendChatMessage("user", query, true);
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
                    fetch('/api/chat/clear', { method: 'POST' }).catch(e => console.error(e));
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

        const btnToggleGarminPassword = document.getElementById('btn-toggle-garmin-password');
        const inputGarminPassword = document.getElementById('input-garmin-password');
        if (btnToggleGarminPassword && inputGarminPassword) {
            btnToggleGarminPassword.addEventListener('click', () => {
                soundSynth.playClick();
                if (inputGarminPassword.type === 'password') {
                    inputGarminPassword.type = 'text';
                    btnToggleGarminPassword.innerHTML = '<i class="fa-solid fa-eye-slash"></i>';
                } else {
                    inputGarminPassword.type = 'password';
                    btnToggleGarminPassword.innerHTML = '<i class="fa-solid fa-eye"></i>';
                }
            });
        }

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

        // Toggle Garmin Fit Dashboard Modal
        const btnGarmin = document.getElementById('btn-garmin');
        const modalGarmin = document.getElementById('modal-garmin');
        const btnCloseGarmin = document.getElementById('btn-close-garmin');
        
        if (btnGarmin && modalGarmin && btnCloseGarmin) {
            btnGarmin.addEventListener('click', () => {
                soundSynth.playClick();
                modalGarmin.classList.add('active');
                self.loadGarminDashboardUI();
            });
            
            btnCloseGarmin.addEventListener('click', () => {
                soundSynth.playClick();
                modalGarmin.classList.remove('active');
            });
        }

        // Save manual Garmin entry
        const btnSaveGarminManual = document.getElementById('btn-save-garmin-manual');
        if (btnSaveGarminManual) {
            btnSaveGarminManual.addEventListener('click', async () => {
                const dateInput = document.getElementById('garmin-input-date').value;
                if (!dateInput) {
                    self.writeLog("GARMIN DATA FAILURE: DATUM SAKNAS", "err");
                    soundSynth.playError();
                    alert("Ange ett giltigt datum.");
                    return;
                }
                soundSynth.playClick();
                     const payload = {
                    date: dateInput,
                    steps: parseInt(document.getElementById('garmin-input-steps').value) || 0,
                    sleep_hours: parseFloat(document.getElementById('garmin-input-sleep').value) || 0.0,
                    resting_hr: parseInt(document.getElementById('garmin-input-hr').value) || 0,
                    active_calories: parseInt(document.getElementById('garmin-input-calories').value) || 0,
                    workout_type: document.getElementById('garmin-input-workout-type').value.trim(),
                    workout_duration: parseInt(document.getElementById('garmin-input-workout-duration').value) || 0,
                    body_battery: document.getElementById('garmin-input-body-battery').value !== "" ? parseInt(document.getElementById('garmin-input-body-battery').value) : null,
                    hrv: document.getElementById('garmin-input-hrv').value !== "" ? parseInt(document.getElementById('garmin-input-hrv').value) : null
                };
                
                self.writeLog(`SAVING GARMIN LOG FOR ${dateInput}`, "sys");
                try {
                    const res = await fetch('/api/garmin/data', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    const resData = await res.json();
                    if (res.ok && resData.status === 'success') {
                        self.writeLog("GARMIN LOG SECURED IN DATABASE", "sys");
                        soundSynth.playNotify();
                        self.loadGarminDashboardUI();
                        
                        // Clear form input fields except date
                        document.getElementById('garmin-input-steps').value = '';
                        document.getElementById('garmin-input-sleep').value = '';
                        document.getElementById('garmin-input-hr').value = '';
                        document.getElementById('garmin-input-calories').value = '';
                        document.getElementById('garmin-input-workout-type').value = '';
                        document.getElementById('garmin-input-workout-duration').value = '';
                        document.getElementById('garmin-input-body-battery').value = '';
                    } else {
                        throw new Error(resData.message || "Unknown error");
                    }
                } catch (err) {
                    self.writeLog(`GARMIN SAVE ERROR: ${err.message}`, "err");
                    soundSynth.playError();
                }
            });
        }

        // Sync Garmin device simulation from dashboard
        const btnSyncGarminDashboard = document.getElementById('btn-sync-garmin-dashboard');
        if (btnSyncGarminDashboard) {
            btnSyncGarminDashboard.addEventListener('click', async () => {
                soundSynth.playClick();
                self.writeLog("INITIATING GARMIN DEVICE SYNCHRONIZATION", "sys");
                try {
                    const res = await fetch('/api/garmin/sync');
                    const resData = await res.json();
                    if (res.ok && resData.status === 'syncing') {
                        self.pollSyncStatus('garmin');
                    } else {
                        throw new Error(resData.detail || resData.message || "Sync error");
                    }
                } catch (err) {
                    self.writeLog(`GARMIN SYNC ERROR: ${err.message}`, "err");
                    soundSynth.playError();
                }
            });
        }

        const btnToggleStravaSecret = document.getElementById('btn-toggle-strava-secret');
        const inputStravaSecret = document.getElementById('input-strava-client-secret');
        if (btnToggleStravaSecret && inputStravaSecret) {
            btnToggleStravaSecret.addEventListener('click', () => {
                soundSynth.playClick();
                if (inputStravaSecret.type === 'password') {
                    inputStravaSecret.type = 'text';
                    btnToggleStravaSecret.innerHTML = '<i class="fa-solid fa-eye-slash"></i>';
                } else {
                    inputStravaSecret.type = 'password';
                    btnToggleStravaSecret.innerHTML = '<i class="fa-solid fa-eye"></i>';
                }
            });
        }

        const btnToggleStravaToken = document.getElementById('btn-toggle-strava-token');
        const inputStravaToken = document.getElementById('input-strava-refresh-token');
        if (btnToggleStravaToken && inputStravaToken) {
            btnToggleStravaToken.addEventListener('click', () => {
                soundSynth.playClick();
                if (inputStravaToken.type === 'password') {
                    inputStravaToken.type = 'text';
                    btnToggleStravaToken.innerHTML = '<i class="fa-solid fa-eye-slash"></i>';
                } else {
                    inputStravaToken.type = 'password';
                    btnToggleStravaToken.innerHTML = '<i class="fa-solid fa-eye"></i>';
                }
            });
        }

        // Toggle Strava Fit Dashboard Modal
        const btnStrava = document.getElementById('btn-strava');
        const modalStrava = document.getElementById('modal-strava');
        const btnCloseStrava = document.getElementById('btn-close-strava');
        
        if (btnStrava && modalStrava && btnCloseStrava) {
            btnStrava.addEventListener('click', () => {
                soundSynth.playClick();
                modalStrava.classList.add('active');
                
                // Pre-fill today's date if empty
                const dateField = document.getElementById('strava-input-date');
                if (dateField && !dateField.value) {
                    const today = new Date().toISOString().substring(0, 10);
                    dateField.value = today;
                }
                
                self.loadStravaDashboardUI();
            });
            
            btnCloseStrava.addEventListener('click', () => {
                soundSynth.playClick();
                modalStrava.classList.remove('active');
            });
        }

        // Save manual Strava entry
        const btnSaveStravaManual = document.getElementById('btn-save-strava-manual');
        if (btnSaveStravaManual) {
            btnSaveStravaManual.addEventListener('click', async () => {
                const nameInput = document.getElementById('strava-input-name').value.trim();
                const dateInput = document.getElementById('strava-input-date').value;
                if (!dateInput) {
                    self.writeLog("STRAVA DATA FAILURE: DATUM SAKNAS", "err");
                    soundSynth.playError();
                    alert("Ange ett giltigt datum.");
                    return;
                }
                soundSynth.playClick();
                const payload = {
                    name: nameInput || "Manuellt pass",
                    date: dateInput,
                    type: document.getElementById('strava-input-type').value,
                    distance: parseFloat(document.getElementById('strava-input-distance').value) || 0.0,
                    moving_time: parseInt(document.getElementById('strava-input-duration').value) || 0,
                    total_elevation_gain: parseFloat(document.getElementById('strava-input-elevation').value) || 0.0,
                    average_heartrate: document.getElementById('strava-input-avg-hr').value !== "" ? parseFloat(document.getElementById('strava-input-avg-hr').value) : null,
                    max_heartrate: document.getElementById('strava-input-max-hr').value !== "" ? parseFloat(document.getElementById('strava-input-max-hr').value) : null,
                    calories: document.getElementById('strava-input-calories').value !== "" ? parseFloat(document.getElementById('strava-input-calories').value) : null
                };
                
                self.writeLog(`SAVING STRAVA LOG FOR ${dateInput}`, "sys");
                try {
                    const res = await fetch('/api/strava/data', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    const resData = await res.json();
                    if (res.ok && resData.status === 'success') {
                        self.writeLog("STRAVA LOG SECURED IN DATABASE", "sys");
                        soundSynth.playNotify();
                        self.loadStravaDashboardUI();
                        
                        // Clear form input fields except date
                        document.getElementById('strava-input-name').value = '';
                        document.getElementById('strava-input-distance').value = '';
                        document.getElementById('strava-input-duration').value = '';
                        document.getElementById('strava-input-elevation').value = '';
                        document.getElementById('strava-input-avg-hr').value = '';
                        document.getElementById('strava-input-max-hr').value = '';
                        document.getElementById('strava-input-calories').value = '';
                    } else {
                        throw new Error(resData.message || "Unknown error");
                    }
                } catch (err) {
                    self.writeLog(`STRAVA SAVE ERROR: ${err.message}`, "err");
                    soundSynth.playError();
                }
            });
        }

        // Sync Strava device simulation from dashboard
        const btnSyncStravaDashboard = document.getElementById('btn-sync-strava-dashboard');
        if (btnSyncStravaDashboard) {
            btnSyncStravaDashboard.addEventListener('click', async () => {
                soundSynth.playClick();
                self.writeLog("INITIATING STRAVA SYNCHRONIZATION", "sys");
                try {
                    const res = await fetch('/api/strava/sync');
                    const resData = await res.json();
                    if (res.ok && resData.status === 'syncing') {
                        self.pollSyncStatus('strava');
                    } else {
                        throw new Error(resData.detail || resData.message || "Sync error");
                    }
                } catch (err) {
                    self.writeLog(`STRAVA SYNC ERROR: ${err.message}`, "err");
                    soundSynth.playError();
                }
            });
        }

        // Toggle Withings API config password visibility
        const btnToggleWithingsSecret = document.getElementById('btn-toggle-withings-secret');
        const inputWithingsSecret = document.getElementById('input-withings-client-secret');
        if (btnToggleWithingsSecret && inputWithingsSecret) {
            btnToggleWithingsSecret.addEventListener('click', () => {
                soundSynth.playClick();
                if (inputWithingsSecret.type === 'password') {
                    inputWithingsSecret.type = 'text';
                    btnToggleWithingsSecret.innerHTML = '<i class="fa-solid fa-eye-slash"></i>';
                } else {
                    inputWithingsSecret.type = 'password';
                    btnToggleWithingsSecret.innerHTML = '<i class="fa-solid fa-eye"></i>';
                }
            });
        }

        const btnToggleWithingsToken = document.getElementById('btn-toggle-withings-token');
        const inputWithingsToken = document.getElementById('input-withings-refresh-token');
        if (btnToggleWithingsToken && inputWithingsToken) {
            btnToggleWithingsToken.addEventListener('click', () => {
                soundSynth.playClick();
                if (inputWithingsToken.type === 'password') {
                    inputWithingsToken.type = 'text';
                    btnToggleWithingsToken.innerHTML = '<i class="fa-solid fa-eye-slash"></i>';
                } else {
                    inputWithingsToken.type = 'password';
                    btnToggleWithingsToken.innerHTML = '<i class="fa-solid fa-eye"></i>';
                }
            });
        }

        // Toggle Withings Fit Dashboard Modal
        const btnWithings = document.getElementById('btn-withings');
        const modalWithings = document.getElementById('modal-withings');
        const btnCloseWithings = document.getElementById('btn-close-withings');
        
        if (btnWithings && modalWithings && btnCloseWithings) {
            btnWithings.addEventListener('click', () => {
                soundSynth.playClick();
                modalWithings.classList.add('active');
                
                // Pre-fill today's date if empty
                const dateField = document.getElementById('withings-input-date');
                if (dateField && !dateField.value) {
                    const today = new Date().toISOString().substring(0, 10);
                    dateField.value = today;
                }
                
                self.loadWithingsDashboardUI();
            });
            
            btnCloseWithings.addEventListener('click', () => {
                soundSynth.playClick();
                modalWithings.classList.remove('active');
            });
        }

        // Save manual Withings entry
        const btnSaveWithingsManual = document.getElementById('btn-save-withings-manual');
        if (btnSaveWithingsManual) {
            btnSaveWithingsManual.addEventListener('click', async () => {
                const dateInput = document.getElementById('withings-input-date').value;
                if (!dateInput) {
                    self.writeLog("WITHINGS DATA FAILURE: DATUM SAKNAS", "err");
                    soundSynth.playError();
                    alert("Ange ett giltigt datum.");
                    return;
                }
                soundSynth.playClick();
                const payload = {
                    date: dateInput,
                    weight: document.getElementById('withings-input-weight').value !== "" ? parseFloat(document.getElementById('withings-input-weight').value) : null,
                    fat_ratio: document.getElementById('withings-input-fat').value !== "" ? parseFloat(document.getElementById('withings-input-fat').value) : null,
                    bone_mass: document.getElementById('withings-input-bone').value !== "" ? parseFloat(document.getElementById('withings-input-bone').value) : null,
                    heart_pulse: document.getElementById('withings-input-pulse').value !== "" ? parseFloat(document.getElementById('withings-input-pulse').value) : null
                };
                
                self.writeLog(`SAVING WITHINGS LOG FOR ${dateInput}`, "sys");
                try {
                    const res = await fetch('/api/withings/data', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    const resData = await res.json();
                    if (res.ok && resData.status === 'success') {
                        self.writeLog("WITHINGS LOG SECURED IN DATABASE", "sys");
                        soundSynth.playNotify();
                        self.loadWithingsDashboardUI();
                        
                        // Clear form input fields except date
                        document.getElementById('withings-input-weight').value = '';
                        document.getElementById('withings-input-fat').value = '';
                        document.getElementById('withings-input-bone').value = '';
                        document.getElementById('withings-input-pulse').value = '';
                    } else {
                        throw new Error(resData.message || "Unknown error");
                    }
                } catch (err) {
                    self.writeLog(`WITHINGS SAVE ERROR: ${err.message}`, "err");
                    soundSynth.playError();
                }
            });
        }

        // Sync Withings device simulation from dashboard
        const btnSyncWithingsDashboard = document.getElementById('btn-sync-withings-dashboard');
        if (btnSyncWithingsDashboard) {
            btnSyncWithingsDashboard.addEventListener('click', async () => {
                soundSynth.playClick();
                self.writeLog("INITIATING WITHINGS SYNCHRONIZATION", "sys");
                try {
                    const res = await fetch('/api/withings/sync');
                    const resData = await res.json();
                    if (res.ok && resData.status === 'syncing') {
                        self.pollSyncStatus('withings');
                    } else {
                        throw new Error(resData.detail || resData.message || "Sync error");
                    }
                } catch (err) {
                    self.writeLog(`WITHINGS SYNC ERROR: ${err.message}`, "err");
                    soundSynth.playError();
                }
            });
        }

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
        btnSaveSettings.addEventListener('click', async () => {
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

            const chkSearch = document.getElementById('chk-tool-google_search');
            if (chkSearch) {
                localStorage.setItem("freja_tool_google_search_allowed", chkSearch.checked);
            }

            const chkGarmin = document.getElementById('chk-tool-get_garmin_health');
            if (chkGarmin) {
                const isAllowed = chkGarmin.checked;
                localStorage.setItem("freja_tool_get_garmin_health_allowed", isAllowed);
                
                const capGarmin = document.getElementById('cap-garmin');
                if (capGarmin) {
                    if (isAllowed) {
                        capGarmin.classList.add('active');
                    } else {
                        capGarmin.classList.remove('active');
                    }
                }
            }

            const chkStrava = document.getElementById('chk-tool-get_strava_data');
            const chkStravaAnalysis = document.getElementById('chk-tool-get_strava_activity_analysis');
            const chkStravaStats = document.getElementById('chk-tool-get_strava_athlete_stats');
            
            let anyStravaAllowed = false;
            
            if (chkStrava) {
                const isAllowed = chkStrava.checked;
                localStorage.setItem("freja_tool_get_strava_data_allowed", isAllowed);
                if (isAllowed) anyStravaAllowed = true;
            }
            if (chkStravaAnalysis) {
                const isAllowed = chkStravaAnalysis.checked;
                localStorage.setItem("freja_tool_get_strava_activity_analysis_allowed", isAllowed);
                if (isAllowed) anyStravaAllowed = true;
            }
            if (chkStravaStats) {
                const isAllowed = chkStravaStats.checked;
                localStorage.setItem("freja_tool_get_strava_athlete_stats_allowed", isAllowed);
                if (isAllowed) anyStravaAllowed = true;
            }
            
            const capStrava = document.getElementById('cap-strava');
            if (capStrava) {
                if (anyStravaAllowed) {
                    capStrava.classList.add('active');
                } else {
                    capStrava.classList.remove('active');
                }
            }

            const chkWithings = document.getElementById('chk-tool-get_withings_health');
            if (chkWithings) {
                const isAllowed = chkWithings.checked;
                localStorage.setItem("freja_tool_get_withings_health_allowed", isAllowed);
                
                const capWithings = document.getElementById('cap-withings');
                if (capWithings) {
                    if (isAllowed) {
                        capWithings.classList.add('active');
                    } else {
                        capWithings.classList.remove('active');
                    }
                }
            }

            const garminEmail = document.getElementById('input-garmin-email').value.trim();
            const garminPassword = document.getElementById('input-garmin-password').value;
            localStorage.setItem("freja_garmin_email", garminEmail);
            localStorage.setItem("freja_garmin_password", garminPassword);

            const stravaClientId = document.getElementById('input-strava-client-id').value.trim();
            const stravaClientSecret = document.getElementById('input-strava-client-secret').value;
            const stravaRefreshToken = document.getElementById('input-strava-refresh-token').value;
            localStorage.setItem("freja_strava_client_id", stravaClientId);
            localStorage.setItem("freja_strava_client_secret", stravaClientSecret);
            localStorage.setItem("freja_strava_refresh_token", stravaRefreshToken);

            const withingsClientId = document.getElementById('input-withings-client-id').value.trim();
            const withingsClientSecret = document.getElementById('input-withings-client-secret').value;
            const withingsRefreshToken = document.getElementById('input-withings-refresh-token').value;
            localStorage.setItem("freja_withings_client_id", withingsClientId);
            localStorage.setItem("freja_withings_client_secret", withingsClientSecret);
            localStorage.setItem("freja_withings_refresh_token", withingsRefreshToken);

            // Save keys to secure SQLite database
            await self.saveKeysToServer({
                freja_gemini_apikey: apiKey,
                freja_eleven_apikey: elevenKey,
                freja_mem0_apikey: mem0Key,
                freja_garmin_email: garminEmail,
                freja_garmin_password: garminPassword,
                freja_strava_client_id: stravaClientId,
                freja_strava_client_secret: stravaClientSecret,
                freja_strava_refresh_token: stravaRefreshToken,
                freja_withings_client_id: withingsClientId,
                freja_withings_client_secret: withingsClientSecret,
                freja_withings_refresh_token: withingsRefreshToken
            });

            modalSettings.classList.remove('active');
            self.writeLog("INTERFACE NETWORK CONFIGURATIONS SECURED", "sys");
            soundSynth.playNotify();
        });

        // Reset Settings button triggers
        const btnResetSettings = document.getElementById('btn-reset-settings');
        btnResetSettings.addEventListener('click', async () => {
            soundSynth.playError();
            if (confirm("Vill du återställa alla inställningar till grundutförande?")) {
                localStorage.clear();
                self.gemini.clearHistory();
                try {
                    await fetch('/api/keys', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            freja_gemini_apikey: "",
                            freja_eleven_apikey: "",
                            freja_mem0_apikey: ""
                        })
                    });
                } catch (e) {
                    console.error("Failed to reset keys on server:", e);
                }
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

        // Toggle Telegram Dashboard Modal
        const btnTelegram = document.getElementById('btn-telegram');
        const modalTelegram = document.getElementById('modal-telegram');
        const btnCloseTelegram = document.getElementById('btn-close-telegram');
        
        if (btnTelegram && modalTelegram && btnCloseTelegram) {
            btnTelegram.addEventListener('click', () => {
                soundSynth.playClick();
                modalTelegram.classList.add('active');
                self.loadTelegramDashboardUI();
            });
            
            btnCloseTelegram.addEventListener('click', () => {
                soundSynth.playClick();
                modalTelegram.classList.remove('active');
            });
        }

        const btnSaveTelegram = document.getElementById('btn-save-telegram');
        if (btnSaveTelegram) {
            btnSaveTelegram.addEventListener('click', async () => {
                soundSynth.playClick();
                const token = document.getElementById('telegram-input-token').value.trim();
                const chatId = document.getElementById('telegram-input-chat-id').value.trim();
                
                self.writeLog("SAVING TELEGRAM CONFIGURATIONS...", "sys");
                try {
                    const res = await fetch('/api/telegram/config', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ token: token, chat_id: chatId })
                    });
                    const resData = await res.json();
                    if (res.ok && resData.status === 'success') {
                        self.writeLog("TELEGRAM CONFIGURATIONS SECURED SUCCESS", "sys");
                        soundSynth.playNotify();
                        self.loadTelegramDashboardUI();
                    } else {
                        throw new Error(resData.detail || "Save error");
                    }
                } catch (err) {
                    self.writeLog(`TELEGRAM SAVE ERROR: ${err.message}`, "err");
                    soundSynth.playError();
                    alert("Kunde inte spara inställningarna: " + err.message);
                }
            });
        }

        const btnRefreshTelegram = document.getElementById('btn-refresh-telegram');
        if (btnRefreshTelegram) {
            btnRefreshTelegram.addEventListener('click', () => {
                soundSynth.playClick();
                self.loadTelegramDashboardUI();
            });
        }
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
     * Retrieves status and message activity for the Telegram bot interface.
     */
    async loadTelegramDashboardUI() {
        const tokenInput = document.getElementById('telegram-input-token');
        const chatIdInput = document.getElementById('telegram-input-chat-id');
        const botStatus = document.getElementById('telegram-bot-status');
        const telegramList = document.getElementById('telegram-list');
        
        if (!telegramList) return;
        
        try {
            const res = await fetch('/api/telegram/status');
            if (!res.ok) throw new Error(`HTTP error ${res.status}`);
            
            const status = await res.json();
            
            // Set inputs if they aren't active/focused
            if (tokenInput && document.activeElement !== tokenInput) {
                tokenInput.value = status.token_masked || "";
            }
            if (chatIdInput && document.activeElement !== chatIdInput) {
                chatIdInput.value = status.chat_id || "";
            }
            
            // Set status label
            if (botStatus) {
                if (status.is_active) {
                    botStatus.textContent = "AKTIV (AVLYSSNAR)";
                    botStatus.style.color = "var(--color-primary)";
                } else {
                    botStatus.textContent = "INAKTIV (NYCKLAR SAKNAS)";
                    botStatus.style.color = "var(--color-error)";
                }
            }
            
            // Render logs
            if (!status.recent_messages || status.recent_messages.length === 0) {
                telegramList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[INGEN AKTIVITET LOGGAD]</div>';
                return;
            }
            
            telegramList.innerHTML = "";
            status.recent_messages.forEach(msg => {
                const line = document.createElement('div');
                line.className = "log-line";
                line.style.fontSize = "11px";
                line.style.marginBottom = "4px";
                line.style.fontFamily = "var(--font-mono)";
                
                const timeSpan = document.createElement('span');
                timeSpan.className = "log-time";
                timeSpan.textContent = msg.time + " ";
                
                const tagSpan = document.createElement('span');
                if (msg.authorized) {
                    tagSpan.className = "log-tag tag-sys";
                    tagSpan.textContent = "[TELEGRAM] ";
                } else {
                    tagSpan.className = "log-tag tag-err";
                    tagSpan.textContent = "[UNAUTH] ";
                }
                
                line.appendChild(timeSpan);
                line.appendChild(tagSpan);
                
                const textNode = document.createTextNode(`Chat ${msg.chat_id}: ${msg.text}`);
                line.appendChild(textNode);
                
                telegramList.appendChild(line);
            });
            
        } catch (e) {
            console.error("[TELEGRAM] UI load error:", e);
            telegramList.innerHTML = '<div style="color: #ff3b30; text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[FEL VID HÄMTNING AV STATUS]</div>';
        }
    }

    /**
     * Synchronizes and draws the Garmin logs list inside the Garmin Dashboard overlay.
     */
    async loadGarminDashboardUI() {
        const garminList = document.getElementById('garmin-list');
        if (!garminList) return;
        
        // Set date input default to today if empty
        const dateInput = document.getElementById('garmin-input-date');
        if (dateInput && !dateInput.value) {
            const today = new Date().toISOString().split('T')[0];
            dateInput.value = today;
        }

        garminList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">Laddar historik...</div>';
        
        try {
            const res = await fetch('/api/garmin/data?days=10');
            if (!res.ok) throw new Error(`HTTP error ${res.status}`);
            
            const logs = await res.json();
            if (logs.length === 0) {
                garminList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[INGA HÄLSOLOGGAR HITTADE]</div>';
                return;
            }
            
            garminList.innerHTML = "";
            logs.forEach(log => {
                const item = document.createElement('div');
                item.className = "garmin-log-item";
                item.style.display = "flex";
                item.style.justifyContent = "space-between";
                item.style.alignItems = "center";
                item.style.padding = "8px";
                item.style.borderBottom = "1px solid rgba(0, 242, 254, 0.08)";
                item.style.fontSize = "11px";
                item.style.fontFamily = "var(--font-mono)";
                
                const workoutInfo = log.workout_type && log.workout_type !== "Ingen" 
                    ? ` | ${log.workout_type} (${log.workout_duration}m)` 
                    : "";
                const bbInfo = log.body_battery ? ` | BB: ${log.body_battery}` : "";
                const hrvInfo = log.hrv ? ` | HRV: ${log.hrv}ms` : "";
                
                item.innerHTML = `
                    <div style="flex: 1; color: var(--color-text-bright);">
                        <span style="color: var(--color-primary);">${log.date}</span>: ${log.steps} steg | ${log.sleep_hours}h sömn | ${log.resting_hr} puls | ${log.active_calories} kcal${workoutInfo}${bbInfo}${hrvInfo}
                    </div>
                    <button class="garmin-delete-btn" data-date="${log.date}" title="Radera logg" style="background: transparent; border: none; color: #ff3b30; cursor: pointer; padding: 2px 6px;">
                        <i class="fa-solid fa-trash-can"></i>
                    </button>
                `;
                
                const delBtn = item.querySelector('.garmin-delete-btn');
                delBtn.addEventListener('click', async () => {
                    soundSynth.playClick();
                    const dateVal = delBtn.getAttribute('data-date');
                    item.style.opacity = '0.5';
                    try {
                        const delRes = await fetch(`/api/garmin/delete?date=${dateVal}`);
                        const delData = await delRes.json();
                        if (delRes.ok && delData.status === 'success') {
                            this.writeLog(`GARMIN LOG FOR ${dateVal} PURGED`, "sys");
                            item.remove();
                            if (garminList.children.length === 0) {
                                garminList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[INGA HÄLSOLOGGAR HITTADE]</div>';
                            }
                        } else {
                            throw new Error(delData.message || "Failed deleting");
                        }
                    } catch (err) {
                        item.style.opacity = '1';
                        this.writeLog(`GARMIN DELETE ERROR: ${err.message}`, "err");
                        soundSynth.playError();
                    }
                });
                
                garminList.appendChild(item);
            });
        } catch (err) {
            console.error("[GARMIN] UI load error:", err);
            garminList.innerHTML = '<div style="color: #ff3b30; text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[FEL VID LADDNING AV HISTORIK]</div>';
        }
    }

    /**
     * Synchronizes and draws the Strava activities list inside the Strava Dashboard overlay.
     */
    async loadStravaDashboardUI() {
        const stravaList = document.getElementById('strava-list');
        if (!stravaList) return;
        
        // Set date input default to today if empty
        const dateInput = document.getElementById('strava-input-date');
        if (dateInput && !dateInput.value) {
            const today = new Date().toISOString().split('T')[0];
            dateInput.value = today;
        }

        stravaList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">Laddar historik...</div>';
        
        try {
            const res = await fetch('/api/strava/data?days=15');
            if (!res.ok) throw new Error(`HTTP error ${res.status}`);
            
            const logs = await res.json();
            if (logs.length === 0) {
                stravaList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[INGA TRÄNINGSPASS HITTADE]</div>';
                return;
            }
            
            stravaList.innerHTML = "";
            logs.forEach(log => {
                const item = document.createElement('div');
                item.className = "strava-log-item";
                item.style.display = "flex";
                item.style.justifyContent = "space-between";
                item.style.alignItems = "center";
                item.style.padding = "8px";
                item.style.borderBottom = "1px solid rgba(0, 242, 254, 0.08)";
                item.style.fontSize = "11px";
                item.style.fontFamily = "var(--font-mono)";
                
                const km = log.distance ? (log.distance / 1000).toFixed(2) + " km" : "0 km";
                const mins = log.moving_time ? Math.round(log.moving_time / 60) + " min" : "0 min";
                const hrInfo = log.average_heartrate ? ` | snittpuls: ${Math.round(log.average_heartrate)}` : "";
                const calInfo = log.calories ? ` | ${Math.round(log.calories)} kcal` : "";
                const elevInfo = log.total_elevation_gain ? ` | +${Math.round(log.total_elevation_gain)}m` : "";
                const speedInfo = log.formatted_speed ? ` | ${log.formatted_speed}` : "";
                
                item.innerHTML = `
                    <div style="flex: 1; color: var(--color-text-bright);">
                        <span style="color: var(--color-primary);">${log.date}</span>: <strong style="color: var(--color-accent);">${log.type}</strong> - ${log.name} (${km} | ${mins}${speedInfo}${elevInfo}${hrInfo}${calInfo})
                    </div>
                    <button class="strava-delete-btn" data-id="${log.id}" title="Radera aktivitet" style="background: transparent; border: none; color: #ff3b30; cursor: pointer; padding: 2px 6px;">
                        <i class="fa-solid fa-trash-can"></i>
                    </button>
                `;
                
                const delBtn = item.querySelector('.strava-delete-btn');
                delBtn.addEventListener('click', async () => {
                    soundSynth.playClick();
                    const idVal = delBtn.getAttribute('data-id');
                    item.style.opacity = '0.5';
                    try {
                        const delRes = await fetch(`/api/strava/delete?id=${idVal}`);
                        const delData = await delRes.json();
                        if (delRes.ok && delData.status === 'success') {
                            this.writeLog(`STRAVA LOG FOR ID ${idVal} PURGED`, "sys");
                            item.remove();
                            if (stravaList.children.length === 0) {
                                stravaList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[INGA TRÄNINGSPASS HITTADE]</div>';
                            }
                        } else {
                            throw new Error(delData.message || "Failed deleting");
                        }
                    } catch (err) {
                        item.style.opacity = '1';
                        this.writeLog(`STRAVA DELETE ERROR: ${err.message}`, "err");
                        soundSynth.playError();
                    }
                });
                
                stravaList.appendChild(item);
            });
        } catch (err) {
            console.error("[STRAVA] UI load error:", err);
            stravaList.innerHTML = '<div style="color: #ff3b30; text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[FEL VID LADDNING AV HISTORIK]</div>';
        }
    }

    /**
     * Synchronizes and draws the Withings measurements list inside the Withings Dashboard overlay.
     */
    async loadWithingsDashboardUI() {
        const withingsList = document.getElementById('withings-list');
        if (!withingsList) return;
        
        // Set date input default to today if empty
        const dateInput = document.getElementById('withings-input-date');
        if (dateInput && !dateInput.value) {
            const today = new Date().toISOString().split('T')[0];
            dateInput.value = today;
        }

        withingsList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">Laddar mätningar...</div>';
        
        try {
            const res = await fetch('/api/withings/data?days=15');
            if (!res.ok) throw new Error(`HTTP error ${res.status}`);
            
            const logs = await res.json();
            if (logs.length === 0) {
                withingsList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[INGA MÄTNINGAR HITTADE]</div>';
                return;
            }
            
            withingsList.innerHTML = "";
            logs.forEach(log => {
                const item = document.createElement('div');
                item.className = "withings-log-item";
                item.style.display = "flex";
                item.style.justifyContent = "space-between";
                item.style.alignItems = "center";
                item.style.padding = "8px";
                item.style.borderBottom = "1px solid rgba(0, 242, 254, 0.08)";
                item.style.fontSize = "11px";
                item.style.fontFamily = "var(--font-mono)";
                
                const weight = log.weight ? `${log.weight} kg` : "N/A";
                const fat = log.fat_ratio ? ` | fett: ${log.fat_ratio}%` : "";
                const bone = log.bone_mass ? ` | benmassa: ${log.bone_mass} kg` : "";
                const pulse = log.heart_pulse ? ` | puls: ${log.heart_pulse} BPM` : "";
                
                item.innerHTML = `
                    <div style="flex: 1; color: var(--color-text-bright);">
                        <span style="color: var(--color-primary);">${log.date}</span>: <strong style="color: var(--color-accent);">Mätning</strong> - ${weight}${fat}${bone}${pulse}
                    </div>
                    <button class="withings-delete-btn" data-date="${log.date}" title="Radera mätning" style="background: transparent; border: none; color: #ff3b30; cursor: pointer; padding: 2px 6px;">
                        <i class="fa-solid fa-trash-can"></i>
                    </button>
                `;
                
                const delBtn = item.querySelector('.withings-delete-btn');
                delBtn.addEventListener('click', async () => {
                    soundSynth.playClick();
                    const dateVal = delBtn.getAttribute('data-date');
                    item.style.opacity = '0.5';
                    try {
                        const delRes = await fetch(`/api/withings/delete?date=${dateVal}`);
                        const delData = await delRes.json();
                        if (delRes.ok && delData.status === 'success') {
                            this.writeLog(`WITHINGS LOG FOR DATE ${dateVal} PURGED`, "sys");
                            item.remove();
                            if (withingsList.children.length === 0) {
                                withingsList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[INGA MÄTNINGAR HITTADE]</div>';
                            }
                        } else {
                            throw new Error(delData.message || "Failed deleting");
                        }
                    } catch (err) {
                        item.style.opacity = '1';
                        this.writeLog(`WITHINGS DELETE ERROR: ${err.message}`, "err");
                        soundSynth.playError();
                    }
                });
                
                withingsList.appendChild(item);
            });
        } catch (err) {
            console.error("[WITHINGS] UI load error:", err);
            withingsList.innerHTML = '<div style="color: #ff3b30; text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[FEL VID LADDNING AV HISTORIK]</div>';
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
                    history.forEach(msg => {
                        this.appendChatMessage(msg.sender, msg.content, false);
                    });
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
        
        msgDiv.innerHTML = `
            <div class="msg-sender">${senderTag}</div>
            <div class="msg-content">${formattedText}</div>
        `;
        
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
            
            // Sync capability badge if garmin health
            if (tool.name === 'get_garmin_health') {
                const capGarmin = document.getElementById('cap-garmin');
                if (capGarmin) capGarmin.classList.add('active');
            }
            
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
