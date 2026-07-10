/**
 * F.R.E.J.A. UI Controller - Tools & Background Task Monitors Module
 */

FrejaUIController.prototype.handleToolCall = async function(call) {
    this.writeLog(`TOOL CALL REQUESTED: ${call.name}`, "sys");
    
    const toolsMetadata = {
        "get_weather": {
            name: "get_weather",
            displayName: "Weather forecast",
            permissionKey: "freja_tool_get_weather_allowed"
        },
        "google_search": {
            name: "google_search",
            displayName: "Google search",
            permissionKey: "freja_tool_google_search_allowed"
        },
        "get_garmin_health": {
            name: "get_garmin_health",
            displayName: "Garmin health data",
            permissionKey: "freja_tool_get_garmin_health_allowed"
        },
        "get_withings_health": {
            name: "get_withings_health",
            displayName: "Withings health data",
            permissionKey: "freja_tool_get_withings_health_allowed"
        },
        "get_strava_data": {
            name: "get_strava_data",
            displayName: "Strava activities",
            permissionKey: "freja_tool_get_strava_data_allowed"
        },
        "get_strava_activity_analysis": {
            name: "get_strava_activity_analysis",
            displayName: "Strava activity analysis",
            permissionKey: "freja_tool_get_strava_activity_analysis_allowed"
        },
        "get_strava_athlete_stats": {
            name: "get_strava_athlete_stats",
            displayName: "Strava athlete statistics",
            permissionKey: "freja_tool_get_strava_athlete_stats_allowed"
        },
        "manage_google_calendar": {
            name: "manage_google_calendar",
            displayName: "Google Calendar",
            permissionKey: "freja_tool_manage_google_calendar_allowed"
        },
        "execute_codex_code": {
            name: "execute_codex_code",
            displayName: "Code execution",
            permissionKey: "freja_tool_execute_codex_code_allowed"
        },
        "run_code": {
            name: "run_code",
            displayName: "Code execution",
            permissionKey: "freja_tool_run_code_allowed"
        },
        "codex_git_ops": {
            name: "codex_git_ops",
            displayName: "Git operations",
            permissionKey: "freja_tool_codex_git_ops_allowed"
        },
        "codex_audit_codebase": {
            name: "codex_audit_codebase",
            displayName: "Code audit",
            permissionKey: "freja_tool_codex_audit_codebase_allowed"
        },
        "tool_analyze_code": {
            name: "tool_analyze_code",
            displayName: "Code audit",
            permissionKey: "freja_tool_tool_analyze_code_allowed"
        },
        "codex_run_and_fix": {
            name: "codex_run_and_fix",
            displayName: "Automatic code repair",
            permissionKey: "freja_tool_codex_run_and_fix_allowed"
        },
        "download_facebook_photos": {
            name: "download_facebook_photos",
            displayName: "Facebook photo download",
            permissionKey: "freja_tool_download_facebook_photos_allowed"
        },
        "learn_topic": {
            name: "learn_topic",
            displayName: "Freja learning",
            permissionKey: "freja_tool_learn_topic_allowed"
        },
        "get_learned_knowledge": {
            name: "get_learned_knowledge",
            displayName: "Search the knowledge base",
            permissionKey: "freja_tool_get_learned_knowledge_allowed"
        },
        "system_update": {
            name: "system_update",
            displayName: "System update from GitHub",
            permissionKey: "freja_tool_system_update_allowed"
        },
        "read_project_file": {
            name: "read_project_file",
            displayName: "Read project file",
            permissionKey: "freja_tool_read_project_file_allowed"
        },
        "run_windows_command": {
            name: "run_windows_command",
            displayName: "Windows automation",
            permissionKey: "freja_tool_run_windows_command_allowed"
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
                    message: "The Facebook photo download has started in the background. Progress can be followed in the terminal."
                };
            }
            
            if (name === "learn_topic") {
                this.pollLearningProgress(taskId);
                return {
                    status: "initiated",
                    task_id: taskId,
                    message: `The learning process for "${args.topic}" has started in the background. Progress can be followed in the terminal or in the Neural Learning Engine.`
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
    
    // `git push` publishes to a remote and is hard to reverse, unlike the other
    // codex_git_ops actions (status/log/clone/checkout/commit stay local). It must
    // always be re-confirmed, even if the user previously chose "Allow always" for
    // Git operations in general. The backend enforces this independently too
    // (see is_tool_execution_authorized in backend/routes/tools.py) since the client
    // check is only a UX convenience, not the authority.
    const isGitPush = call.name === "codex_git_ops" && (call.args?.action || "").toLowerCase() === "push";

    // Check permission (either true/false from localStorage)
    const isAllowed = !isGitPush && localStorage.getItem(tool.permissionKey) === "true";

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
            this.appendPermissionRequest(tool, call.args, resolve, isGitPush);
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
};

FrejaUIController.prototype.appendPermissionRequest = function(tool, args, resolvePromise, isGitPush) {
    const chatHistory = document.getElementById('chat-history');
    const msgDiv = document.createElement('div');
    msgDiv.className = 'chat-msg system-msg permission-request-msg';

    const argsStr = JSON.stringify(args, null, 2);

    const warningText = isGitPush
        ? `FREJA wants to run <strong>git push</strong> and publish local commits to the remote. This is not easily reversible and always requires confirmation, regardless of earlier settings for Git operations.`
        : `FREJA is requesting access to the <strong>${tool.displayName || tool.name}</strong> tool in order to complete your request.`;

    // git push can never be permanently allowed from this dialog - only "allow once" or "deny".
    const allowAlwaysButton = isGitPush ? '' :
        `<button class="hud-btn btn-secondary btn-allow-always" style="font-size: 10px; padding: 4px 10px;">Allow always</button>`;

    msgDiv.innerHTML = `
        <div class="msg-sender">[SECURITY GATEWAY]</div>
        <div class="msg-content glass-morphic" style="border-color: #fdd663; padding: 12px; margin-top: 5px; background: rgba(25, 20, 10, 0.45);">
            <h4 style="color: #fdd663; margin-top: 0; font-family: var(--font-display); font-size: 11px; letter-spacing: 1px;">
                <i class="fa-solid fa-shield-halved"></i> PERMISSION REQUEST REQUIRED
            </h4>
            <p style="font-size: 11px; margin: 6px 0; line-height: 1.4; color: #f8f9fa;">
                ${warningText}
            </p>
            <div style="background: rgba(0,0,0,0.6); border: 1px solid rgba(253, 214, 99, 0.2); border-radius: 4px; padding: 6px; font-family: var(--font-mono); font-size: 10px; color: #fdd663; margin-bottom: 10px; white-space: pre-wrap;">Arguments: ${argsStr}</div>
            <div style="display: flex; gap: 8px;">
                <button class="hud-btn btn-primary btn-allow-once" style="background: #fdd663; border-color: #fdd663; color: #000; font-size: 10px; padding: 4px 10px;">Allow once</button>
                ${allowAlwaysButton}
                <button class="hud-btn btn-secondary btn-deny" style="border-color: #ff3b30; color: #ff3b30; font-size: 10px; padding: 4px 10px;">Deny</button>
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

    btnAllowOnce.addEventListener('click', async () => {
        soundSynth.playClick();
        msgDiv.remove();
        try {
            // Register a short-lived server-side grant so the backend (the authoritative
            // enforcement point) allows this single upcoming /api/tools/execute call.
            // Args are included so the backend can namespace the grant per git action
            // (a grant for `git log` must not also authorize a subsequent `git push`).
            await fetch('/api/tools/grant_once', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: tool.name, args })
            });
        } catch (err) {
            console.error('Failed to register one-time tool grant:', err);
        }
        self.writeLog(`TOOL PERMISSION GRANTED: ${tool.name} (ONCE)`, "sys");
        resolvePromise(true);
    });

    if (btnAllowAlways) btnAllowAlways.addEventListener('click', async () => {
        soundSynth.playClick();
        msgDiv.remove();
        // Save always allowed locally (fast UI read) and persist server-side, since the
        // backend is the authoritative enforcement point for /api/tools/execute.
        localStorage.setItem(tool.permissionKey, "true");
        try {
            await fetch('/api/keys', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ [tool.permissionKey]: "true" })
            });
        } catch (err) {
            console.error('Failed to persist tool permission to server:', err);
        }
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
};

FrejaUIController.prototype.pollFacebookDownloadProgress = async function(taskId) {
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
                
                // Freja speaks to the user, so this text stays Swedish (as do the
                // appendChatMessage assistant lines below). Only the writeLog diagnostics
                // and the permission-gateway UI above are English.
                if (self.speech && self.speech.autoSpeak) {
                    self.speech.speak(`Nedladdningen av Facebook-bilder är klar. Hämtade ${statusData.result.downloaded_count} bilder.`);
                }
                
                self.appendChatMessage("assistant", `**[SYSTEMMEDDELANDE]** Nedladdningen av Facebook-bilder är klar! Totalt hämtades ${statusData.result.downloaded_count} bilder.`, false);
                
            } else if (statusData.status === "failed") {
                clearInterval(self.facebookDownloadInterval);
                self.facebookDownloadInterval = null;
                
                self.writeLog(`[FACEBOOK DOWNLOAD] FAILED: ${statusData.error || "Unknown error"}`, "err");
                soundSynth.playError();
                
                if (self.speech && self.speech.autoSpeak) {
                    self.speech.speak(`Nedladdningen av Facebook-bilder misslyckades.`);
                }
                
                self.appendChatMessage("assistant", `**[SYSTEMMEDDELANDE - FEL]** Nedladdningen av Facebook-bilder misslyckades: ${statusData.error || "okänt fel"}`, false);
                
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
};

FrejaUIController.prototype.pollLearningProgress = function(taskId) {
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
                
                if (stageLabel) stageLabel.textContent = "Cancelled.";
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
};
