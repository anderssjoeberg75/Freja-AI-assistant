/**
 * F.R.E.J.A. UI Controller - Tools & Background Task Monitors Module
 */

/**
 * Human-readable labels for the permission gateway. Purely cosmetic: a tool missing from
 * here just gets a humanized version of its registry name. This map must NOT decide which
 * tools may run - it used to, as a hand-maintained whitelist, and every tool added to the
 * backend registry without a matching entry here was refused client-side as an unknown
 * tool (get_trainer_workouts and the Instagram tools all died that way).
 * The set of callable tools now comes from /api/tools/metadata, i.e. the registry itself.
 */
const TOOL_DISPLAY_NAMES = {
    "get_weather": "Weather forecast",
    "google_search": "Google search",
    "get_garmin_health": "Garmin health data",
    "get_withings_health": "Withings health data",
    "get_strava_data": "Strava activities",
    "get_strava_activity_analysis": "Strava activity analysis",
    "get_strava_athlete_stats": "Strava athlete statistics",
    "get_personal_trainer_advice": "Personal trainer advice",
    "get_trainer_workouts": "Scheduled training sessions",
    "update_trainer_workout": "Adjust a training session",
    "manage_google_calendar": "Google Calendar",
    "execute_codex_code": "Code execution",
    "run_code": "Code execution",
    "codex_git_ops": "Git operations",
    "codex_audit_codebase": "Code audit",
    "tool_analyze_code": "Code audit",
    "codex_run_and_fix": "Automatic code repair",
    "publish_instagram_post": "Publish Instagram post",
    "get_instagram_feed": "Instagram feed",
    "get_instagram_post_comments": "Instagram comments",
    "reply_to_instagram_comment": "Reply to Instagram comment",
    "learn_topic": "Freja learning",
    "get_learned_knowledge": "Search the knowledge base",
    "system_update": "System update from GitHub",
    "read_project_file": "Read project file",
    "run_windows_command": "Windows automation"
};

/** "get_trainer_workouts" -> "Get trainer workouts" - fallback label for unlabelled tools. */
function humanizeToolName(name) {
    const words = String(name).replace(/_/g, " ").trim();
    return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Builds the gateway's view of a tool from its registry name and permission key. */
function buildToolMetadata(name, permissionKey) {
    return {
        name: name,
        displayName: TOOL_DISPLAY_NAMES[name] || humanizeToolName(name),
        // Every registered tool declares a permission_key server-side; the derived
        // fallback only matters if metadata could not be fetched at all.
        permissionKey: permissionKey || `freja_tool_${name}_allowed`
    };
}

/**
 * Fetches (and caches for the session) the registry's tool list. Failures are not cached,
 * so a transient network error doesn't permanently degrade the gateway to fallback labels.
 */
FrejaUIController.prototype.loadToolsMetadata = function() {
    if (this._toolsMetadataPromise) return this._toolsMetadataPromise;

    const promise = (async () => {
        const res = await fetch("/api/tools/metadata");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const list = await res.json();
        const map = {};
        (list || []).forEach((entry) => {
            if (entry && entry.name) {
                map[entry.name] = buildToolMetadata(entry.name, entry.permission_key);
            }
        });
        return map;
    })();

    promise.catch(() => { this._toolsMetadataPromise = null; });
    this._toolsMetadataPromise = promise;
    return promise;
};

FrejaUIController.prototype.handleToolCall = async function(call) {
    this.writeLog(`TOOL CALL REQUESTED: ${call.name}`, "sys");

    let toolsMetadata = {};
    try {
        toolsMetadata = await this.loadToolsMetadata();
    } catch (err) {
        this.writeLog(`TOOL METADATA UNAVAILABLE: ${err.message}. Using derived permissions.`, "warn");
    }

    // Unknown names are no longer refused here. The backend is the authority on which
    // tools exist and which are authorized (is_tool_execution_authorized), and it answers
    // an unregistered name with a clear error - refusing client-side only hid tools the
    // backend was perfectly able to run.
    const tool = toolsMetadata[call.name] || buildToolMetadata(call.name, null);

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
