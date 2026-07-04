/**
 * F.R.E.J.A. UI Controller - UI Initialization Module
 */
FrejaUIController.prototype.initializeUI = function() {
    const accessToken = localStorage.getItem("freja_access_token") || "";
    const inputAccessToken = document.getElementById('input-access-token');
    if (inputAccessToken) inputAccessToken.value = accessToken;

    const backendUrl = localStorage.getItem("freja_backend_url") || (window.location.port === '5000' ? (window.location.protocol + '//' + window.location.hostname + ':8000') : "");
    const inputBackendUrl = document.getElementById('input-backend-url');
    if (inputBackendUrl) inputBackendUrl.value = backendUrl;

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
    if (this.gemini && typeof this.gemini.loadApiKey === 'function') {
        this.gemini.loadApiKey();
    }

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
                const backendBase = (localStorage.getItem('freja_backend_url') || '').replace(/\/$/, '') || window.location.origin;
                const redirectUri = backendBase + '/api/strava/callback';
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
    
    // Dynamically build and update Google Calendar authorize link
    const updateGoogleLink = () => {
        const clientId = inputGoogleClientId ? inputGoogleClientId.value.trim() : "";
        const authLink = document.getElementById('lnk-google-calendar-authorize');
        if (authLink) {
            if (clientId) {
                authLink.style.display = 'flex';
            } else {
                authLink.style.display = 'none';
            }
        }
    };
    if (inputGoogleClientId) {
        inputGoogleClientId.addEventListener('input', updateGoogleLink);
    }
    updateGoogleLink();

    const lnkGoogleAuthorize = document.getElementById('lnk-google-calendar-authorize');
    if (lnkGoogleAuthorize) {
        lnkGoogleAuthorize.addEventListener('click', async (e) => {
            e.preventDefault();
            soundSynth.playClick();
            
            const clientId = inputGoogleClientId.value.trim();
            if (!clientId) return;
            
            // Generate PKCE code verifier and code challenge
            const verifier = generateCodeVerifier();
            localStorage.setItem("google_code_verifier", verifier);
            const challenge = await generateCodeChallenge(verifier);
            
            // Save client ID to localStorage so it is preserved when we redirect back
            localStorage.setItem("freja_google_calendar_client_id", clientId);
            
            // Also save it to server right away so it is recorded
            await this.saveKeysToServer({
                freja_google_calendar_client_id: clientId
            });
            
            const backendBase = (localStorage.getItem('freja_backend_url') || '').replace(/\/$/, '') || window.location.origin;
            const redirectUri = backendBase + '/api/google_calendar/callback';
            const scope = 'https://www.googleapis.com/auth/calendar';
            
            const oauthUrl = `https://accounts.google.com/o/oauth2/v2/auth` +
                `?response_type=code` +
                `&client_id=${encodeURIComponent(clientId)}` +
                `&redirect_uri=${encodeURIComponent(redirectUri)}` +
                `&scope=${encodeURIComponent(scope)}` +
                `&code_challenge=${encodeURIComponent(challenge)}` +
                `&code_challenge_method=S256` +
                `&access_type=offline` +
                `&prompt=consent` +
                `&state=${encodeURIComponent(window.location.origin)}`;
            
            window.location.href = oauthUrl;
        });
    }
    
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
};
