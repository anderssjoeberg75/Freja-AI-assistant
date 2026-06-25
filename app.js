// Intercept all fetch requests to inject X-Freja-Token automatically for F.R.E.J.A. API endpoints.
// Also handle 401 Unauthorized globally by showing the login modal overlay.
window.originalFetch = window.fetch;
window.fetch = async function(url, options = {}) {
    let urlStr = typeof url === 'string' ? url : (url instanceof Request ? url.url : '');
    
    // Append header only for F.R.E.J.A. backend api endpoints, excluding external URLs
    if (urlStr.includes('/api/') && (!urlStr.startsWith('http') || urlStr.startsWith(window.location.origin + '/api/'))) {
        const token = localStorage.getItem('freja_access_token') || 'freja1234';
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
        return response;
    } catch (err) {
        throw err;
    }
};

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

    async pollFacebookDownloadProgress(taskId) {
        if (this.facebookDownloadInterval) return; // already polling
        
        const self = this;
        self.writeLog(`BACKGROUND FACEBOOK DOWNLOAD MONITOR ACTIVE. Task ID: ${taskId.substring(0, 8)}...`, "sys");
        
        this.facebookDownloadInterval = setInterval(async () => {
            try {
                const res = await fetch(`/api/tools/status/${taskId}`);
                if (!res.ok) {
                    clearInterval(self.facebookDownloadInterval);
                    self.facebookDownloadInterval = null;
                    self.writeLog(`FACEBOOK DOWNLOAD ERROR: Failed to fetch task status.`, "err");
                    soundSynth.playError();
                    return;
                }
                const statusData = await res.json();
                
                if (statusData.status === "success") {
                    clearInterval(self.facebookDownloadInterval);
                    self.facebookDownloadInterval = null;
                    
                    self.writeLog(`[FACEBOOK DOWNLOAD] COMPLETED SUCCESSFULLY. Saved ${statusData.result.downloaded_count} images.`, "sys");
                    soundSynth.playNotify();
                    
                    if (self.speech && self.speech.autoSpeak) {
                        self.speech.speak(`Nedladdningen av Facebook-bilder är klar. Hämtade ${statusData.result.downloaded_count} bilder.`);
                    }
                    
                    self.appendChatMessage("assistant", `**[SYSTEMMEDDELANDE]** Nedladdningen av Facebook-bilder är klar! Totalt hämtades ${statusData.result.downloaded_count} bilder.`, false);
                    
                } else if (statusData.status === "failed") {
                    clearInterval(self.facebookDownloadInterval);
                    self.facebookDownloadInterval = null;
                    
                    self.writeLog(`[FACEBOOK DOWNLOAD] FAILED: ${statusData.error || "Okänt fel"}`, "err");
                    soundSynth.playError();
                    
                    if (self.speech && self.speech.autoSpeak) {
                        self.speech.speak(`Nedladdningen av Facebook-bilder misslyckades.`);
                    }
                    
                    self.appendChatMessage("assistant", `**[SYSTEMMEDDELANDE - FEL]** Nedladdningen av Facebook-bilder misslyckades: ${statusData.error || "Okänt fel"}`, false);
                    
                } else if (statusData.status === "cancelled") {
                    clearInterval(self.facebookDownloadInterval);
                    self.facebookDownloadInterval = null;
                    
                    self.writeLog(`[FACEBOOK DOWNLOAD] CANCELLED BY USER.`, "warn");
                    soundSynth.playError();
                    
                    self.appendChatMessage("assistant", `**[SYSTEMMEDDELANDE]** Nedladdningen av Facebook-bilder avbröts.`, false);
                    
                } else {
                    const progress = statusData.progress || 0;
                    const stage = statusData.stage || "initierar...";
                    const current = statusData.current || 0;
                    const total = statusData.total || 0;
                    
                    let progressMsg = `[FACEBOOK DOWNLOAD] ${stage}`;
                    if (total > 0) {
                        progressMsg += ` (${current}/${total} - ${progress}%)`;
                    }
                    self.writeLog(progressMsg, "sys");
                }
            } catch (err) {
                console.error("Error polling facebook download:", err);
            }
        }, 3000);
    }

    async pollSyncStatus(provider) {
        if (this[`syncInterval_${provider}`]) return; // already polling
        
        const self = this;
        const btn = document.getElementById(`btn-sync-${provider}-dashboard`);
        const btnAll = document.getElementById(`btn-sync-${provider}-all`);
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> SYNKAR...`;
        }
        if (btnAll) {
            btnAll.disabled = true;
            btnAll.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> SYNKAR...`;
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
                            btn.innerHTML = provider === 'google_calendar'
                                ? `<i class="fa-solid fa-arrows-rotate"></i> SYNKRONISERA KALENDER`
                                : `<i class="fa-solid fa-arrows-rotate"></i> SYNKRONISERA ENHET`;
                        }
                        if (btnAll) {
                            btnAll.disabled = false;
                            btnAll.innerHTML = `<i class="fa-solid fa-clock-rotate-left"></i> HÄMTA ALL HISTORIK`;
                        }
                        if (capItem) {
                            capItem.classList.remove('syncing-blink');
                        }
                        
                        self.writeLog(`BACKGROUND SYNCHRONIZATION COMPLETED FOR ${provider.toUpperCase()}`, "sys");
                        soundSynth.playNotify();
                        
                        if (provider === 'garmin') self.loadGarminDashboardUI();
                        if (provider === 'strava') self.loadStravaDashboardUI();
                        if (provider === 'withings') self.loadWithingsDashboardUI();
                        if (provider === 'google_calendar') self.loadGoogleCalendarDashboardUI();
                        
                    } else if (state === 'error') {
                        clearInterval(self[`syncInterval_${provider}`]);
                        self[`syncInterval_${provider}`] = null;
                        
                        if (btn) {
                            btn.disabled = false;
                            btn.innerHTML = provider === 'google_calendar'
                                ? `<i class="fa-solid fa-arrows-rotate"></i> SYNKRONISERA KALENDER`
                                : `<i class="fa-solid fa-arrows-rotate"></i> SYNKRONISERA ENHET`;
                        }
                        if (btnAll) {
                            btnAll.disabled = false;
                            btnAll.innerHTML = `<i class="fa-solid fa-clock-rotate-left"></i> HÄMTA ALL HISTORIK`;
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
                if (keys.freja_access_token !== undefined) {
                    localStorage.setItem("freja_access_token", keys.freja_access_token);
                }
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
                if (keys.freja_google_calendar_client_id !== undefined) {
                    localStorage.setItem("freja_google_calendar_client_id", keys.freja_google_calendar_client_id);
                }
                if (keys.freja_google_calendar_client_secret !== undefined) {
                    localStorage.setItem("freja_google_calendar_client_secret", keys.freja_google_calendar_client_secret);
                }
                if (keys.freja_google_calendar_refresh_token !== undefined) {
                    localStorage.setItem("freja_google_calendar_refresh_token", keys.freja_google_calendar_refresh_token);
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
     * Pulls previously cached configuration values from LocalStorage.
     */
    initializeUI() {
        const accessToken = localStorage.getItem("freja_access_token") || "freja1234";
        const inputAccessToken = document.getElementById('input-access-token');
        if (inputAccessToken) inputAccessToken.value = accessToken;

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

        const googleClientId = localStorage.getItem("freja_google_calendar_client_id") || "";
        const googleClientSecret = localStorage.getItem("freja_google_calendar_client_secret") || "";
        const googleRefreshToken = localStorage.getItem("freja_google_calendar_refresh_token") || "";
        const inputGoogleClientId = document.getElementById('input-google-calendar-client-id');
        if (inputGoogleClientId) inputGoogleClientId.value = googleClientId;
        const inputGoogleClientSecret = document.getElementById('input-google-calendar-client-secret');
        if (inputGoogleClientSecret) inputGoogleClientSecret.value = googleClientSecret;
        const inputGoogleRefreshToken = document.getElementById('input-google-calendar-refresh-token');
        if (inputGoogleRefreshToken) inputGoogleRefreshToken.value = googleRefreshToken;
        
        this.memory.apiKey = mem0Key;
        this.memory.enabled = mem0Enabled;
        this.memory.updateCapBadge();

        // Load camera settings
        window.FrejaCamera.init();
        const autoOptics = localStorage.getItem("freja_auto_optics") !== "false";
        
        const chkAutoOptics = document.getElementById('chk-auto-optics');
        if (chkAutoOptics) {
            chkAutoOptics.checked = autoOptics;
        }
        this.savedCameraId = window.FrejaCamera.savedCameraId;

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

        const codexExecAllowed = localStorage.getItem("freja_tool_execute_codex_code_allowed") === "true" || localStorage.getItem("freja_tool_run_code_allowed") === "true";
        const chkCodexExec = document.getElementById('chk-tool-execute_codex_code');
        if (chkCodexExec) {
            chkCodexExec.checked = codexExecAllowed;
        }

        const codexGitAllowed = localStorage.getItem("freja_tool_codex_git_ops_allowed") === "true";
        const chkCodexGit = document.getElementById('chk-tool-codex_git_ops');
        if (chkCodexGit) {
            chkCodexGit.checked = codexGitAllowed;
        }

        const codexAuditAllowed = localStorage.getItem("freja_tool_codex_audit_codebase_allowed") === "true" || localStorage.getItem("freja_tool_tool_analyze_code_allowed") === "true";
        const chkCodexAudit = document.getElementById('chk-tool-codex_audit_codebase');
        if (chkCodexAudit) {
            chkCodexAudit.checked = codexAuditAllowed;
        }

        const codexFixAllowed = localStorage.getItem("freja_tool_codex_run_and_fix_allowed") === "true";
        const chkCodexFix = document.getElementById('chk-tool-codex_run_and_fix');
        if (chkCodexFix) {
            chkCodexFix.checked = codexFixAllowed;
        }

        const facebookDownloadAllowed = localStorage.getItem("freja_tool_download_facebook_photos_allowed") === "true";
        const chkFacebookDownload = document.getElementById('chk-tool-download_facebook_photos');
        if (chkFacebookDownload) {
            chkFacebookDownload.checked = facebookDownloadAllowed;
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

        const googleCalendarAllowed = localStorage.getItem("freja_tool_manage_google_calendar_allowed") === "true";
        const chkGoogleCalendar = document.getElementById('chk-tool-manage_google_calendar');
        if (chkGoogleCalendar) {
            chkGoogleCalendar.checked = googleCalendarAllowed;
        }

        const capGoogleCalendar = document.getElementById('cap-google_calendar');
        if (capGoogleCalendar) {
            if (googleCalendarAllowed) {
                capGoogleCalendar.classList.add('active');
            } else {
                capGoogleCalendar.classList.remove('active');
            }
        }

        const trainerAllowed = localStorage.getItem("freja_tool_get_personal_trainer_advice_allowed") === "true";
        const chkTrainer = document.getElementById('chk-tool-get_personal_trainer_advice');
        if (chkTrainer) {
            chkTrainer.checked = trainerAllowed;
        }

        const capTrainer = document.getElementById('cap-trainer');
        if (capTrainer) {
            if (trainerAllowed) {
                capTrainer.classList.add('active');
            } else {
                capTrainer.classList.remove('active');
            }
        }

        const learnTopicAllowed = localStorage.getItem("freja_tool_learn_topic_allowed") === "true";
        const chkLearnTopic = document.getElementById('chk-tool-learn_topic');
        if (chkLearnTopic) {
            chkLearnTopic.checked = learnTopicAllowed;
        }

        const getLearnedKnowledgeAllowed = localStorage.getItem("freja_tool_get_learned_knowledge_allowed") === "true";
        const chkGetLearnedKnowledge = document.getElementById('chk-tool-get_learned_knowledge');
        if (chkGetLearnedKnowledge) {
            chkGetLearnedKnowledge.checked = getLearnedKnowledgeAllowed;
        }

        const capLearning = document.getElementById('cap-learning');
        if (capLearning) {
            if (learnTopicAllowed || getLearnedKnowledgeAllowed) {
                capLearning.classList.add('active');
            } else {
                capLearning.classList.remove('active');
            }
        }

        this.applyTheme(theme);
    }

    /**
     * Configures DOM button click bindings, forms triggers, and inputs listeners.
     */
    bindEvents() {
        const self = this;

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
                if (self.hasLoadedHistory) {
                    const sv = document.getElementById('select-lang-quick').value === 'sv-SE';
                    const resumeMsg = sv
                        ? "Välkommen tillbaka, sir. Chattsession återupptagen."
                        : "Welcome back, sir. Chat session resumed.";
                    self.speech.speak(resumeMsg);
                    self.writeLog("SESSION RESUMED. CHAT HISTORY SYNCHRONIZED.", "sys");
                    return;
                }

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
                
                // Save keys to secure SQLite database
                await self.saveKeysToServer({
                    freja_garmin_email: garminEmail,
                    freja_garmin_password: garminPassword
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
                
                // Save keys to secure SQLite database
                await self.saveKeysToServer({
                    freja_strava_client_id: stravaClientId,
                    freja_strava_client_secret: stravaClientSecret,
                    freja_strava_refresh_token: stravaRefreshToken
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
                
                // Save keys to secure SQLite database
                await self.saveKeysToServer({
                    freja_withings_client_id: withingsClientId,
                    freja_withings_client_secret: withingsClientSecret,
                    freja_withings_refresh_token: withingsRefreshToken
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
                
                // Save keys to secure SQLite database
                await self.saveKeysToServer({
                    freja_google_calendar_client_id: googleClientId,
                    freja_google_calendar_client_secret: googleClientSecret,
                    freja_google_calendar_refresh_token: googleRefreshToken
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

            // Save tool permissions
            const chkWeather = document.getElementById('chk-tool-get_weather');
            if (chkWeather) {
                localStorage.setItem("freja_tool_get_weather_allowed", chkWeather.checked);
            }

            const chkSearch = document.getElementById('chk-tool-google_search');
            if (chkSearch) {
                localStorage.setItem("freja_tool_google_search_allowed", chkSearch.checked);
            }

            const chkCodexExec = document.getElementById('chk-tool-execute_codex_code');
            if (chkCodexExec) {
                localStorage.setItem("freja_tool_execute_codex_code_allowed", chkCodexExec.checked);
                localStorage.setItem("freja_tool_run_code_allowed", chkCodexExec.checked);
            }

            const chkCodexGit = document.getElementById('chk-tool-codex_git_ops');
            if (chkCodexGit) {
                localStorage.setItem("freja_tool_codex_git_ops_allowed", chkCodexGit.checked);
            }

            const chkCodexAudit = document.getElementById('chk-tool-codex_audit_codebase');
            if (chkCodexAudit) {
                localStorage.setItem("freja_tool_codex_audit_codebase_allowed", chkCodexAudit.checked);
                localStorage.setItem("freja_tool_tool_analyze_code_allowed", chkCodexAudit.checked);
            }

            const chkCodexFix = document.getElementById('chk-tool-codex_run_and_fix');
            if (chkCodexFix) {
                localStorage.setItem("freja_tool_codex_run_and_fix_allowed", chkCodexFix.checked);
            }

            const chkFacebookDownload = document.getElementById('chk-tool-download_facebook_photos');
            if (chkFacebookDownload) {
                localStorage.setItem("freja_tool_download_facebook_photos_allowed", chkFacebookDownload.checked);
            }

            const chkTrainer = document.getElementById('chk-tool-get_personal_trainer_advice');
            if (chkTrainer) {
                localStorage.setItem("freja_tool_get_personal_trainer_advice_allowed", chkTrainer.checked);
                const capTrainer = document.getElementById('cap-trainer');
                if (capTrainer) {
                    if (chkTrainer.checked) {
                        capTrainer.classList.add('active');
                    } else {
                        capTrainer.classList.remove('active');
                    }
                }
            }

            const chkLearnTopic = document.getElementById('chk-tool-learn_topic');
            if (chkLearnTopic) {
                localStorage.setItem("freja_tool_learn_topic_allowed", chkLearnTopic.checked);
            }

            const chkGetLearnedKnowledge = document.getElementById('chk-tool-get_learned_knowledge');
            if (chkGetLearnedKnowledge) {
                localStorage.setItem("freja_tool_get_learned_knowledge_allowed", chkGetLearnedKnowledge.checked);
            }

            const capLearning = document.getElementById('cap-learning');
            if (capLearning) {
                if ((chkLearnTopic && chkLearnTopic.checked) || (chkGetLearnedKnowledge && chkGetLearnedKnowledge.checked)) {
                    capLearning.classList.add('active');
                } else {
                    capLearning.classList.remove('active');
                }
            }

            const accessTokenVal = document.getElementById('input-access-token').value.trim();

            // Save keys to secure SQLite database
            const success = await self.saveKeysToServer({
                freja_access_token: accessTokenVal,
                freja_gemini_apikey: apiKey,
                freja_eleven_apikey: elevenKey
            });

            if (success && accessTokenVal) {
                localStorage.setItem("freja_access_token", accessTokenVal);
            }

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
        window.FrejaTheme.applyTheme(theme);
    }

    /**
     * Resolves the canvas accent hue angle based on the selected CSS theme.
     */
    getCurrentThemeHue() {
        return window.FrejaTheme.getCurrentThemeHue();
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
     * Fetches and renders trainer plans history inside the Personal Trainer Dashboard.
     */
    async loadTrainerDashboardUI() {
        const trainerList = document.getElementById('trainer-list');
        if (!trainerList) return;
        
        trainerList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">Laddar historik...</div>';
        
        try {
            const res = await fetch('/api/trainer/plans?limit=10');
            if (!res.ok) throw new Error(`HTTP error ${res.status}`);
            
            const plans = await res.json();
            if (plans.length === 0) {
                trainerList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[INGA TIDIGARE PROGRAM HITTADE]</div>';
                return;
            }
            
            trainerList.innerHTML = "";
            plans.forEach(plan => {
                const item = document.createElement('div');
                item.className = "trainer-plan-item";
                item.style.display = "flex";
                item.style.justifyContent = "space-between";
                item.style.alignItems = "center";
                item.style.padding = "8px";
                item.style.borderBottom = "1px solid rgba(0, 242, 254, 0.08)";
                item.style.fontSize = "11px";
                item.style.fontFamily = "var(--font-mono)";
                
                const limitInfo = plan.limitations ? ` (${plan.limitations})` : "";
                item.innerHTML = `
                    <div style="flex: 1; cursor: pointer; color: var(--color-text-bright);" class="trainer-view-btn">
                        <span style="color: var(--color-primary);">${plan.date}</span>: ${plan.goal}${limitInfo}
                    </div>
                    <div style="display: flex; gap: 8px;">
                        <button class="trainer-view-icon-btn" title="Visa detaljer" style="background: transparent; border: none; color: var(--color-primary); cursor: pointer; padding: 2px 4px;">
                            <i class="fa-solid fa-eye"></i>
                        </button>
                        <button class="trainer-delete-btn" data-id="${plan.id}" title="Radera logg" style="background: transparent; border: none; color: #ff3b30; cursor: pointer; padding: 2px 4px;">
                            <i class="fa-solid fa-trash-can"></i>
                        </button>
                    </div>
                `;
                
                const showPlan = () => {
                    soundSynth.playClick();
                    const outputContainer = document.getElementById('trainer-plan-output-container');
                    const outputDiv = document.getElementById('trainer-plan-output');
                    if (outputContainer && outputDiv) {
                        this.renderTrainerPlanDetails(plan.id, plan.advice_text);
                    }
                };

                item.querySelector('.trainer-view-btn').addEventListener('click', showPlan);
                item.querySelector('.trainer-view-icon-btn').addEventListener('click', showPlan);
                
                const delBtn = item.querySelector('.trainer-delete-btn');
                delBtn.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    if (!confirm("Vill du verkligen radera detta program?")) return;
                    soundSynth.playClick();
                    try {
                        const delRes = await fetch(`/api/trainer/plans?plan_id=${plan.id}`, {
                            method: 'DELETE'
                        });
                        if (delRes.ok) {
                            this.writeLog(`DELETED TRAINER PLAN ID ${plan.id}`, "sys");
                            this.loadTrainerDashboardUI();
                            
                            // Clear output if we deleted the currently viewed plan
                            const outputDiv = document.getElementById('trainer-plan-output');
                            if (outputDiv && outputDiv.textContent === plan.advice_text) {
                                document.getElementById('trainer-plan-output-container').style.display = 'none';
                                outputDiv.textContent = '';
                            }
                        }
                    } catch (err) {
                        console.error("[TRAINER] Failed to delete plan:", err);
                    }
                });
                
                trainerList.appendChild(item);
            });
        } catch (e) {
            console.error("[TRAINER] UI load error:", e);
            trainerList.innerHTML = '<div style="color: #ff3b30; text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[FEL VID HÄMTNING AV HISTORIK]</div>';
        }
    }

    /**
     * Renders structured/unstructured trainer advice details with calendar/checkbox interactions.
     */
    renderTrainerPlanDetails(planId, adviceText) {
        const outputContainer = document.getElementById('trainer-plan-output-container');
        const outputDiv = document.getElementById('trainer-plan-output');
        if (!outputContainer || !outputDiv) return;
        
        outputContainer.style.display = 'flex';
        
        outputDiv.style.fontFamily = "var(--font-sans)";
        outputDiv.style.fontSize = "13px";
        outputDiv.style.maxHeight = "400px";
        
        let planData = null;
        try {
            let cleanText = adviceText.trim();
            if (cleanText.startsWith("```json")) {
                cleanText = cleanText.substring(7);
            }
            if (cleanText.startsWith("```")) {
                cleanText = cleanText.substring(3);
            }
            if (cleanText.endsWith("```")) {
                cleanText = cleanText.substring(0, cleanText.length - 3);
            }
            cleanText = cleanText.trim();
            planData = JSON.parse(cleanText);
        } catch (e) {
            planData = null;
        }
        
        if (!planData) {
            outputDiv.style.fontFamily = "var(--font-mono)";
            outputDiv.style.fontSize = "11px";
            outputDiv.innerHTML = window.FrejaMarkdown.parseMarkdown(adviceText);
            return;
        }
        
        const rhr_trend = planData.resting_hr_trend || "Stabil / Saknas";
        const hrv_trend = planData.hrv_trend || "Normal / Saknas";
        const weekly_focus = planData.weekly_focus || "Allmän träning";
        const summary = planData.summary || "";
        const workouts = planData.workouts || [];
        
        const getNextMondayStr = () => {
            const today = new Date();
            const day = today.getDay();
            const distanceToMonday = (8 - day) % 7 || 7;
            const nextMonday = new Date(today);
            nextMonday.setDate(today.getDate() + distanceToMonday);
            
            const yyyy = nextMonday.getFullYear();
            let mm = nextMonday.getMonth() + 1;
            let dd = nextMonday.getDate();
            if (mm < 10) mm = '0' + mm;
            if (dd < 10) dd = '0' + dd;
            return `${yyyy}-${mm}-${dd}`;
        };
        
        const workoutsHTML = workouts.map((w, idx) => {
            const isCompleted = w.completed ? 'checked' : '';
            const completedStyle = w.completed ? 'text-decoration: line-through; opacity: 0.6;' : '';
            
            let icon = "fa-person-running";
            const type = (w.activity_type || "").toLowerCase();
            if (type.includes("styrka") || type.includes("gym") || type.includes("body")) {
                icon = "fa-dumbbell";
            } else if (type.includes("cykel") || type.includes("cykling") || type.includes("bike")) {
                icon = "fa-bicycle";
            } else if (type.includes("yoga") || type.includes("stretch") || type.includes("rörlighet")) {
                icon = "fa-child-reaching";
            } else if (type.includes("vila") || type.includes("återhämtning") || type.includes("rest")) {
                icon = "fa-bed";
            }
            
            return `
                <div style="display: flex; gap: 10px; background: rgba(0,0,0,0.15); border: 1px solid rgba(0,242,254,0.08); border-radius: 4px; padding: 10px; align-items: flex-start;">
                    <input type="checkbox" class="workout-checkbox" data-index="${idx}" ${isCompleted} style="margin-top: 3px; cursor: pointer; width: 14px; height: 14px;">
                    <div style="flex: 1; display: flex; flex-direction: column; gap: 2px; ${completedStyle}">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <span style="font-weight: bold; font-size: 12px; color: var(--color-text-bright);">${w.day}: <i class="fa-solid ${icon}"></i> ${w.title}</span>
                            <span style="font-size: 10px; color: var(--color-primary);">${w.duration_minutes > 0 ? w.duration_minutes + ' min' : 'Vila'}</span>
                        </div>
                        <div style="font-size: 11px; color: var(--color-text-muted); line-height: 1.4; margin-top: 2px;">
                            ${w.description}
                        </div>
                    </div>
                </div>
            `;
        }).join('');
        
        outputDiv.innerHTML = `
            <div class="trainer-structured-plan" style="display: flex; flex-direction: column; gap: 15px; font-family: var(--font-sans, inherit); color: var(--color-text); text-align: left;">
                
                <!-- Trends Section -->
                <div class="trainer-trends-row" style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
                    <div class="trend-card" style="background: rgba(0,0,0,0.3); border: 1px solid rgba(0, 242, 254, 0.15); border-radius: 4px; padding: 10px;">
                        <div style="font-size: 9px; color: var(--color-text-muted); font-family: var(--font-display); letter-spacing: 0.5px;">VILOPULS TREND (RHR)</div>
                        <div style="font-size: 11px; font-weight: bold; margin-top: 4px; color: var(--color-primary); font-family: var(--font-mono);">${rhr_trend}</div>
                    </div>
                    <div class="trend-card" style="background: rgba(0,0,0,0.3); border: 1px solid rgba(0, 242, 254, 0.15); border-radius: 4px; padding: 10px;">
                        <div style="font-size: 9px; color: var(--color-text-muted); font-family: var(--font-display); letter-spacing: 0.5px;">HRV TREND</div>
                        <div style="font-size: 11px; font-weight: bold; margin-top: 4px; color: var(--color-primary); font-family: var(--font-mono);">${hrv_trend}</div>
                    </div>
                </div>

                <!-- Weekly Focus -->
                <div class="focus-banner" style="background: rgba(0, 242, 254, 0.08); border-left: 3px solid var(--color-primary); padding: 10px; border-radius: 0 4px 4px 0;">
                    <div style="font-size: 9px; color: var(--color-primary); font-family: var(--font-display); letter-spacing: 0.5px;">VECKANS FOKUS</div>
                    <div style="font-size: 12px; font-weight: bold; margin-top: 3px;">${weekly_focus}</div>
                </div>

                <!-- Summary -->
                <div class="summary-section" style="font-size: 12px; line-height: 1.5; color: var(--color-text-muted);">
                    ${summary}
                </div>

                <!-- Workouts Checklist -->
                <div class="workouts-section">
                    <div style="font-size: 10px; color: var(--color-primary); font-family: var(--font-display); margin-bottom: 8px; letter-spacing: 0.5px;">VECKANS TRÄNINGSPASS</div>
                    <div style="display: flex; flex-direction: column; gap: 8px;">
                        ${workoutsHTML}
                    </div>
                </div>

                <!-- Calendar Booking Widget -->
                <div class="booking-widget" style="background: rgba(0, 0, 0, 0.25); border: 1px solid rgba(0, 242, 254, 0.15); border-radius: 4px; padding: 12px; display: flex; flex-direction: column; gap: 8px;">
                    <div style="font-size: 9px; color: var(--color-text-muted); font-family: var(--font-display); letter-spacing: 0.5px;">BOKA IN PASSEN I DIN GOOGLE KALENDER</div>
                    <div style="display: flex; gap: 8px; align-items: center;">
                        <input type="date" id="trainer-book-start-date" class="hud-input" style="height: 32px; font-size: 12px; flex: 1;" value="${getNextMondayStr()}">
                        <button id="btn-trainer-book-calendar" class="hud-btn btn-primary" style="height: 32px; font-family: var(--font-display); font-size: 11px; padding: 0 12px; display: flex; align-items: center; gap: 5px;">
                            <i class="fa-solid fa-calendar-plus"></i> BOKA PASS
                        </button>
                    </div>
                </div>

            </div>
        `;
        
        outputDiv.querySelectorAll('.workout-checkbox').forEach(cb => {
            cb.addEventListener('change', async (e) => {
                const idx = parseInt(e.target.getAttribute('data-index'));
                planData.workouts[idx].completed = e.target.checked;
                
                soundSynth.playClick();
                
                try {
                    const putRes = await fetch('/api/trainer/plans', {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            plan_id: planId,
                            advice_text: JSON.stringify(planData)
                        })
                    });
                    if (putRes.ok) {
                        this.writeLog(`WORKOUT STATUS UPDATED`, "sys");
                        this.renderTrainerPlanDetails(planId, JSON.stringify(planData));
                    }
                } catch (err) {
                    console.error("Error saving completed workout state:", err);
                }
            });
        });
        
        const btnBook = document.getElementById('btn-trainer-book-calendar');
        if (btnBook) {
            btnBook.addEventListener('click', async () => {
                const startDateVal = document.getElementById('trainer-book-start-date').value;
                if (!startDateVal) {
                    alert("Ange ett startdatum för träningsveckan.");
                    return;
                }
                
                soundSynth.playClick();
                btnBook.disabled = true;
                btnBook.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> BOKAR...';
                
                try {
                    const bookRes = await fetch('/api/trainer/plans/book', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            plan_id: planId,
                            start_date: startDateVal
                        })
                    });
                    
                    if (bookRes.ok) {
                        const bookData = await bookRes.json();
                        this.writeLog(`CALENDAR: ${bookData.message.toUpperCase()}`, "sys");
                        soundSynth.playNotify();
                        alert(bookData.message);
                    } else {
                        const bookErr = await bookRes.json();
                        this.writeLog(`CALENDAR ERROR: ${bookErr.detail}`, "err");
                        soundSynth.playError();
                        alert(`Fel vid bokning: ${bookErr.detail}`);
                    }
                } catch (err) {
                    this.writeLog(`CALENDAR EXCEPTION: ${err.message}`, "err");
                    soundSynth.playError();
                    alert(`Fel vid kommunikation med servern: ${err.message}`);
                } finally {
                    btnBook.disabled = false;
                    btnBook.innerHTML = '<i class="fa-solid fa-calendar-plus"></i> BOKA PASS';
                }
            });
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
     * Synchronizes and draws the Google Calendar events inside the Google Calendar Dashboard overlay.
     */
    async loadGoogleCalendarDashboardUI() {
        const calendarList = document.getElementById('google-calendar-list');
        if (!calendarList) return;
        
        // Set start/end input defaults to today and tomorrow if empty
        const startInput = document.getElementById('google-calendar-input-start');
        const endInput = document.getElementById('google-calendar-input-end');
        if (startInput && !startInput.value) {
            const now = new Date();
            const startISO = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
            startInput.value = startISO;
        }
        if (endInput && !endInput.value) {
            const nextHour = new Date(new Date().getTime() + 60 * 60 * 1000);
            const endISO = new Date(nextHour.getTime() - nextHour.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
            endInput.value = endISO;
        }

        calendarList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">Laddar kalender...</div>';
        
        try {
            const res = await fetch('/api/google_calendar/data?days=30');
            if (!res.ok) throw new Error(`HTTP error ${res.status}`);
            
            const events = await res.json();
            if (events.length === 0) {
                calendarList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[INGA HÄNDELSER HITTADE]</div>';
                return;
            }
            
            calendarList.innerHTML = "";
            events.forEach(evt => {
                const item = document.createElement('div');
                item.className = "google-calendar-log-item";
                item.style.display = "flex";
                item.style.justifyContent = "space-between";
                item.style.alignItems = "flex-start";
                item.style.padding = "8px";
                item.style.borderBottom = "1px solid rgba(0, 242, 254, 0.08)";
                item.style.fontSize = "11px";
                item.style.fontFamily = "var(--font-mono)";
                
                const formatDateTime = (isoStr) => {
                    if (!isoStr) return "";
                    const parts = isoStr.split('T');
                    if (parts.length === 2) {
                        return `${parts[0]} ${parts[1].substring(0, 5)}`;
                    }
                    return isoStr;
                };

                const startFormatted = formatDateTime(evt.start_time);
                const endFormatted = formatDateTime(evt.end_time);
                
                const locInfo = evt.location ? ` <span style="color: var(--color-accent);"><i class="fa-solid fa-location-dot"></i> ${evt.location}</span>` : "";
                const descInfo = evt.description ? `<div style="color: var(--color-text-muted); margin-top: 2px; font-size: 10px; font-style: italic; white-space: pre-wrap;">${evt.description}</div>` : "";

                item.innerHTML = `
                    <div style="flex: 1; color: var(--color-text-bright); margin-right: 10px;">
                        <strong style="color: var(--color-primary); font-size: 12px;">${evt.summary}</strong>${locInfo}
                        <div style="color: var(--color-text-muted); margin-top: 2px;">Tid: ${startFormatted} - ${endFormatted}</div>
                        ${descInfo}
                    </div>
                    <div style="display: flex; gap: 5px; align-self: center;">
                        <button class="calendar-edit-btn" title="Redigera händelse" style="background: transparent; border: none; color: var(--color-primary); cursor: pointer; padding: 2px 6px;">
                            <i class="fa-solid fa-pencil"></i>
                        </button>
                        <button class="calendar-delete-btn" title="Radera händelse" style="background: transparent; border: none; color: #ff3b30; cursor: pointer; padding: 2px 6px;">
                            <i class="fa-solid fa-trash-can"></i>
                        </button>
                    </div>
                `;
                
                // Bind Edit Action
                const editBtn = item.querySelector('.calendar-edit-btn');
                editBtn.addEventListener('click', () => {
                    soundSynth.playClick();
                    document.getElementById('google-calendar-input-id').value = evt.id;
                    document.getElementById('google-calendar-input-summary').value = evt.summary;
                    document.getElementById('google-calendar-input-start').value = evt.start_time.substring(0, 16);
                    document.getElementById('google-calendar-input-end').value = evt.end_time.substring(0, 16);
                    document.getElementById('google-calendar-input-description').value = evt.description || "";
                    document.getElementById('google-calendar-input-location').value = evt.location || "";
                    
                    const btnSave = document.getElementById('btn-save-google-calendar-manual');
                    if (btnSave) btnSave.innerHTML = `<i class="fa-solid fa-floppy-disk"></i> SPARA ÄNDRINGAR`;
                    
                    const btnCancel = document.getElementById('btn-cancel-google-calendar-edit');
                    if (btnCancel) btnCancel.style.display = "block";
                });
                
                // Bind Delete Action
                const delBtn = item.querySelector('.calendar-delete-btn');
                delBtn.addEventListener('click', async () => {
                    if (!confirm(`Vill du verkligen ta bort händelsen "${evt.summary}"?`)) return;
                    soundSynth.playClick();
                    item.style.opacity = '0.5';
                    try {
                        const delRes = await fetch(`/api/google_calendar/delete?id=${evt.id}`);
                        const delData = await delRes.json();
                        if (delRes.ok && delData.status === 'success') {
                            this.writeLog(`CALENDAR EVENT "${evt.summary}" REMOVED`, "sys");
                            item.remove();
                            if (calendarList.children.length === 0) {
                                calendarList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[INGA HÄNDELSER HITTADE]</div>';
                            }
                        } else {
                            throw new Error(delData.message || "Failed deleting");
                        }
                    } catch (err) {
                        item.style.opacity = '1';
                        this.writeLog(`CALENDAR DELETE ERROR: ${err.message}`, "err");
                        soundSynth.playError();
                    }
                });
                
                calendarList.appendChild(item);
            });
        } catch (err) {
            console.error("[CALENDAR] UI load error:", err);
            calendarList.innerHTML = '<div style="color: #ff3b30; text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[FEL VID LADDNING AV KALENDER]</div>';
        }
    }

    /**
     * Executes conversational transactions, drawing replies and managing long-term memory encodes.
     */
    async processUserQuery(text) {
        const cleanText = text.trim().toLowerCase();
        const cleanTextNoPunct = cleanText.replace(/[.,\/#!$%\^&\*;:{}=\-_`~()?]/g, "");
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
        return window.FrejaMarkdown.parseMarkdown(text);
    }

    /**
     * Copies code content from code blocks to user clipboard.
     */
    copyCode(button) {
        window.FrejaMarkdown.copyCode(button);
    }

    /**
     * Handles tool calls requested by Gemini.
     * Checks permissions, prompts the user if permission is missing, and executes the tool.
     */
    async handleToolCall(call) {
        this.writeLog(`TOOL CALL REQUESTED: ${call.name}`, "sys");
        
        const toolsMetadata = {
            "get_weather": {
                name: "get_weather",
                displayName: "Väderprognos",
                permissionKey: "freja_tool_get_weather_allowed"
            },
            "google_search": {
                name: "google_search",
                displayName: "Google Sökning",
                permissionKey: "freja_tool_google_search_allowed"
            },
            "get_garmin_health": {
                name: "get_garmin_health",
                displayName: "Garmin Hälsodata",
                permissionKey: "freja_tool_get_garmin_health_allowed"
            },
            "get_withings_health": {
                name: "get_withings_health",
                displayName: "Withings Hälsodata",
                permissionKey: "freja_tool_get_withings_health_allowed"
            },
            "get_strava_data": {
                name: "get_strava_data",
                displayName: "Strava Aktiviteter",
                permissionKey: "freja_tool_get_strava_data_allowed"
            },
            "get_strava_activity_analysis": {
                name: "get_strava_activity_analysis",
                displayName: "Strava Aktivitetsanalys",
                permissionKey: "freja_tool_get_strava_activity_analysis_allowed"
            },
            "get_strava_athlete_stats": {
                name: "get_strava_athlete_stats",
                displayName: "Strava Atletstatistik",
                permissionKey: "freja_tool_get_strava_athlete_stats_allowed"
            },
            "manage_google_calendar": {
                name: "manage_google_calendar",
                displayName: "Google Kalender",
                permissionKey: "freja_tool_manage_google_calendar_allowed"
            },
            "execute_codex_code": {
                name: "execute_codex_code",
                displayName: "Kodexekvering",
                permissionKey: "freja_tool_execute_codex_code_allowed"
            },
            "run_code": {
                name: "run_code",
                displayName: "Kodexekvering",
                permissionKey: "freja_tool_run_code_allowed"
            },
            "codex_git_ops": {
                name: "codex_git_ops",
                displayName: "Git-operationer",
                permissionKey: "freja_tool_codex_git_ops_allowed"
            },
            "codex_audit_codebase": {
                name: "codex_audit_codebase",
                displayName: "Kodgranskning",
                permissionKey: "freja_tool_codex_audit_codebase_allowed"
            },
            "tool_analyze_code": {
                name: "tool_analyze_code",
                displayName: "Kodgranskning",
                permissionKey: "freja_tool_tool_analyze_code_allowed"
            },
            "codex_run_and_fix": {
                name: "codex_run_and_fix",
                displayName: "Kod auto-rättning",
                permissionKey: "freja_tool_codex_run_and_fix_allowed"
            },
            "download_facebook_photos": {
                name: "download_facebook_photos",
                displayName: "Facebook Bildnedladdning",
                permissionKey: "freja_tool_download_facebook_photos_allowed"
            },
            "learn_topic": {
                name: "learn_topic",
                displayName: "Freja Inlärning",
                permissionKey: "freja_tool_learn_topic_allowed"
            },
            "get_learned_knowledge": {
                name: "get_learned_knowledge",
                displayName: "Sök i Kunskapsbank",
                permissionKey: "freja_tool_get_learned_knowledge_allowed"
            }
        };
        
        const tool = toolsMetadata[call.name];
        if (!tool) {
            this.writeLog(`ERROR: Tool '${call.name}' not recognized in systems`, "err");
            return { error: `Tool '${call.name}' not recognized.` };
        }
        
        // Helper to execute tool via backend API
        const executeBackendTool = async (name, args) => {
            const res = await fetch("/api/tools/execute", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name, args })
            });
            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.detail || `HTTP ${res.status}`);
            }
            const responseData = await res.json();
            
            // If the response contains a background task ID, start polling!
            if (responseData && responseData.task_id) {
                const taskId = responseData.task_id;
                this.writeLog(`BACKGROUND TASK INITIATED: ${taskId.substring(0, 8)}...`, "sys");
                
                if (name === "download_facebook_photos") {
                    this.pollFacebookDownloadProgress(taskId);
                    return {
                        status: "initiated",
                        task_id: taskId,
                        message: "Nedladdningen av Facebook-bilder har påbörjats i bakgrunden. Du kan följa förloppet i terminalen."
                    };
                }
                
                if (name === "learn_topic") {
                    this.pollLearningProgress(taskId);
                    return {
                        status: "initiated",
                        task_id: taskId,
                        message: `Inlärningsprocessen för "${args.topic}" har påbörjats i bakgrunden. Du kan följa förloppet i terminalen eller i Neural Learning Engine.`
                    };
                }
                
                // Polling loop
                const pollResult = await new Promise((resolve, reject) => {
                    const pollInterval = setInterval(async () => {
                        try {
                            const pollRes = await fetch(`/api/tools/status/${taskId}`);
                            if (!pollRes.ok) {
                                clearInterval(pollInterval);
                                reject(new Error(`Failed to poll status for task ${taskId}`));
                                return;
                            }
                            const statusData = await pollRes.json();
                            if (statusData.status === "success") {
                                clearInterval(pollInterval);
                                resolve(statusData.result);
                            } else if (statusData.status === "failed") {
                                clearInterval(pollInterval);
                                reject(new Error(statusData.error || "Background task failed."));
                            } else {
                                console.log(`[APP] Task ${taskId} is still processing...`);
                            }
                        } catch (err) {
                            clearInterval(pollInterval);
                            reject(err);
                        }
                    }, 2000);
                });
                
                // Dispatch calendar update event to reload dashboard UI if needed
                if (name === "manage_google_calendar" && args && args.action && args.action !== "list") {
                    console.log("[APP] Calendar modified. Dispatching freja-calendar-updated event.");
                    window.dispatchEvent(new Event('freja-calendar-updated'));
                }
                return pollResult;
            }
            
            // Dispatch calendar update event to reload dashboard UI if needed
            if (name === "manage_google_calendar" && args && args.action && args.action !== "list") {
                console.log("[APP] Calendar modified. Dispatching freja-calendar-updated event.");
                window.dispatchEvent(new Event('freja-calendar-updated'));
            }
            return responseData;
        };
        
        // Check permission (either true/false from localStorage)
        const isAllowed = localStorage.getItem(tool.permissionKey) === "true";
        
        if (isAllowed) {
            this.writeLog(`EXECUTING TOOL: ${tool.displayName}`, "sys");
            try {
                const result = await executeBackendTool(call.name, call.args);
                this.writeLog(`TOOL EXECUTION SUCCESS: ${tool.displayName}`, "sys");
                return result;
            } catch (err) {
                this.writeLog(`TOOL EXECUTION ERROR: ${err.message}`, "err");
                return { error: `Execution failed: ${err.message}` };
            }
        } else {
            // Permission is not granted, ask the user!
            this.writeLog(`PERMISSION REQUIRED FOR TOOL: ${tool.displayName}`, "warn");
            
            // We return a Promise that resolves when the user allows or denies
            const allowed = await new Promise((resolve) => {
                this.appendPermissionRequest(tool, call.args, resolve);
            });
            
            if (allowed) {
                this.writeLog(`EXECUTING TOOL POST-APPROVAL: ${tool.displayName}`, "sys");
                try {
                    const result = await executeBackendTool(call.name, call.args);
                    this.writeLog(`TOOL EXECUTION SUCCESS: ${tool.displayName}`, "sys");
                    return result;
                } catch (err) {
                    this.writeLog(`TOOL EXECUTION ERROR: ${err.message}`, "err");
                    return { error: `Execution failed: ${err.message}` };
                }
            } else {
                this.writeLog(`TOOL ACCESS DENIED BY USER: ${tool.displayName}`, "warn");
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
     * Polls the background learning progress for a task.
     */
    pollLearningProgress(taskId) {
        const self = this;
        const progressContainer = document.getElementById('learning-active-status');
        const stageLabel = document.getElementById('learning-status-stage');
        const percentLabel = document.getElementById('learning-status-percent');
        const progressBar = document.getElementById('learning-status-bar');
        const topicInput = document.getElementById('learning-input-topic');
        const btnStartLearning = document.getElementById('btn-start-learning');
        
        if (progressContainer) progressContainer.style.display = 'block';
        
        const pollInterval = setInterval(async () => {
            try {
                const res = await fetch(`/api/tools/status/${taskId}`);
                if (!res.ok) {
                    clearInterval(pollInterval);
                    return;
                }
                const data = await res.json();
                
                if (data.status === "processing") {
                    const pct = data.progress || 0;
                    const stage = data.stage || "Scrapar webbsidor...";
                    if (stageLabel) stageLabel.textContent = stage;
                    if (percentLabel) percentLabel.textContent = `${pct}%`;
                    if (progressBar) progressBar.style.width = `${pct}%`;
                } else if (data.status === "success") {
                    clearInterval(pollInterval);
                    self.writeLog("NEURAL LEARNING COMPLETED SUCCESSFULLY", "sys");
                    soundSynth.playNotify();
                    
                    if (stageLabel) stageLabel.textContent = "Klar!";
                    if (percentLabel) percentLabel.textContent = "100%";
                    if (progressBar) progressBar.style.width = "100%";
                    
                    setTimeout(() => {
                        if (progressContainer) progressContainer.style.display = 'none';
                        if (btnStartLearning) btnStartLearning.disabled = false;
                        if (topicInput) {
                            topicInput.disabled = false;
                            topicInput.value = "";
                        }
                        self.loadLearningVaultUI();
                    }, 2000);
                } else if (data.status === "cancelled") {
                    clearInterval(pollInterval);
                    self.writeLog("NEURAL LEARNING CANCELLED BY USER", "warn");
                    soundSynth.playError();
                    
                    if (stageLabel) stageLabel.textContent = "Avbruten.";
                    if (percentLabel) percentLabel.textContent = "0%";
                    if (progressBar) progressBar.style.width = "0%";
                    
                    setTimeout(() => {
                        if (progressContainer) progressContainer.style.display = 'none';
                        if (btnStartLearning) btnStartLearning.disabled = false;
                        if (topicInput) topicInput.disabled = false;
                    }, 2000);
                } else if (data.status === "failed") {
                    clearInterval(pollInterval);
                    self.writeLog(`NEURAL LEARNING FAILED: ${data.error}`, "err");
                    soundSynth.playError();
                    
                    if (stageLabel) stageLabel.textContent = `Fel: ${data.error}`;
                    
                    setTimeout(() => {
                        if (progressContainer) progressContainer.style.display = 'none';
                        if (btnStartLearning) btnStartLearning.disabled = false;
                        if (topicInput) topicInput.disabled = false;
                    }, 4000);
                }
            } catch (err) {
                console.error("Error polling learning status:", err);
            }
        }, 1500);
    }

    /**
     * Loads and renders stored credentials.
     */
    async loadCredentialsUI() {
        const credsList = document.getElementById('credentials-list');
        if (!credsList) return;
        
        try {
            const res = await fetch("/api/learning/credentials");
            if (!res.ok) throw new Error("Failed to load credentials");
            const data = await res.json();
            
            if (data.length === 0) {
                credsList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; padding: 5px;">Inga sparade inloggningar...</div>';
                return;
            }
            
            credsList.innerHTML = "";
            const self = this;
            data.forEach(cred => {
                const item = document.createElement('div');
                item.style.display = 'flex';
                item.style.justifyContent = 'space-between';
                item.style.alignItems = 'center';
                item.style.background = 'rgba(255,255,255,0.03)';
                item.style.padding = '4px 6px';
                item.style.borderRadius = '3px';
                item.style.border = '1px solid rgba(255,255,255,0.05)';
                item.style.marginBottom = '2px';
                
                item.innerHTML = `
                    <span style="color: var(--color-primary); flex: 1;">${cred.domain}</span>
                    <span style="color: var(--color-text-muted); margin-right: 10px;">${cred.username}</span>
                    <button class="btn-delete-cred hud-btn-icon" data-clean="${cred.clean_domain}" style="height: 18px; width: 18px; font-size: 9px; line-height: 18px; display: inline-flex; justify-content: center; align-items: center; border-color: rgba(255, 59, 48, 0.3); color: #ff3b30;" title="Radera inloggning">
                        <i class="fa-solid fa-trash-can"></i>
                    </button>
                `;
                
                item.querySelector('.btn-delete-cred').addEventListener('click', async (e) => {
                    const btn = e.currentTarget;
                    const cleanDomain = btn.getAttribute('data-clean');
                    if (confirm(`Vill du radera inloggningsuppgifter för domänen?`)) {
                        soundSynth.playClick();
                        try {
                            const delRes = await fetch(`/api/learning/credentials/${cleanDomain}`, { method: "DELETE" });
                            if (delRes.ok) {
                                self.writeLog(`CREDENTIALS DELETED`, "sys");
                                self.loadCredentialsUI();
                            }
                        } catch (err) {
                            console.error(err);
                        }
                    }
                });
                credsList.appendChild(item);
            });
        } catch (err) {
            console.error("Failed to load credentials UI:", err);
        }
    }

    /**
     * Loads and renders learned knowledge base.
     */
    async loadLearningVaultUI() {
        const vaultList = document.getElementById('knowledge-list');
        if (!vaultList) return;
        
        try {
            const res = await fetch("/api/learning/list");
            if (!res.ok) throw new Error("Failed to load learning list");
            const data = await res.json();
            
            if (data.length === 0) {
                vaultList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">Ingen sparad kunskap hittades...</div>';
                return;
            }
            
            vaultList.innerHTML = "";
            const self = this;
            data.forEach(entry => {
                const card = document.createElement('div');
                card.style.background = 'rgba(0, 0, 0, 0.25)';
                card.style.border = '1px solid var(--color-border)';
                card.style.borderRadius = '4px';
                card.style.padding = '10px';
                card.style.display = 'flex';
                card.style.flexDirection = 'column';
                card.style.gap = '8px';
                card.style.marginBottom = '8px';
                
                const sourcesMarkup = entry.sources.map(src => `<a href="${src.url}" target="_blank" style="color: var(--color-primary); text-decoration: underline; margin-right: 10px;">${src.title || src.url}</a>`).join(' ');
                
                card.innerHTML = `
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <h4 style="font-family: var(--font-display); font-size: 12px; color: var(--color-primary); margin: 0; text-transform: uppercase; letter-spacing: 0.5px;">${entry.topic}</h4>
                        <div style="display: flex; gap: 8px; align-items: center;">
                            <span style="font-family: var(--font-mono); font-size: 9px; color: var(--color-text-muted);">${entry.timestamp}</span>
                            <button class="btn-delete-knowledge hud-btn-icon" data-id="${entry.id}" style="height: 20px; width: 20px; font-size: 10px; border-color: rgba(255,59,48,0.3); color: #ff3b30;" title="Radera kunskap">
                                <i class="fa-solid fa-trash-can"></i>
                            </button>
                        </div>
                    </div>
                    <p style="font-family: var(--font-mono); font-size: 11px; margin: 0; line-height: 1.3; color: var(--color-text-muted);">${self.escapeHTML(entry.summary)}</p>
                    
                    <button class="btn-toggle-notes hud-btn btn-secondary" style="height: 22px; font-family: var(--font-display); font-size: 9px; padding: 0 8px; align-self: flex-start;">VISA DETALJERADE ANTECKNINGAR</button>
                    
                    <div class="detailed-notes-container" style="display: none; background: rgba(0,0,0,0.3); border: 1px solid rgba(255,255,255,0.05); padding: 10px; border-radius: 3px; font-family: var(--font-mono); font-size: 11px; line-height: 1.4; color: #eceff1; max-height: 250px; overflow-y: auto; margin-top: 5px;">
                        ${window.FrejaMarkdown ? window.FrejaMarkdown.parseMarkdown(entry.detailed_notes) : entry.detailed_notes}
                        
                        <div style="margin-top: 10px; border-top: 1px dashed rgba(255,255,255,0.1); padding-top: 8px; font-size: 10px; color: var(--color-text-muted);">
                            <strong>Källor:</strong> ${sourcesMarkup || 'Inga källor angivna'}
                        </div>
                    </div>
                `;
                
                // Toggle details handler
                const btnToggle = card.querySelector('.btn-toggle-notes');
                const detailsContainer = card.querySelector('.detailed-notes-container');
                btnToggle.addEventListener('click', () => {
                    soundSynth.playClick();
                    if (detailsContainer.style.display === 'none') {
                        detailsContainer.style.display = 'block';
                        btnToggle.textContent = 'DÖLJ DETALJERADE ANTECKNINGAR';
                    } else {
                        detailsContainer.style.display = 'none';
                        btnToggle.textContent = 'VISA DETALJERADE ANTECKNINGAR';
                    }
                });
                
                // Delete handler
                card.querySelector('.btn-delete-knowledge').addEventListener('click', async (e) => {
                    const knowledgeId = e.currentTarget.getAttribute('data-id');
                    if (confirm(`Är du säker på att du vill radera all sparad kunskap om "${entry.topic}"?`)) {
                        soundSynth.playClick();
                        try {
                            const delRes = await fetch(`/api/learning/delete/${knowledgeId}`, { method: "DELETE" });
                            if (delRes.ok) {
                                self.writeLog(`KNOWLEDGE ENTRY DELETED: ${entry.topic}`, "sys");
                                self.loadLearningVaultUI();
                            }
                        } catch (err) {
                            console.error(err);
                        }
                    }
                });
                
                vaultList.appendChild(card);
            });
        } catch (err) {
            console.error("Failed to load learning vault UI:", err);
        }
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
}

// Instantiates the UI controller once the DOM elements have loaded successfully
window.addEventListener('DOMContentLoaded', () => {
    window.uiController = new FrejaUIController();
});
