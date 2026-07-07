/**
 * F.R.E.J.A. UI Controller - Event Bindings Module
 */
FrejaUIController.prototype.bindEvents = function() {
    const self = this;

    const shield = document.getElementById('interaction-shield');
    const initAudioBtn = document.getElementById('btn-initialize-audio');

    const removeShield = () => {
        try {
            soundSynth.init();
            soundSynth.playStartupSweep();
        } catch (e) {
            console.warn("[AUDIO] Sound synth init error:", e);
        }

        if (shield) {
            shield.classList.add('fade-out');
            shield.style.pointerEvents = 'none';
            setTimeout(() => { shield.style.display = 'none'; }, 500);
        }
        
        self.writeLog("COGNITIVE SERVICES LINKING ACTIVE", "sys");
        self.writeLog("SPEECH RECOGNITION ONLINE [SV-SE]", "sys");
        
        // Populate camera selection inputs dropdown
        try {
            self.loadCameraDevices();
        } catch (e) {
            console.warn("[CAMERA] Device load warning:", e);
        }
        
        // Initiate canvas visualizer animations
        try {
            if (typeof ArcReactorVisualizer !== 'undefined') {
                window.visualizer = new ArcReactorVisualizer('arc-canvas');
                window.visualizer.setThemeHue(self.getCurrentThemeHue());
                window.visualizer.startAnimation();
            }
        } catch (e) {
            console.warn("[VISUALIZER] Canvas init warning:", e);
        }
        
        // Ask permission for microphone in background
        try {
            soundSynth.getMicrophoneStream().then(() => {
                self.writeLog("MICROPHONE CORE ACQUIRED. DYNAMIC EQUALIZER LINKED", "sys");
            }).catch(() => {
                self.writeLog("MICROPHONE DENIED. RUNNING SPEECH VIA MANUAL WRITING", "warn");
            });
        } catch (e) {
            console.warn("[AUDIO] Mic stream request error:", e);
        }

        // Greet User post boot-up sequence
        setTimeout(() => {
            const langEl = document.getElementById('select-lang-quick');
            const sv = langEl ? langEl.value === 'sv-SE' : true;

            if (self.hasLoadedHistory) {
                const resumeMsg = sv
                    ? "Välkommen tillbaka. Chattsession återupptagen."
                    : "Welcome back. Chat session resumed.";
                self.speech.speak(resumeMsg);
                self.writeLog("SESSION RESUMED. CHAT HISTORY SYNCHRONIZED.", "sys");
                return;
            }

            const startMsg = sv 
                ? "System aktiverat. Alla nätverksprotokoll online. Hur kan jag hjälpa dig idag?"
                : "Systems fully engaged. AI diagnostic matrix secure. How may I assist you today?";

            
            self.appendChatMessage("assistant", startMsg);
            self.speech.speak(startMsg);
        }, 1000);
    };
    
    if (initAudioBtn) {
        initAudioBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            removeShield();
        });
    }
    if (shield) {
        shield.addEventListener('click', (e) => {
            removeShield();
        });
    }
    
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
                self.hasLoadedHistory = false;
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

    // Toggle Access Token visibility mask
    const btnToggleAccessToken = document.getElementById('btn-toggle-access-token');
    const inputAccessTokenField = document.getElementById('input-access-token');
    if (btnToggleAccessToken && inputAccessTokenField) {
        btnToggleAccessToken.addEventListener('click', () => {
            soundSynth.playClick();
            if (inputAccessTokenField.type === 'password') {
                inputAccessTokenField.type = 'text';
                btnToggleAccessToken.innerHTML = '<i class="fa-solid fa-eye-slash"></i>';
            } else {
                inputAccessTokenField.type = 'password';
                btnToggleAccessToken.innerHTML = '<i class="fa-solid fa-eye"></i>';
            }
        });
    }

    // Login Submit action for auth overlay
    const btnSubmitLogin = document.getElementById('btn-submit-login');
    const inputLoginToken = document.getElementById('input-login-token');
    const loginErrorMsg = document.getElementById('login-error-msg');
    const modalAuthLogin = document.getElementById('modal-auth-login');
    if (btnSubmitLogin && inputLoginToken && modalAuthLogin) {
        btnSubmitLogin.addEventListener('click', async () => {
            soundSynth.playClick();
            const token = inputLoginToken.value.trim();
            if (!token) return;
            
            // Test connection with token
            localStorage.setItem('freja_access_token', token);
            try {
                const res = await window.originalFetch('/api/keys', {
                    headers: { 'X-Freja-Token': token }
                });
                if (res.ok) {
                    modalAuthLogin.classList.remove('active');
                    if (loginErrorMsg) loginErrorMsg.style.display = 'none';
                    self.writeLog("ACCESS TOKEN GRANTED. SESSION RE-ESTABLISHED.", "sys");
                    soundSynth.playNotify();
                    // Reload keys
                    await self.loadKeysFromServer();
                    self.initializeUI();
                } else {
                    localStorage.removeItem('freja_access_token');
                    if (loginErrorMsg) loginErrorMsg.style.display = 'block';
                    soundSynth.playError();
                }
            } catch (err) {
                localStorage.removeItem('freja_access_token');
                if (loginErrorMsg) loginErrorMsg.style.display = 'block';
                soundSynth.playError();
            }
        });
    }

    // Toggle Login Token visibility mask
    const btnToggleLoginToken = document.getElementById('btn-toggle-login-token');
    if (btnToggleLoginToken && inputLoginToken) {
        btnToggleLoginToken.addEventListener('click', () => {
            soundSynth.playClick();
            if (inputLoginToken.type === 'password') {
                inputLoginToken.type = 'text';
                btnToggleLoginToken.innerHTML = '<i class="fa-solid fa-eye-slash"></i>';
            } else {
                inputLoginToken.type = 'password';
                btnToggleLoginToken.innerHTML = '<i class="fa-solid fa-eye"></i>';
            }
        });
    }

    // Toggle API Keys visibility masks
    const btnToggleKey = document.getElementById('btn-toggle-key');
    const inputApiKey = document.getElementById('input-api-key');
    if (btnToggleKey && inputApiKey) {
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
    }

    const btnToggleElevenKey = document.getElementById('btn-toggle-eleven-key');
    const inputElevenKey = document.getElementById('input-eleven-key');
    if (btnToggleElevenKey && inputElevenKey) {
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
    }

    const btnToggleMem0Key = document.getElementById('btn-toggle-mem0-key');
    const inputMem0Key = document.getElementById('input-mem0-key');
    if (btnToggleMem0Key && inputMem0Key) {
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
    }

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
    
    if (btnMemory && modalMemory) {
        btnMemory.addEventListener('click', () => {
            soundSynth.playClick();
            modalMemory.classList.add('active');
            self.loadMemoryVaultUI();
        });
    }
    
    if (btnCloseMemory && modalMemory) {
        btnCloseMemory.addEventListener('click', () => {
            soundSynth.playClick();
            modalMemory.classList.remove('active');
        });
    }


    // Save Memory API Settings from Memory modal
    const btnSaveMemoryApi = document.getElementById('btn-save-memory-api');
    if (btnSaveMemoryApi) {
        btnSaveMemoryApi.addEventListener('click', async () => {
            soundSynth.playClick();
            
            const mem0Key = document.getElementById('input-mem0-key').value.trim();
            const mem0Enabled = document.getElementById('chk-use-mem0').checked;
            
            self.memory.saveSettings(mem0Key, mem0Enabled);
            
            // Save keys to secure SQLite database
            await self.saveKeysToServer({
                freja_mem0_apikey: mem0Key
            });
            
            self.writeLog("NEURAL MEMORY CONFIGURATION SECURED", "sys");
            soundSynth.playNotify();
        });
    }

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

    const btnSyncGarminAll = document.getElementById('btn-sync-garmin-all');
    if (btnSyncGarminAll) {
        btnSyncGarminAll.addEventListener('click', async () => {
            soundSynth.playClick();
            self.writeLog("INITIATING GARMIN HISTORICAL SYNCHRONIZATION (180 DAYS)", "sys");
            try {
                const res = await fetch('/api/garmin/sync?days=180');
                const resData = await res.json();
                if (res.ok && resData.status === 'syncing') {
                    self.pollSyncStatus('garmin');
                } else {
                    throw new Error(resData.detail || resData.message || "Sync error");
                }
            } catch (err) {
                self.writeLog(`GARMIN HISTORICAL SYNC ERROR: ${err.message}`, "err");
                soundSynth.playError();
            }
        });
    }

    // Save Garmin account/permission settings from Garmin modal
    const btnSaveGarminApi = document.getElementById('btn-save-garmin-api');
    if (btnSaveGarminApi) {
        btnSaveGarminApi.addEventListener('click', async () => {
            soundSynth.playClick();
            
            const garminEmail = document.getElementById('input-garmin-email').value.trim();
            const garminPassword = document.getElementById('input-garmin-password').value;
            
            localStorage.setItem("freja_garmin_email", garminEmail);
            localStorage.setItem("freja_garmin_password", garminPassword);
            
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
            
            // Save keys to secure SQLite database (permission flag included so the
            // backend, the authoritative enforcement point, sees the same grant).
            await self.saveKeysToServer({
                freja_garmin_email: garminEmail,
                freja_garmin_password: garminPassword,
                freja_tool_get_garmin_health_allowed: String(chkGarmin ? chkGarmin.checked : false)
            });
            
            self.writeLog("GARMIN CONNECT CONFIGURATION SECURED", "sys");
            soundSynth.playNotify();
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

    const btnSyncStravaAll = document.getElementById('btn-sync-strava-all');
    if (btnSyncStravaAll) {
        btnSyncStravaAll.addEventListener('click', async () => {
            soundSynth.playClick();
            self.writeLog("INITIATING STRAVA HISTORICAL SYNCHRONIZATION (365 DAYS)", "sys");
            try {
                const res = await fetch('/api/strava/sync?days=365');
                const resData = await res.json();
                if (res.ok && resData.status === 'syncing') {
                    self.pollSyncStatus('strava');
                } else {
                    throw new Error(resData.detail || resData.message || "Sync error");
                }
            } catch (err) {
                self.writeLog(`STRAVA HISTORICAL SYNC ERROR: ${err.message}`, "err");
                soundSynth.playError();
            }
        });
    }

    // Save Strava API settings from dashboard
    const btnSaveStravaApi = document.getElementById('btn-save-strava-api');
    if (btnSaveStravaApi) {
        btnSaveStravaApi.addEventListener('click', async () => {
            soundSynth.playClick();
            
            const stravaClientId = document.getElementById('input-strava-client-id').value.trim();
            const stravaClientSecret = document.getElementById('input-strava-client-secret').value;
            const stravaRefreshToken = document.getElementById('input-strava-refresh-token').value;
            
            localStorage.setItem("freja_strava_client_id", stravaClientId);
            localStorage.setItem("freja_strava_client_secret", stravaClientSecret);
            localStorage.setItem("freja_strava_refresh_token", stravaRefreshToken);
            
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
            
            // Save keys to secure SQLite database (permission flags included so the
            // backend, the authoritative enforcement point, sees the same grants).
            await self.saveKeysToServer({
                freja_strava_client_id: stravaClientId,
                freja_strava_client_secret: stravaClientSecret,
                freja_strava_refresh_token: stravaRefreshToken,
                freja_tool_get_strava_data_allowed: String(chkStrava ? chkStrava.checked : false),
                freja_tool_get_strava_activity_analysis_allowed: String(chkStravaAnalysis ? chkStravaAnalysis.checked : false),
                freja_tool_get_strava_athlete_stats_allowed: String(chkStravaStats ? chkStravaStats.checked : false)
            });
            
            self.writeLog("STRAVA API CONFIGURATION SECURED", "sys");
            soundSynth.playNotify();
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

    const btnSyncWithingsAll = document.getElementById('btn-sync-withings-all');
    if (btnSyncWithingsAll) {
        btnSyncWithingsAll.addEventListener('click', async () => {
            soundSynth.playClick();
            self.writeLog("INITIATING WITHINGS HISTORICAL SYNCHRONIZATION (365 DAYS)", "sys");
            try {
                const res = await fetch('/api/withings/sync?days=365');
                const resData = await res.json();
                if (res.ok && resData.status === 'syncing') {
                    self.pollSyncStatus('withings');
                } else {
                    throw new Error(resData.detail || resData.message || "Sync error");
                }
            } catch (err) {
                self.writeLog(`WITHINGS HISTORICAL SYNC ERROR: ${err.message}`, "err");
                soundSynth.playError();
            }
        });
    }

    // Save Withings API settings from Withings modal
    const btnSaveWithingsApi = document.getElementById('btn-save-withings-api');
    if (btnSaveWithingsApi) {
        btnSaveWithingsApi.addEventListener('click', async () => {
            soundSynth.playClick();
            
            const withingsClientId = document.getElementById('input-withings-client-id').value.trim();
            const withingsClientSecret = document.getElementById('input-withings-client-secret').value;
            const withingsRefreshToken = document.getElementById('input-withings-refresh-token').value;
            
            localStorage.setItem("freja_withings_client_id", withingsClientId);
            localStorage.setItem("freja_withings_client_secret", withingsClientSecret);
            localStorage.setItem("freja_withings_refresh_token", withingsRefreshToken);
            
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
            
            // Save keys to secure SQLite database (permission flag included so the
            // backend, the authoritative enforcement point, sees the same grant).
            await self.saveKeysToServer({
                freja_withings_client_id: withingsClientId,
                freja_withings_client_secret: withingsClientSecret,
                freja_withings_refresh_token: withingsRefreshToken,
                freja_tool_get_withings_health_allowed: String(chkWithings ? chkWithings.checked : false)
            });
            
            self.writeLog("WITHINGS API CONFIGURATION SECURED", "sys");
            soundSynth.playNotify();
        });
    }

    // Toggle Google Calendar API config password visibility
    const btnToggleGoogleSecret = document.getElementById('btn-toggle-google-calendar-secret');
    const inputGoogleSecret = document.getElementById('input-google-calendar-client-secret');
    if (btnToggleGoogleSecret && inputGoogleSecret) {
        btnToggleGoogleSecret.addEventListener('click', () => {
            soundSynth.playClick();
            if (inputGoogleSecret.type === 'password') {
                inputGoogleSecret.type = 'text';
                btnToggleGoogleSecret.innerHTML = '<i class="fa-solid fa-eye-slash"></i>';
            } else {
                inputGoogleSecret.type = 'password';
                btnToggleGoogleSecret.innerHTML = '<i class="fa-solid fa-eye"></i>';
            }
        });
    }

    const btnToggleGoogleToken = document.getElementById('btn-toggle-google-calendar-token');
    const inputGoogleToken = document.getElementById('input-google-calendar-refresh-token');
    if (btnToggleGoogleToken && inputGoogleToken) {
        btnToggleGoogleToken.addEventListener('click', () => {
            soundSynth.playClick();
            if (inputGoogleToken.type === 'password') {
                inputGoogleToken.type = 'text';
                btnToggleGoogleToken.innerHTML = '<i class="fa-solid fa-eye-slash"></i>';
            } else {
                inputGoogleToken.type = 'password';
                btnToggleGoogleToken.innerHTML = '<i class="fa-solid fa-eye"></i>';
            }
        });
    }

    // Save Google Calendar API settings from Google Calendar modal
    const btnSaveGoogleCalendarApi = document.getElementById('btn-save-google-calendar-api');
    if (btnSaveGoogleCalendarApi) {
        btnSaveGoogleCalendarApi.addEventListener('click', async () => {
            soundSynth.playClick();
            
            const googleClientId = document.getElementById('input-google-calendar-client-id').value.trim();
            const googleClientSecret = document.getElementById('input-google-calendar-client-secret').value;
            const googleRefreshToken = document.getElementById('input-google-calendar-refresh-token').value;
            
            localStorage.setItem("freja_google_calendar_client_id", googleClientId);
            localStorage.setItem("freja_google_calendar_client_secret", googleClientSecret);
            localStorage.setItem("freja_google_calendar_refresh_token", googleRefreshToken);
            
            const chkGoogleCalendar = document.getElementById('chk-tool-manage_google_calendar');
            if (chkGoogleCalendar) {
                const isAllowed = chkGoogleCalendar.checked;
                localStorage.setItem("freja_tool_manage_google_calendar_allowed", isAllowed);
                
                const capGoogleCalendar = document.getElementById('cap-google_calendar');
                if (capGoogleCalendar) {
                    if (isAllowed) {
                        capGoogleCalendar.classList.add('active');
                    } else {
                        capGoogleCalendar.classList.remove('active');
                    }
                }
            }
            
            // Save keys to secure SQLite database (permission flag included so the
            // backend, the authoritative enforcement point, sees the same grant).
            await self.saveKeysToServer({
                freja_google_calendar_client_id: googleClientId,
                freja_google_calendar_client_secret: googleClientSecret,
                freja_google_calendar_refresh_token: googleRefreshToken,
                freja_tool_manage_google_calendar_allowed: String(chkGoogleCalendar ? chkGoogleCalendar.checked : false)
            });
            
            self.writeLog("GOOGLE CALENDAR API CONFIGURATION SECURED", "sys");
            soundSynth.playNotify();
        });
    }

    // Toggle Google Calendar Dashboard Modal
    const btnGoogleCalendar = document.getElementById('btn-google-calendar');
    const modalGoogleCalendar = document.getElementById('modal-google-calendar');
    const btnCloseGoogleCalendar = document.getElementById('btn-close-google-calendar');
    
    if (btnGoogleCalendar && modalGoogleCalendar && btnCloseGoogleCalendar) {
        btnGoogleCalendar.addEventListener('click', () => {
            soundSynth.playClick();
            modalGoogleCalendar.classList.add('active');
            
            // Clear any leftover edit states in form on open
            document.getElementById('google-calendar-input-id').value = '';
            document.getElementById('google-calendar-input-summary').value = '';
            document.getElementById('google-calendar-input-description').value = '';
            document.getElementById('google-calendar-input-location').value = '';
            
            const now = new Date();
            const startISO = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
            document.getElementById('google-calendar-input-start').value = startISO;
            
            const nextHour = new Date(new Date().getTime() + 60 * 60 * 1000);
            const endISO = new Date(nextHour.getTime() - nextHour.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
            document.getElementById('google-calendar-input-end').value = endISO;

            const btnSave = document.getElementById('btn-save-google-calendar-manual');
            if (btnSave) btnSave.innerHTML = `<i class="fa-solid fa-floppy-disk"></i> SPARA HÄNDELSE`;
            
            const btnCancel = document.getElementById('btn-cancel-google-calendar-edit');
            if (btnCancel) btnCancel.style.display = "none";
            
            self.loadGoogleCalendarDashboardUI();
        });
        
        btnCloseGoogleCalendar.addEventListener('click', () => {
            soundSynth.playClick();
            modalGoogleCalendar.classList.remove('active');
        });
    }

    // Save manual Google Calendar entry
    const btnSaveGoogleCalendarManual = document.getElementById('btn-save-google-calendar-manual');
    if (btnSaveGoogleCalendarManual) {
        btnSaveGoogleCalendarManual.addEventListener('click', async () => {
            const summary = document.getElementById('google-calendar-input-summary').value.trim();
            const startTime = document.getElementById('google-calendar-input-start').value;
            const endTime = document.getElementById('google-calendar-input-end').value;
            
            if (!summary || !startTime || !endTime) {
                self.writeLog("CALENDAR FAILURE: TITEL OCH TIDER SAKNAS", "err");
                soundSynth.playError();
                alert("Titel, starttid och sluttid krävs.");
                return;
            }
            soundSynth.playClick();
            
            const eventId = document.getElementById('google-calendar-input-id').value;
            const payload = {
                summary: summary,
                start_time: startTime,
                end_time: endTime,
                description: document.getElementById('google-calendar-input-description').value.trim(),
                location: document.getElementById('google-calendar-input-location').value.trim()
            };
            
            if (eventId) {
                payload.id = parseInt(eventId);
            }
            
            self.writeLog(`SAVING CALENDAR EVENT: "${summary}"`, "sys");
            try {
                const res = await fetch('/api/google_calendar/data', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const resData = await res.json();
                if (res.ok && resData.status === 'success') {
                    self.writeLog("CALENDAR EVENT SECURED IN DATABASE", "sys");
                    soundSynth.playNotify();
                    self.loadGoogleCalendarDashboardUI();
                    
                    // Clear form input fields
                    document.getElementById('google-calendar-input-id').value = '';
                    document.getElementById('google-calendar-input-summary').value = '';
                    document.getElementById('google-calendar-input-description').value = '';
                    document.getElementById('google-calendar-input-location').value = '';
                    
                    const btnSave = document.getElementById('btn-save-google-calendar-manual');
                    if (btnSave) btnSave.innerHTML = `<i class="fa-solid fa-floppy-disk"></i> SPARA HÄNDELSE`;
                    
                    const btnCancel = document.getElementById('btn-cancel-google-calendar-edit');
                    if (btnCancel) btnCancel.style.display = "none";
                } else {
                    throw new Error(resData.message || "Unknown error");
                }
            } catch (err) {
                self.writeLog(`CALENDAR SAVE ERROR: ${err.message}`, "err");
                soundSynth.playError();
            }
        });
    }

    // Cancel Google Calendar edit operation
    const btnCancelGoogleCalendarEdit = document.getElementById('btn-cancel-google-calendar-edit');
    if (btnCancelGoogleCalendarEdit) {
        btnCancelGoogleCalendarEdit.addEventListener('click', () => {
            soundSynth.playClick();
            document.getElementById('google-calendar-input-id').value = '';
            document.getElementById('google-calendar-input-summary').value = '';
            document.getElementById('google-calendar-input-description').value = '';
            document.getElementById('google-calendar-input-location').value = '';
            
            const btnSave = document.getElementById('btn-save-google-calendar-manual');
            if (btnSave) btnSave.innerHTML = `<i class="fa-solid fa-floppy-disk"></i> SPARA HÄNDELSE`;
            
            btnCancelGoogleCalendarEdit.style.display = "none";
        });
    }

    // Sync Google Calendar from dashboard
    const btnSyncGoogleCalendarDashboard = document.getElementById('btn-sync-google_calendar-dashboard');
    if (btnSyncGoogleCalendarDashboard) {
        btnSyncGoogleCalendarDashboard.addEventListener('click', async () => {
            soundSynth.playClick();
            self.writeLog("INITIATING GOOGLE CALENDAR SYNCHRONIZATION", "sys");
            try {
                const res = await fetch('/api/google_calendar/sync');
                const resData = await res.json();
                if (res.ok && resData.status === 'syncing') {
                    self.pollSyncStatus('google_calendar');
                } else {
                    throw new Error(resData.detail || resData.message || "Sync error");
                }
            } catch (err) {
                self.writeLog(`CALENDAR SYNC ERROR: ${err.message}`, "err");
                soundSynth.playError();
            }
        });
    }

    // Listen to background sync updates from tool execution
    window.addEventListener('freja-calendar-updated', () => {
        self.loadGoogleCalendarDashboardUI();
    });

    // Insert new engram cards manually
    const btnAddMemoryManual = document.getElementById('btn-add-memory-manual');
    const inputNewMemory = document.getElementById('input-new-memory');
    
    if (btnAddMemoryManual && inputNewMemory) {
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
    }
    
    // Synchronize engrams list
    const btnRefreshMemory = document.getElementById('btn-refresh-memory');
    if (btnRefreshMemory) {
        btnRefreshMemory.addEventListener('click', () => {
            soundSynth.playClick();
            self.writeLog("SYNCHRONIZING NEURAL ENGRAMS", "sys");
            self.loadMemoryVaultUI();
        });
    }
    
    // Core Memory wipe-out button
    const btnWipeMemory = document.getElementById('btn-wipe-memory');
    if (btnWipeMemory) {
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
    }

    // Local Speech Synthesizer voice selectors changes
    const selectVoice = document.getElementById('select-voice');
    if (selectVoice) {
        selectVoice.addEventListener('change', () => {
            self.speech.voiceIndex = selectVoice.value ? parseInt(selectVoice.value) : null;
        });
    }

    // ElevenLabs voice dropdown selection triggers
    const selectElevenVoice = document.getElementById('select-eleven-voice');
    const groupElevenCustom = document.getElementById('group-eleven-custom');
    if (selectElevenVoice && groupElevenCustom) {
        selectElevenVoice.addEventListener('change', () => {
            if (selectElevenVoice.value === 'custom') {
                groupElevenCustom.style.display = 'block';
            } else {
                groupElevenCustom.style.display = 'none';
            }
        });
    }

    // Sliders change bindings
    const sliderRate = document.getElementById('slider-rate');
    if (sliderRate) {
        sliderRate.addEventListener('input', () => {
            document.getElementById('val-rate').textContent = sliderRate.value;
            self.speech.rate = parseFloat(sliderRate.value);
        });
    }

    const sliderPitch = document.getElementById('slider-pitch');
    if (sliderPitch) {
        sliderPitch.addEventListener('input', () => {
            document.getElementById('val-pitch').textContent = sliderPitch.value;
            self.speech.pitch = parseFloat(sliderPitch.value);
        });
    }

    // Save settings form actions

    const btnSaveSettings = document.getElementById('btn-save-settings');
    btnSaveSettings.addEventListener('click', async () => {
        soundSynth.playClick();
        
        const inputBackendUrl = document.getElementById('input-backend-url');
        const backendUrlVal = inputBackendUrl ? inputBackendUrl.value.trim() : "";
        if (backendUrlVal) {
            localStorage.setItem("freja_backend_url", backendUrlVal);
        }

        const inputAccessToken = document.getElementById('input-access-token');
        const accessTokenVal = inputAccessToken ? inputAccessToken.value.trim() : "";
        if (accessTokenVal) {
            localStorage.setItem("freja_access_token", accessTokenVal);
        }

        const sliderRate = document.getElementById('slider-rate');
        const sliderPitch = document.getElementById('slider-pitch');
        if (sliderRate) localStorage.setItem("freja_speech_rate", sliderRate.value);
        if (sliderPitch) localStorage.setItem("freja_speech_pitch", sliderPitch.value);
        
        const personaEl = document.getElementById('textarea-persona');
        if (personaEl) {
            localStorage.setItem("freja_speech_persona", personaEl.value);
            self.gemini.systemPrompt = personaEl.value;
        }

        const selectVoice = document.getElementById('select-voice');
        if (selectVoice && selectVoice.value) {
            localStorage.setItem("freja_speech_voiceidx", selectVoice.value);
        }

        const elevenVoiceEl = document.getElementById('select-eleven-voice');
        const elevenVoice = elevenVoiceEl ? elevenVoiceEl.value : "21m00Tcm4TlvDq8ikWAM";
        const elevenCustomVoiceEl = document.getElementById('input-eleven-custom-voice');
        const elevenCustomVoice = elevenCustomVoiceEl ? elevenCustomVoiceEl.value.trim() : "";

        localStorage.setItem("freja_eleven_voice", elevenVoice);
        self.speech.elevenVoice = elevenVoice;

        localStorage.setItem("freja_eleven_custom_voice", elevenCustomVoice);
        self.speech.elevenCustomVoice = elevenCustomVoice;

        // Save access token to secure SQLite database if provided
        if (accessTokenVal) {
            try {
                await self.saveKeysToServer({ freja_access_token: accessTokenVal });
            } catch (e) {
                console.error("Failed to save token to server:", e);
            }
        }

        self.writeLog("INTERFACE CONFIGURATIONS SECURED & SAVED", "sys");
        soundSynth.playNotify();

        const modalSettings = document.getElementById('modal-settings');
        if (modalSettings) modalSettings.classList.remove('active');
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

    // Toggle Trainer Dashboard Modal
    const btnTrainer = document.getElementById('btn-trainer');
    const modalTrainer = document.getElementById('modal-trainer');
    const btnCloseTrainer = document.getElementById('btn-close-trainer');
    
    if (btnTrainer && modalTrainer && btnCloseTrainer) {
        btnTrainer.addEventListener('click', () => {
            soundSynth.playClick();
            modalTrainer.classList.add('active');
            self.loadTrainerDashboardUI();
        });
        
        btnCloseTrainer.addEventListener('click', () => {
            soundSynth.playClick();
            modalTrainer.classList.remove('active');
        });
    }

    // Toggle Neural Learning Modal
    const btnLearning = document.getElementById('btn-learning');
    const modalLearning = document.getElementById('modal-learning');
    const btnCloseLearning = document.getElementById('btn-close-learning');
    
    if (btnLearning && modalLearning && btnCloseLearning) {
        btnLearning.addEventListener('click', () => {
            soundSynth.playClick();
            modalLearning.classList.add('active');
            self.loadLearningVaultUI();
            self.loadCredentialsUI();
        });
        
        btnCloseLearning.addEventListener('click', () => {
            soundSynth.playClick();
            modalLearning.classList.remove('active');
        });
    }

    // Trigger manual learning process
    const btnStartLearning = document.getElementById('btn-start-learning');
    if (btnStartLearning) {
        btnStartLearning.addEventListener('click', async () => {
            const topicInput = document.getElementById('learning-input-topic');
            const topic = topicInput.value.trim();
            if (!topic) {
                alert("Ange ett ämne att lära sig.");
                return;
            }
            
            soundSynth.playClick();
            btnStartLearning.disabled = true;
            topicInput.disabled = true;
            
            self.writeLog(`STARTING MANUAL LEARNING TASK FOR: "${topic}"`, "sys");
            
            try {
                const res = await fetch("/api/tools/execute", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ name: "learn_topic", args: { topic } })
                });
                if (res.ok) {
                    const data = await res.json();
                    if (data.task_id) {
                        self.pollLearningProgress(data.task_id);
                    }
                } else {
                    const err = await res.json();
                    self.writeLog(`LEARNING FAILURE: ${err.detail || "HTTP error"}`, "err");
                    soundSynth.playError();
                    btnStartLearning.disabled = false;
                    topicInput.disabled = false;
                }
            } catch (err) {
                console.error("Failed to start learning task:", err);
                btnStartLearning.disabled = false;
                topicInput.disabled = false;
            }
        });
    }

    // Cancel learning process
    const btnCancelLearning = document.getElementById('btn-cancel-learning');
    if (btnCancelLearning) {
        btnCancelLearning.addEventListener('click', async () => {
            soundSynth.playClick();
            try {
                const res = await fetch("/api/tools/cancel_download", { method: "POST" });
                if (res.ok) {
                    self.writeLog("LEARNING TASK CANCELLED BY USER", "warn");
                }
            } catch (err) {
                console.error("Failed to cancel learning:", err);
            }
        });
    }

    // Save domain credentials
    const btnSaveCredentials = document.getElementById('btn-save-credentials');
    if (btnSaveCredentials) {
        btnSaveCredentials.addEventListener('click', async () => {
            const domainInput = document.getElementById('cred-input-domain');
            const userInput = document.getElementById('cred-input-user');
            const passInput = document.getElementById('cred-input-pass');
            
            const domain = domainInput.value.trim();
            const username = userInput.value.trim();
            const password = passInput.value.trim();
            
            if (!domain || !username || !password) {
                alert("Fyll i domän, användarnamn och lösenord.");
                return;
            }
            
            soundSynth.playClick();
            btnSaveCredentials.disabled = true;
            
            try {
                const res = await fetch("/api/learning/credentials", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ domain, username, password })
                });
                if (res.ok) {
                    self.writeLog(`CREDENTIALS SAVED FOR: ${domain}`, "sys");
                    domainInput.value = "";
                    userInput.value = "";
                    passInput.value = "";
                    self.loadCredentialsUI();
                } else {
                    const err = await res.json();
                    alert("Kunde inte spara credentials: " + (err.detail || "Fel"));
                }
            } catch (err) {
                console.error(err);
            } finally {
                btnSaveCredentials.disabled = false;
            }
        });
    }

    // Refresh learning vault & credentials
    const btnRefreshLearning = document.getElementById('btn-refresh-learning');
    if (btnRefreshLearning) {
        btnRefreshLearning.addEventListener('click', () => {
            soundSynth.playClick();
            self.loadLearningVaultUI();
            self.loadCredentialsUI();
        });
    }

    // Generate Trainer Plan
    const btnGenerateTrainerPlan = document.getElementById('btn-generate-trainer-plan');
    if (btnGenerateTrainerPlan) {
        btnGenerateTrainerPlan.addEventListener('click', async () => {
            const goalInput = document.getElementById('trainer-input-goal').value.trim();
            const limitationsInput = document.getElementById('trainer-input-limitations').value.trim();
            if (!goalInput) {
                self.writeLog("COACH FAILURE: MÅL SAKNAS", "err");
                soundSynth.playError();
                alert("Ange ett träningsmål eller fokusområde.");
                return;
            }
            
            soundSynth.playClick();
            btnGenerateTrainerPlan.disabled = true;
            btnGenerateTrainerPlan.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> GENERERAR...';
            self.writeLog(`GENERATING PERSONAL TRAINER PLAN FOR: "${goalInput}"`, "sys");
            
            try {
                const res = await fetch('/api/trainer/generate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ goal: goalInput, limitations: limitationsInput })
                });
                
                if (res.ok) {
                    const data = await res.json();
                    self.writeLog("COACH PLAN GENERATED AND SAVED", "sys");
                    soundSynth.playNotify();
                    
                    const outputContainer = document.getElementById('trainer-plan-output-container');
                    const outputDiv = document.getElementById('trainer-plan-output');
                    if (outputContainer && outputDiv) {
                        self.renderTrainerPlanDetails(data.plan_id, data.advice_text);
                    }
                    
                    self.loadTrainerDashboardUI();
                } else {
                    const err = await res.json();
                    self.writeLog(`COACH ERROR: ${err.detail || 'Kunde inte generera'}`, "err");
                    soundSynth.playError();
                    alert(`Fel: ${err.detail || 'Kunde inte generera programmet.'}`);
                }
            } catch (e) {
                self.writeLog(`COACH ERROR: ${e.message}`, "err");
                soundSynth.playError();
                alert(`Fel vid kommunikation med servern: ${e.message}`);
            } finally {
                btnGenerateTrainerPlan.disabled = false;
                btnGenerateTrainerPlan.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> GENERERA TRÄNINGSPROGRAM';
            }
        });
    }

    // Refresh Trainer History
    const btnRefreshTrainer = document.getElementById('btn-refresh-trainer');
    if (btnRefreshTrainer) {
        btnRefreshTrainer.addEventListener('click', () => {
            soundSynth.playClick();
            self.loadTrainerDashboardUI();
        });
    }

    // Daily Trainer Check-in
    const btnTrainerCheckin = document.getElementById('btn-trainer-checkin');
    if (btnTrainerCheckin) {
        btnTrainerCheckin.addEventListener('click', () => {
            soundSynth.playClick();
            self.runTrainerCheckin();
        });
    }

    // Optimize upcoming workouts from recovery data (manual trigger)
    const btnTrainerOptimize = document.getElementById('btn-trainer-optimize');
    if (btnTrainerOptimize) {
        btnTrainerOptimize.addEventListener('click', () => {
            soundSynth.playClick();
            self.runTrainerOptimize();
        });
    }

    // Toggle automatic recovery-driven adjustment
    const chkTrainerAutoAdjust = document.getElementById('chk-trainer-auto-adjust');
    if (chkTrainerAutoAdjust) {
        chkTrainerAutoAdjust.addEventListener('change', () => {
            soundSynth.playClick();
            self.saveTrainerAutoAdjust(chkTrainerAutoAdjust.checked);
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
};
