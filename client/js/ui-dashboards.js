/**
 * F.R.E.J.A. UI Controller - Dashboards & Loaders Module
 */

FrejaUIController.prototype.loadMemoryVaultUI = async function() {
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
    
    memoriesList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">Loading memory engrams...</div>';
    
    try {
        const memories = await this.memory.getAllMemories();
        memoryCount.textContent = memories.length;
        
        if (memories.length === 0) {
            memoriesList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[NO MEMORY FRAGMENTS DETECTED]</div>';
            return;
        }
        
        memoriesList.innerHTML = "";
        memories.forEach(m => {
            const card = document.createElement('div');
            card.className = "memory-engram-card";
            
            card.innerHTML = `
                <div class="memory-engram-text">${this.escapeHTML(m.memory)}</div>
                <button class="memory-engram-delete-btn" data-id="${m.id}" title="Delete this engram">
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
                        memoriesList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[NO MEMORY FRAGMENTS DETECTED]</div>';
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
};

FrejaUIController.prototype.loadTelegramDashboardUI = async function() {
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
        telegramList.innerHTML = '<div style="color: #ff3b30; text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[ERROR FETCHING STATUS]</div>';
    }
};

FrejaUIController.prototype.loadTrainerDashboardUI = async function() {
    // Reflect the persisted auto-adjust preference in the settings toggle.
    this.loadTrainerSettings();

    const trainerList = document.getElementById('trainer-list');
    if (!trainerList) return;

    trainerList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">Loading history...</div>';
    
    try {
        const res = await fetch('/api/trainer/plans?limit=10');
        if (!res.ok) throw new Error(`HTTP error ${res.status}`);
        
        const plans = await res.json();
        if (plans.length === 0) {
            trainerList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[NO PREVIOUS PLANS FOUND]</div>';
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
                    <button class="trainer-delete-btn" data-id="${plan.id}" title="Delete log" style="background: transparent; border: none; color: #ff3b30; cursor: pointer; padding: 2px 4px;">
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
                if (!confirm("Really delete this training plan?")) return;
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
        trainerList.innerHTML = '<div style="color: #ff3b30; text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[ERROR FETCHING HISTORY]</div>';
    }
};

FrejaUIController.prototype.runTrainerCheckin = async function() {
    const btn = document.getElementById('btn-trainer-checkin');
    const out = document.getElementById('trainer-checkin-output');
    if (!out) return;

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> CHECKAR IN...';
    }
    out.style.display = 'block';
    out.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 16px;">Reading last night&#39;s health data (Garmin / Withings)...</div>';
    this.writeLog("RUNNING DAILY TRAINER CHECK-IN...", "sys");

    try {
        const res = await fetch('/api/trainer/checkin', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }

        const data = await res.json();
        const checkin = data.checkin || {};
        const briefing = checkin.briefing || 'Ingen briefing genererades.';
        const adh = data.adherence || {};

        const badgeStyle = "font-size: 10px; font-family: var(--font-mono); background: rgba(0,242,254,0.1); border: 1px solid rgba(0,242,254,0.2); color: var(--color-primary); border-radius: 3px; padding: 3px 8px;";
        let badges = '';
        if (data.calendar_updated) {
            badges += `<span style="${badgeStyle}">✅ Calendar updated</span>`;
        }
        if (adh.adherence_pct !== null && adh.adherence_pct !== undefined) {
            badges += `<span style="${badgeStyle}">📊 Adherence ${adh.adherence_pct}% (${adh.completed}/${adh.planned})</span>`;
        }

        out.innerHTML = `
            <div class="trainer-briefing">${window.FrejaMarkdown.parseMarkdown(briefing)}</div>
            ${badges ? `<div style="display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px;">${badges}</div>` : ''}
        `;

        soundSynth.playNotify();
        this.writeLog("DAILY CHECK-IN COMPLETE", "sys");

        // If the coach re-timed today's workout, refresh the history/plan view.
        if (data.calendar_updated) {
            this.loadTrainerDashboardUI();
        }
    } catch (e) {
        out.innerHTML = `<div style="color: #ff3b30; font-family: var(--font-mono); font-size: 11px; padding: 12px;">[CHECK-IN FAILED] ${e.message}</div>`;
        soundSynth.playError();
        this.writeLog(`CHECK-IN ERROR: ${e.message}`, "err");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i class="fa-solid fa-heart-pulse"></i> CHECKA IN';
        }
    }
};

FrejaUIController.prototype.loadTrainerSettings = async function() {
    const chk = document.getElementById('chk-trainer-auto-adjust');
    if (!chk) return;
    try {
        const res = await fetch('/api/trainer/profile');
        if (!res.ok) return;
        const profile = await res.json();
        // Default ON: only an explicit 0/false disables automatic adjustment.
        const val = profile.auto_adjust;
        chk.checked = !(val === 0 || val === '0' || val === false);
    } catch (e) {
        console.error('[TRAINER] Failed to load settings:', e);
    }
};

FrejaUIController.prototype.saveTrainerAutoAdjust = async function(enabled) {
    try {
        await fetch('/api/trainer/profile', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ auto_adjust: enabled ? 1 : 0 })
        });
        this.writeLog(`PT AUTO-ADJUST ${enabled ? 'ENABLED' : 'DISABLED'}`, "sys");
    } catch (e) {
        this.writeLog(`PT SETTINGS ERROR: ${e.message}`, "err");
    }
};

FrejaUIController.prototype.runTrainerOptimize = async function() {
    const btn = document.getElementById('btn-trainer-optimize');
    const out = document.getElementById('trainer-optimize-output');
    if (!out) return;

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> OPTIMERAR...';
    }
    out.style.display = 'block';
    out.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 16px;">Reading recovery data and reviewing upcoming sessions...</div>';
    this.writeLog("OPTIMIZING UPCOMING WORKOUTS FROM RECOVERY DATA...", "sys");

    try {
        const res = await fetch('/api/trainer/optimize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }

        const data = await res.json();
        const briefing = data.briefing || 'Ingen sammanfattning genererades.';
        const changes = data.changes || [];

        const badgeStyle = "font-size: 10px; font-family: var(--font-mono); background: rgba(0,242,254,0.1); border: 1px solid rgba(0,242,254,0.2); color: var(--color-primary); border-radius: 3px; padding: 3px 8px;";
        let badges = `<span style="${badgeStyle}">🔍 ${data.considered || 0} pass granskade</span>`;
        badges += `<span style="${badgeStyle}">${data.changes_count ? '✅' : '➖'} ${data.changes_count || 0} adjusted</span>`;

        let changeList = '';
        if (changes.length) {
            changeList = '<ul style="margin: 10px 0 0; padding-left: 18px; font-size: 12px; color: var(--color-text-muted);">' +
                changes.map(c => `<li><strong>${c.date}</strong>: ${c.from_minutes}→${c.to_minutes} min – ${c.reason || c.title}</li>`).join('') +
                '</ul>';
        }

        out.innerHTML = `
            <div class="trainer-briefing">${window.FrejaMarkdown.parseMarkdown(briefing)}</div>
            <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px;">${badges}</div>
            ${changeList}
        `;

        soundSynth.playNotify();
        this.writeLog(`WORKOUT OPTIMIZATION COMPLETE (${data.changes_count || 0} adjusted)`, "sys");
    } catch (e) {
        out.innerHTML = `<div style="color: #ff3b30; font-family: var(--font-mono); font-size: 11px; padding: 12px;">[OPTIMIZATION FAILED] ${e.message}</div>`;
        soundSynth.playError();
        this.writeLog(`OPTIMIZATION ERROR: ${e.message}`, "err");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i class="fa-solid fa-wand-sparkles"></i> OPTIMERA KOMMANDE PASS NU';
        }
    }
};

FrejaUIController.prototype.renderTrainerPlanDetails = function(planId, adviceText) {
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
    const weekly_focus = planData.weekly_focus || "General training";
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
        // activity_type comes from the generated plan and is Swedish (see the trainer
        // response schema), so these keyword tests match Swedish words. English variants are
        // included for plans that predate that schema.
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
                <div style="font-size: 10px; color: var(--color-primary); font-family: var(--font-display); margin-bottom: 8px; letter-spacing: 0.5px;">THIS WEEK&#39;S WORKOUTS</div>
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
                alert("Enter a start date for the training week.");
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
};

FrejaUIController.prototype.loadGarminDashboardUI = async function() {
    const garminList = document.getElementById('garmin-list');
    if (!garminList) return;
    
    // Set date input default to today if empty
    const dateInput = document.getElementById('garmin-input-date');
    if (dateInput && !dateInput.value) {
        const today = new Date().toISOString().split('T')[0];
        dateInput.value = today;
    }

    garminList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">Loading history...</div>';
    
    try {
        const res = await fetch('/api/garmin/data?days=10');
        if (!res.ok) throw new Error(`HTTP error ${res.status}`);
        
        const logs = await res.json();
        if (logs.length === 0) {
            garminList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[NO HEALTH LOGS FOUND]</div>';
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
            
            // "Ingen" is the Swedish placeholder the backend substitutes for a null workout_type.
            const workoutInfo = log.workout_type && log.workout_type !== "Ingen" 
                ? ` | ${log.workout_type} (${log.workout_duration}m)` 
                : "";
            const bbInfo = log.body_battery ? ` | BB: ${log.body_battery}` : "";
            const hrvInfo = log.hrv ? ` | HRV: ${log.hrv}ms` : "";
            
            item.innerHTML = `
                <div style="flex: 1; color: var(--color-text-bright);">
                    <span style="color: var(--color-primary);">${log.date}</span>: ${log.steps} steps | ${log.sleep_hours}h sleep | ${log.resting_hr} bpm | ${log.active_calories} kcal${workoutInfo}${bbInfo}${hrvInfo}
                </div>
                <button class="garmin-delete-btn" data-date="${log.date}" title="Delete log" style="background: transparent; border: none; color: #ff3b30; cursor: pointer; padding: 2px 6px;">
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
                            garminList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[NO HEALTH LOGS FOUND]</div>';
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
        garminList.innerHTML = '<div style="color: #ff3b30; text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[ERROR LOADING HISTORY]</div>';
    }
};

FrejaUIController.prototype.loadStravaDashboardUI = async function() {
    const stravaList = document.getElementById('strava-list');
    if (!stravaList) return;
    
    // Set date input default to today if empty
    const dateInput = document.getElementById('strava-input-date');
    if (dateInput && !dateInput.value) {
        const today = new Date().toISOString().split('T')[0];
        dateInput.value = today;
    }

    stravaList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">Loading history...</div>';
    
    try {
        const res = await fetch('/api/strava/data?days=15');
        if (!res.ok) throw new Error(`HTTP error ${res.status}`);
        
        const logs = await res.json();
        if (logs.length === 0) {
            stravaList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[NO WORKOUTS FOUND]</div>';
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
                <button class="strava-delete-btn" data-id="${log.id}" title="Delete activity" style="background: transparent; border: none; color: #ff3b30; cursor: pointer; padding: 2px 6px;">
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
                            stravaList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[NO WORKOUTS FOUND]</div>';
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
        stravaList.innerHTML = '<div style="color: #ff3b30; text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[ERROR LOADING HISTORY]</div>';
    }
};

FrejaUIController.prototype.loadWithingsDashboardUI = async function() {
    const withingsList = document.getElementById('withings-list');
    if (!withingsList) return;
    
    // Set date input default to today if empty
    const dateInput = document.getElementById('withings-input-date');
    if (dateInput && !dateInput.value) {
        const today = new Date().toISOString().split('T')[0];
        dateInput.value = today;
    }

    withingsList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">Loading measurements...</div>';
    
    try {
        const res = await fetch('/api/withings/data?days=15');
        if (!res.ok) throw new Error(`HTTP error ${res.status}`);
        
        const logs = await res.json();
        if (logs.length === 0) {
            withingsList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[NO MEASUREMENTS FOUND]</div>';
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
                    <span style="color: var(--color-primary);">${log.date}</span>: <strong style="color: var(--color-accent);">Measurement</strong> - ${weight}${fat}${bone}${pulse}
                </div>
                <button class="withings-delete-btn" data-date="${log.date}" title="Delete measurement" style="background: transparent; border: none; color: #ff3b30; cursor: pointer; padding: 2px 6px;">
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
                            withingsList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[NO MEASUREMENTS FOUND]</div>';
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
        withingsList.innerHTML = '<div style="color: #ff3b30; text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[ERROR LOADING HISTORY]</div>';
    }
};

FrejaUIController.prototype.loadGoogleCalendarDashboardUI = async function() {
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

    calendarList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">Loading the calendar...</div>';
    
    try {
        const res = await fetch('/api/google_calendar/data?days=30');
        if (!res.ok) throw new Error(`HTTP error ${res.status}`);
        
        const events = await res.json();
        if (events.length === 0) {
            calendarList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[NO EVENTS FOUND]</div>';
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
                    <button class="calendar-edit-btn" title="Edit event" style="background: transparent; border: none; color: var(--color-primary); cursor: pointer; padding: 2px 6px;">
                        <i class="fa-solid fa-pencil"></i>
                    </button>
                    <button class="calendar-delete-btn" title="Delete event" style="background: transparent; border: none; color: #ff3b30; cursor: pointer; padding: 2px 6px;">
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
                if (btnSave) btnSave.innerHTML = `<i class="fa-solid fa-floppy-disk"></i> SAVE CHANGES`;
                
                const btnCancel = document.getElementById('btn-cancel-google-calendar-edit');
                if (btnCancel) btnCancel.style.display = "block";
            });
            
            // Bind Delete Action
            const delBtn = item.querySelector('.calendar-delete-btn');
            delBtn.addEventListener('click', async () => {
                if (!confirm(`Really delete the event "${evt.summary}"?`)) return;
                soundSynth.playClick();
                item.style.opacity = '0.5';
                try {
                    const delRes = await fetch(`/api/google_calendar/delete?id=${evt.id}`);
                    const delData = await delRes.json();
                    if (delRes.ok && delData.status === 'success') {
                        this.writeLog(`CALENDAR EVENT "${evt.summary}" REMOVED`, "sys");
                        item.remove();
                        if (calendarList.children.length === 0) {
                            calendarList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[NO EVENTS FOUND]</div>';
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
        calendarList.innerHTML = '<div style="color: #ff3b30; text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[ERROR LOADING THE CALENDAR]</div>';
    }
};

FrejaUIController.prototype.pollSyncStatus = async function(provider) {
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
                            ? `<i class="fa-solid fa-arrows-rotate"></i> SYNC CALENDAR`
                            : `<i class="fa-solid fa-arrows-rotate"></i> SYNC DEVICE`;
                    }
                    if (btnAll) {
                        btnAll.disabled = false;
                        btnAll.innerHTML = `<i class="fa-solid fa-clock-rotate-left"></i> FETCH ALL HISTORY`;
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
                            ? `<i class="fa-solid fa-arrows-rotate"></i> SYNC CALENDAR`
                            : `<i class="fa-solid fa-arrows-rotate"></i> SYNC DEVICE`;
                    }
                    if (btnAll) {
                        btnAll.disabled = false;
                        btnAll.innerHTML = `<i class="fa-solid fa-clock-rotate-left"></i> FETCH ALL HISTORY`;
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
};

FrejaUIController.prototype.loadCredentialsUI = async function() {
    const credsList = document.getElementById('credentials-list');
    if (!credsList) return;
    
    try {
        const res = await fetch("/api/learning/credentials");
        if (!res.ok) throw new Error("Failed to load credentials");
        const data = await res.json();
        
        if (data.length === 0) {
            credsList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; padding: 5px;">No saved logins...</div>';
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
                if (confirm(`Delete the stored credentials for this domain?`)) {
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
};

FrejaUIController.prototype.loadLearningVaultUI = async function() {
    const vaultList = document.getElementById('knowledge-list');
    if (!vaultList) return;
    
    try {
        const res = await fetch("/api/learning/list");
        if (!res.ok) throw new Error("Failed to load learning list");
        const data = await res.json();
        
        if (data.length === 0) {
            vaultList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">No stored knowledge found...</div>';
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
                        <strong>Sources:</strong> ${sourcesMarkup || 'No sources given'}
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
                    btnToggle.textContent = 'HIDE DETAILED NOTES';
                } else {
                    detailsContainer.style.display = 'none';
                    btnToggle.textContent = 'VISA DETALJERADE ANTECKNINGAR';
                }
            });
            
            // Delete handler
            card.querySelector('.btn-delete-knowledge').addEventListener('click', async (e) => {
                const knowledgeId = e.currentTarget.getAttribute('data-id');
                if (confirm(`Are you sure you want to delete all stored knowledge about "${entry.topic}"?`)) {
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
};
