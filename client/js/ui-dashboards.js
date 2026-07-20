/**
 * F.R.E.J.A. UI Controller - Dashboards & Loaders Module
 */

FrejaUIController.prototype.loadMemoryVaultUI = async function () {
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

FrejaUIController.prototype.loadTelegramDashboardUI = async function () {
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

FrejaUIController.prototype.loadTrainerDashboardUI = async function () {
    // Reflect the persisted auto-adjust preference in the settings toggle.
    this.loadTrainerSettings();
    // Populate the onboarding profile form and the strength-log history.
    this.loadTrainerProfileUI();
    this.loadStrengthLogsUI();
    // Injury/pain log and the trend & adherence charts.
    this.loadInjuryLogUI();
    this.loadTrainerTrendsUI();
    // Populate weekly workouts list
    this.loadWeeklyWorkoutsUI();

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
                    <button class="trainer-view-icon-btn" title="View details" style="background: transparent; border: none; color: var(--color-primary); cursor: pointer; padding: 2px 4px;">
                        <i class="fa-solid fa-eye"></i>
                    </button>
                    <button class="trainer-delete-btn" data-id="${plan.id}" title="Delete log" style="background: transparent; border: none; color: #ff3b30; cursor: pointer; padding: 2px 4px;">
                        <i class="fa-solid fa-trash-can"></i>
                    </button>
                </div>
            `;

            const showPlan = async () => {
                soundSynth.playClick();
                const outputContainer = document.getElementById('trainer-plan-output-container');
                const outputDiv = document.getElementById('trainer-plan-output');
                if (outputContainer && outputDiv) {
                    this.renderTrainerPlanDetails(plan.id, plan.advice_text);
                }
                try {
                    const todayStr = new Date().toISOString().split('T')[0];
                    await fetch('/api/trainer/plans/book', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ plan_id: plan.id, start_date: todayStr })
                    });
                    this.loadWeeklyWorkoutsUI();
                } catch (bookErr) {
                    console.warn("[TRAINER] Auto-book plan error:", bookErr);
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

FrejaUIController.prototype.loadWeeklyWorkoutsUI = async function () {
    const weeklyWorkoutsList = document.getElementById('weekly-workouts-list');
    if (!weeklyWorkoutsList) return;

    weeklyWorkoutsList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">Loading weekly workouts...</div>';

    try {
        let workouts = [];

        // 1. Fetch directly from /api/trainer/workouts endpoint (if available)
        try {
            const res = await fetch('/api/trainer/workouts');
            if (res.ok) {
                workouts = await res.json();
            }
        } catch (err) {
            console.warn("[TRAINER UI] Fetch /api/trainer/workouts error:", err);
        }

        // 2. Fetch directly from /api/trainer/plans (always available on backend)
        if (!workouts || workouts.length === 0) {
            try {
                const res = await fetch('/api/trainer/plans?limit=5');
                if (res.ok) {
                    const plans = await res.json();
                    if (plans && plans.length > 0) {
                        const latestPlan = plans[0];
                        let adviceText = latestPlan.advice_text || "";

                        const dayOffsets = {
                            "måndag": 0, "mandag": 0, "mon": 0, "monday": 0,
                            "tisdag": 1, "tue": 1, "tuesday": 1,
                            "onsdag": 2, "wed": 2, "wednesday": 2,
                            "torsdag": 3, "thu": 3, "thursday": 3,
                            "fredag": 4, "fri": 4, "friday": 4,
                            "lördag": 5, "lordag": 5, "sat": 5, "saturday": 5,
                            "söndag": 6, "sondag": 6, "sun": 6, "sunday": 6
                        };

                        const today = new Date();
                        const currentDay = today.getDay();
                        const distanceToMonday = currentDay === 0 ? -6 : 1 - currentDay;
                        const monday = new Date(today);
                        monday.setDate(today.getDate() + distanceToMonday);

                        // Parse workouts safely using robust fallback parser
                        let rawWorkouts = [];
                        let cleaned = adviceText.replace(/```json/gi, '').replace(/```/g, '').trim();

                        try {
                            const planObj = JSON.parse(cleaned);
                            if (planObj && Array.isArray(planObj.workouts) && planObj.workouts.length > 0) {
                                rawWorkouts = planObj.workouts;
                            }
                        } catch (e) {
                            console.warn("[TRAINER UI] Strict JSON parse failed, extracting via regex:", e);
                        }

                        if (rawWorkouts.length === 0) {
                            try {
                                const objectMatches = cleaned.match(/\{\s*"day"[\s\S]*?\}/gi) || [];
                                objectMatches.forEach(str => {
                                    try {
                                        const item = JSON.parse(str);
                                        if (item && item.day) rawWorkouts.push(item);
                                    } catch (err) {
                                        const dayM = str.match(/"day"\s*:\s*"([^"]+)"/i);
                                        const titleM = str.match(/"title"\s*:\s*"([^"]+)"/i);
                                        const actM = str.match(/"activity_type"\s*:\s*"([^"]+)"/i);
                                        const durM = str.match(/"duration_minutes"\s*:\s*(\d+)/i);
                                        if (dayM) {
                                            rawWorkouts.push({
                                                day: dayM[1],
                                                title: titleM ? titleM[1] : "Träningspass",
                                                activity_type: actM ? actM[1] : "Träning",
                                                duration_minutes: durM ? parseInt(durM[1]) : 30
                                            });
                                        }
                                    }
                                });
                            } catch (err2) {
                                console.warn("[TRAINER UI] Regex extraction error:", err2);
                            }
                        }

                        if (rawWorkouts.length === 0) {
                            rawWorkouts = [
                                { day: "Måndag", activity_type: "Löpning", title: "Distanspass", description: "Lugn löpning i samtalstempo", duration_minutes: 35 },
                                { day: "Onsdag", activity_type: "Styrketräning", title: "Helkroppsstyrka", description: "Baskraft, knäböj & marklyft", duration_minutes: 45 },
                                { day: "Fredag", activity_type: "Löpning", title: "Intervallpass", description: "Uppvärmning + 5x3 min tempo", duration_minutes: 40 }
                            ];
                        }

                        rawWorkouts.forEach(w => {
                            const dName = String(w.day || "").toLowerCase().trim();
                            let offset = null;
                            for (const [k, v] of Object.entries(dayOffsets)) {
                                if (dName.includes(k)) { offset = v; break; }
                            }
                            if (offset !== null) {
                                const wDate = new Date(monday);
                                wDate.setDate(monday.getDate() + offset);
                                const dateStr = wDate.toISOString().split('T')[0];
                                const dur = parseInt(w.duration_minutes || 0) || 30;
                                const exercisesText = Array.isArray(w.exercises) && w.exercises.length > 0
                                    ? "\n\nÖvningar:\n" + w.exercises.map(ex => `• ${ex.name || 'Övning'}: ${ex.sets || 0}x${ex.reps || 0} @ ${ex.weight_kg ? ex.weight_kg + ' kg' : 'kroppsvikt'}`).join("\n")
                                    : "";
                                workouts.push({
                                    id: `plan_${latestPlan.id}_${offset}`,
                                    summary: `💪 ${w.activity_type || 'Träning'}: ${w.title || 'Pass'}`,
                                    description: (w.description || "") + exercisesText,
                                    duration_minutes: dur,
                                    start_time: `${dateStr}T08:00:00`,
                                    end_time: `${dateStr}T09:00:00`,
                                    location: "COACH AI",
                                    activity_type: w.activity_type || 'Träning',
                                    title: w.title || 'Pass',
                                    exercises: w.exercises || []
                                });
                            }
                        });
                    }
                }
            } catch (planErr) {
                console.warn("[TRAINER UI] Fetch /api/trainer/plans error:", planErr);
            }
        }

        // 3. Fallback to Google Calendar API data if still empty
        if (!workouts || workouts.length === 0) {
            try {
                const res = await fetch('/api/google_calendar/data?days=14');
                if (res.ok) {
                    const events = await res.json();
                    const WORKOUT_LOCATION_MARKER = "F.R.E.J.A. PT";
                    const WORKOUT_SUMMARY_MARKERS = ["💪", "🏃", "🚶", "🚴", "🧘", "🏊"];
                    workouts = events.filter(evt => {
                        const location = evt.location || "";
                        const summary = evt.summary || "";
                        return location.includes(WORKOUT_LOCATION_MARKER) || 
                               WORKOUT_SUMMARY_MARKERS.some(marker => summary.includes(marker));
                    });
                }
            } catch (calErr) {
                console.warn("[TRAINER UI] Google Calendar fallback error:", calErr);
            }
        }

        // Calculate Monday and Sunday of current week
        const today = new Date();
        const currentDay = today.getDay();
        const distanceToMonday = currentDay === 0 ? -6 : 1 - currentDay;
        
        const monday = new Date(today);
        monday.setDate(today.getDate() + distanceToMonday);
        monday.setHours(0, 0, 0, 0);

        const sunday = new Date(monday);
        sunday.setDate(monday.getDate() + 6);
        sunday.setHours(23, 59, 59, 999);

        // Filter for current week
        let thisWeeksWorkouts = (workouts || []).filter(evt => {
            if (!evt.start_time) return false;
            const eventDate = new Date(evt.start_time);
            return eventDate >= monday && eventDate <= sunday;
        });

        // Show all returned workouts if current week filter yields 0
        if (thisWeeksWorkouts.length === 0 && workouts && workouts.length > 0) {
            thisWeeksWorkouts = workouts;
        }

        if (thisWeeksWorkouts.length === 0) {
            weeklyWorkoutsList.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">No workouts scheduled for this week.</div>';
            return;
        }

        // Sort chronologically
        thisWeeksWorkouts.sort((a, b) => new Date(a.start_time) - new Date(b.start_time));

        weeklyWorkoutsList.innerHTML = "";

        const daysOfWeekEnglish = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
        const monthsEnglish = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

        thisWeeksWorkouts.forEach(evt => {
            const card = document.createElement('div');
            card.className = "workout-card";
            card.style.background = "rgba(0, 242, 254, 0.03)";
            card.style.border = "1px solid rgba(0, 242, 254, 0.12)";
            card.style.borderRadius = "6px";
            card.style.padding = "12px";
            card.style.display = "flex";
            card.style.flexDirection = "column";
            card.style.gap = "6px";
            card.style.transition = "all 0.2s ease";
            card.style.boxShadow = "inset 0 1px 1px rgba(255, 255, 255, 0.02)";

            const d = new Date(evt.start_time);
            const dayName = daysOfWeekEnglish[d.getDay()];
            const dateNum = d.getDate();
            const monthName = monthsEnglish[d.getMonth()];
            
            const formatTime = (isoStr) => {
                if (!isoStr) return "";
                const parts = isoStr.split('T');
                if (parts.length === 2) {
                    return parts[1].substring(0, 5);
                }
                return "";
            };

            const startTimeStr = formatTime(evt.start_time);
            const endTimeStr = formatTime(evt.end_time);
            const timeRange = startTimeStr && endTimeStr ? `${startTimeStr} - ${endTimeStr}` : startTimeStr;

            let durationStr = "";
            if (evt.start_time && evt.end_time) {
                const diffMs = new Date(evt.end_time) - new Date(evt.start_time);
                const diffMin = Math.round(diffMs / 60000);
                if (diffMin > 0) {
                    durationStr = ` (${diffMin} min)`;
                }
            }

            const headerText = `${dayName.toUpperCase()} ${dateNum} ${monthName.toUpperCase()}`;
            const descHtml = evt.description ? `<div style="font-size: 11px; color: var(--color-text-muted); line-height: 1.4; border-top: 1px solid rgba(255, 255, 255, 0.05); padding-top: 6px; margin-top: 4px; white-space: pre-wrap;">${evt.description}</div>` : "";
            const locationHtml = evt.location ? `<span style="color: var(--color-accent); font-size: 10px;"><i class="fa-solid fa-location-dot"></i> ${evt.location}</span>` : "";

            card.innerHTML = `
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="font-family: var(--font-display); font-size: 10px; color: var(--color-primary); letter-spacing: 0.5px; font-weight: bold;">${headerText}</span>
                    <div style="display: flex; flex-direction: column; align-items: flex-end; gap: 2px;">
                        <span style="font-family: var(--font-mono); font-size: 10px; color: var(--color-text-bright);">${timeRange}${durationStr}</span>
                        ${locationHtml}
                    </div>
                </div>
                <div style="font-weight: bold; font-size: 12px; color: var(--color-text-bright); font-family: var(--font-display);">${evt.summary}</div>
                ${descHtml}
            `;
            
            weeklyWorkoutsList.appendChild(card);
        });

    } catch (e) {
        console.error("[TRAINER] Weekly workouts load error:", e);
        weeklyWorkoutsList.innerHTML = '<div style="color: #ff3b30; text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 20px;">[ERROR FETCHING WEEKLY WORKOUTS]</div>';
    }
};

FrejaUIController.prototype.runTrainerCheckin = async function () {
    const btn = document.getElementById('btn-trainer-checkin');
    const out = document.getElementById('trainer-checkin-output');
    if (out) {
        out.style.display = 'none';
        out.innerHTML = '';
    }

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> ADAPTING WORKOUTS...';
    }
    this.writeLog("RUNNING DAILY CHECK-IN & ADAPTING WORKOUTS...", "sys");

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

        soundSynth.playNotify();
        this.writeLog("DAILY CHECK-IN COMPLETE: WORKOUTS ADAPTED", "sys");

        // Refresh workouts & trainer dashboard with adapted data
        await this.loadTrainerDashboardUI();
        await this.loadWeeklyWorkoutsUI();

    } catch (e) {
        console.error("[TRAINER] Check-in error:", e);
        this.writeLog(`CHECK-IN ERROR: ${e.message}`, "err");
        soundSynth.playError();
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i class="fa-solid fa-heart-pulse"></i> CHECK IN';
        }
    }
};

FrejaUIController.prototype.loadTrainerSettings = async function () {
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

FrejaUIController.prototype.saveTrainerAutoAdjust = async function (enabled) {
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

// Populate the onboarding profile form from the stored trainer profile (Issue #32).
FrejaUIController.prototype.loadTrainerProfileUI = async function () {
    try {
        const res = await fetch('/api/trainer/profile');
        if (!res.ok) return;
        const p = await res.json();
        if (!p || typeof p !== 'object') return;

        // Set stored profile values IMMEDIATELY so fields fill in with 0 delay
        const setVal = (id, value) => {
            const el = document.getElementById(id);
            if (el && value !== null && value !== undefined) el.value = value;
        };
        setVal('trainer-input-goal', p.goals || p.goal || '');
        setVal('trainer-input-limitations', p.limitations || '');
        setVal('trainer-input-event', p.event || '');
        setVal('trainer-input-event-date', p.event_date || '');
        setVal('trainer-input-availability', p.availability || '');
        setVal('trainer-input-location', p.location || '');
        setVal('trainer-input-baseline-rhr', p.baseline_resting_hr || '');
        setVal('trainer-input-baseline-sleep', p.baseline_sleep_hours || '');
        setVal('trainer-input-baseline-hrv', p.baseline_hrv || '');

        const fitnessSel = document.getElementById('trainer-select-fitness-level');
        if (fitnessSel && p.fitness_level) {
            const match = Array.from(fitnessSel.options).some(o => o.value.toLowerCase() === p.fitness_level.toLowerCase());
            if (match) fitnessSel.value = p.fitness_level.toLowerCase();
        }

        // Fetch Garmin data in background without blocking form population
        try {
            const garminRes = await fetch('/api/garmin/data?days=7');
            if (garminRes.ok) {
                const garminData = await garminRes.json();
                let latestRhr = null;
                let latestSleep = null;
                let latestHrv = null;

                for (const d of garminData) {
                    if (latestRhr === null && d.resting_hr && d.resting_hr > 0) latestRhr = d.resting_hr;
                    if (latestSleep === null && d.sleep_hours && d.sleep_hours > 0) latestSleep = d.sleep_hours;
                    if (latestHrv === null && d.hrv && d.hrv > 0) latestHrv = d.hrv;
                }

                if (latestRhr !== null) setVal('trainer-input-baseline-rhr', latestRhr);
                if (latestSleep !== null) setVal('trainer-input-baseline-sleep', latestSleep);
                if (latestHrv !== null) setVal('trainer-input-baseline-hrv', latestHrv);
            }
        } catch (garminErr) {
            console.warn('[TRAINER] Could not pull latest Garmin data for baselines:', garminErr);
        }
    } catch (e) {
        console.error('[TRAINER] Failed to load profile form:', e);
    }
};

// Render the recent strength-log history (Issue #34).
FrejaUIController.prototype.loadStrengthLogsUI = async function () {
    const list = document.getElementById('trainer-strength-list');
    if (!list) return;
    try {
        const res = await fetch('/api/trainer/strength/log?limit=25');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const logs = (data && data.logs) || [];
        if (logs.length === 0) {
            list.innerHTML = '<div style="color: var(--color-text-muted); font-family: var(--font-mono); font-size: 11px; padding: 6px;">[NO STRENGTH SETS LOGGED]</div>';
            return;
        }
        list.innerHTML = '';
        logs.forEach(log => {
            const row = document.createElement('div');
            row.style.display = 'flex';
            row.style.justifyContent = 'space-between';
            row.style.alignItems = 'center';
            row.style.fontFamily = 'var(--font-mono)';
            row.style.fontSize = '11px';
            row.style.padding = '4px 6px';
            row.style.borderBottom = '1px solid rgba(0, 242, 254, 0.08)';

            const load = log.weight ? `${log.weight} kg` : 'kroppsvikt';
            const rpe = log.rpe ? `, RPE ${log.rpe}` : '';
            row.innerHTML = `
                <span style="color: var(--color-text-bright);">
                    <span style="color: var(--color-primary);">${log.date}</span>
                    ${log.exercise_name}: ${log.sets || 0}×${log.reps || 0} @ ${load}${rpe}
                </span>
                <button class="strength-delete-btn" data-id="${log.id}" title="Delete" style="background: transparent; border: none; color: #ff3b30; cursor: pointer; padding: 2px 4px;">
                    <i class="fa-solid fa-trash-can"></i>
                </button>
            `;
            row.querySelector('.strength-delete-btn').addEventListener('click', async () => {
                soundSynth.playClick();
                try {
                    const delRes = await fetch(`/api/trainer/strength/log?log_id=${log.id}`, { method: 'DELETE' });
                    if (delRes.ok) this.loadStrengthLogsUI();
                } catch (err) {
                    console.error('[TRAINER] Failed to delete strength log:', err);
                }
            });
            list.appendChild(row);
        });
    } catch (e) {
        console.error('[TRAINER] Strength log load error:', e);
        list.innerHTML = '<div style="color: #ff3b30; font-family: var(--font-mono); font-size: 11px; padding: 6px;">[ERROR LOADING STRENGTH LOG]</div>';
    }
};

// --- Injury / pain log (Issue #38) ------------------------------------------

// Render the injury/pain log. Active entries are listed first and highlighted, since
// those are the ones COACH AI actually feeds into plan generation and optimization.
FrejaUIController.prototype.loadInjuryLogUI = async function () {
    const list = document.getElementById('trainer-injury-list');
    if (!list) return;
    try {
        const res = await fetch('/api/trainer/injuries?limit=50');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const injuries = (data && data.injuries) || [];
        if (injuries.length === 0) {
            list.innerHTML = '<div style="color: var(--color-text-muted); font-family: var(--font-mono); font-size: 11px; padding: 6px;">[NO INJURIES LOGGED]</div>';
            return;
        }

        // Active first, then most recent.
        injuries.sort((a, b) => {
            const activeDiff = (b.status === 'active') - (a.status === 'active');
            return activeDiff !== 0 ? activeDiff : String(b.date).localeCompare(String(a.date));
        });

        list.innerHTML = '';
        injuries.forEach(inj => {
            const isActive = inj.status === 'active';
            const row = document.createElement('div');
            row.style.display = 'flex';
            row.style.justifyContent = 'space-between';
            row.style.alignItems = 'center';
            row.style.gap = '8px';
            row.style.fontFamily = 'var(--font-mono)';
            row.style.fontSize = '11px';
            row.style.padding = '4px 6px';
            row.style.borderBottom = '1px solid rgba(0, 242, 254, 0.08)';
            row.style.opacity = isActive ? '1' : '0.55';

            // Severity drives the colour so a bad niggle is obvious at a glance.
            const sev = Number(inj.severity) || 0;
            const sevColor = sev >= 7 ? '#ff3b30' : (sev >= 4 ? '#ffb020' : 'var(--color-primary)');
            const sevBadge = sev
                ? `<span style="color: ${sevColor};">[${sev}/10]</span>`
                : '';
            const note = inj.note ? ` - ${inj.note}` : '';
            const resolved = isActive ? '' : ` (resolved ${inj.resolved_date || ''})`;

            row.innerHTML = `
                <span style="flex: 1; color: var(--color-text-bright); ${isActive ? '' : 'text-decoration: line-through;'}">
                    <span style="color: var(--color-primary);">${inj.date}</span>
                    ${sevBadge} ${inj.area}${note}${resolved}
                </span>
                <span style="display: flex; gap: 4px;">
                    <button class="injury-toggle-btn" title="${isActive ? 'Mark as resolved' : 'Reopen'}" style="background: transparent; border: none; color: ${isActive ? 'var(--color-primary)' : 'var(--color-text-muted)'}; cursor: pointer; padding: 2px 4px;">
                        <i class="fa-solid ${isActive ? 'fa-check' : 'fa-rotate-left'}"></i>
                    </button>
                    <button class="injury-delete-btn" title="Delete" style="background: transparent; border: none; color: #ff3b30; cursor: pointer; padding: 2px 4px;">
                        <i class="fa-solid fa-trash-can"></i>
                    </button>
                </span>
            `;

            row.querySelector('.injury-toggle-btn').addEventListener('click', async () => {
                soundSynth.playClick();
                try {
                    const putRes = await fetch('/api/trainer/injuries', {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ id: inj.id, status: isActive ? 'resolved' : 'active' })
                    });
                    if (putRes.ok) {
                        this.writeLog(`INJURY ${inj.area.toUpperCase()} ${isActive ? 'RESOLVED' : 'REOPENED'}`, "sys");
                        this.loadInjuryLogUI();
                    }
                } catch (err) {
                    console.error('[TRAINER] Failed to update injury:', err);
                }
            });

            row.querySelector('.injury-delete-btn').addEventListener('click', async () => {
                if (!confirm(`Really delete the injury entry for ${inj.area}?`)) return;
                soundSynth.playClick();
                try {
                    const delRes = await fetch(`/api/trainer/injuries?injury_id=${inj.id}`, { method: 'DELETE' });
                    if (delRes.ok) this.loadInjuryLogUI();
                } catch (err) {
                    console.error('[TRAINER] Failed to delete injury:', err);
                }
            });

            list.appendChild(row);
        });
    } catch (e) {
        console.error('[TRAINER] Injury log load error:', e);
        list.innerHTML = '<div style="color: #ff3b30; font-family: var(--font-mono); font-size: 11px; padding: 6px;">[ERROR LOADING INJURY LOG]</div>';
    }
};

// --- Trend & adherence charts (Issue #36) ------------------------------------

// Builds a self-contained SVG sparkline for one metric. No chart library is loaded in
// the HUD, so the geometry is computed here: the viewBox is stretched to the card width
// (preserveAspectRatio="none") and strokes opt out of that scaling via vector-effect,
// which keeps line weights even at any panel size.
FrejaUIController.prototype.buildTrendSparkline = function (points, options) {
    const opts = options || {};
    const W = 300, H = 60, PAD = 4;
    const values = points.map(p => p.value);
    let min = Math.min.apply(null, values);
    let max = Math.max.apply(null, values);
    if (opts.baseline !== null && opts.baseline !== undefined && !isNaN(opts.baseline)) {
        min = Math.min(min, opts.baseline);
        max = Math.max(max, opts.baseline);
    }
    // A flat series would divide by zero; give it a little headroom instead.
    if (max === min) { max += 1; min -= 1; }

    const x = i => PAD + (i * (W - 2 * PAD)) / Math.max(1, points.length - 1);
    const y = v => H - PAD - ((v - min) / (max - min)) * (H - 2 * PAD);

    const line = points.map((p, i) => `${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');
    const area = `${PAD},${H - PAD} ${line} ${x(points.length - 1).toFixed(1)},${H - PAD}`;
    const color = opts.color || 'var(--color-primary)';

    let baselineLine = '';
    if (opts.baseline !== null && opts.baseline !== undefined && !isNaN(opts.baseline)) {
        const by = y(opts.baseline).toFixed(1);
        baselineLine = `<line x1="${PAD}" y1="${by}" x2="${W - PAD}" y2="${by}" stroke="var(--color-text-muted)" stroke-width="1" stroke-dasharray="3 3" vector-effect="non-scaling-stroke" opacity="0.7"></line>`;
    }

    const lastX = x(points.length - 1).toFixed(1);
    const lastY = y(points[points.length - 1].value).toFixed(1);

    return `
        <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width: 100%; height: ${H}px; display: block; overflow: visible;">
            <polygon points="${area}" fill="${color}" opacity="0.12"></polygon>
            ${baselineLine}
            <polyline points="${line}" fill="none" stroke="${color}" stroke-width="1.5" vector-effect="non-scaling-stroke" stroke-linejoin="round"></polyline>
            <circle cx="${lastX}" cy="${lastY}" r="2.5" fill="${color}" vector-effect="non-scaling-stroke"></circle>
        </svg>
    `;
};

// Renders one metric card: current value, change vs baseline window, and the sparkline.
FrejaUIController.prototype.buildTrendCard = function (cfg) {
    const label = cfg.label || 'METRIC';
    const points = cfg.points || [];
    const unit = cfg.unit || '';
    const color = cfg.color || 'var(--color-primary)';
    const baseline = cfg.baseline;
    const changePct = cfg.changePct;
    const goodDir = cfg.goodDirection || 'up';

    // A sparkline needs at least two points to draw a line between.
    if (points.length < 2) {
        return `
            <div class="trend-metric-card" style="padding: 10px;">
                <div style="font-size: 9px; color: var(--color-text-muted); font-family: var(--font-display); letter-spacing: 0.5px;">${label}</div>
                <div style="font-size: 11px; color: var(--color-text-muted); font-family: var(--font-mono); padding: 12px 0;">[NOT ENOUGH DATA]</div>
            </div>
        `;
    }

    const latestVal = `${points[points.length - 1].value}${unit}`;

    let changeHtml = '';
    if (changePct !== null && changePct !== undefined && !isNaN(changePct)) {
        // "Good" points in opposite directions per metric: a falling RHR is good, a
        // falling HRV is not, so each card says which way is favourable.
        const isGood = (goodDir === 'up' && changePct >= 0) || (goodDir === 'down' && changePct <= 0);
        const arrow = changePct >= 0 ? '▲' : '▼';
        const cColor = Math.abs(changePct) < 1 ? 'var(--color-text-muted)' : (isGood ? '#30d158' : '#ff9f0a');
        changeHtml = `<span style="color: ${cColor}; font-size: 10px; font-family: var(--font-mono);">${arrow} ${Math.abs(changePct).toFixed(1)}%</span>`;
    }

    let baselineHtml = '';
    if (baseline !== null && baseline !== undefined && !isNaN(baseline)) {
        baselineHtml = `<span style="color: var(--color-text-muted); font-size: 9px; font-family: var(--font-mono);">Base: ${Number(baseline).toFixed(1)}${unit}</span>`;
    }

    return `
        <div class="trend-metric-card" style="padding: 10px; display: flex; flex-direction: column; gap: 6px;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span style="font-size: 9px; color: var(--color-text-muted); font-family: var(--font-display); letter-spacing: 0.5px;">${label}</span>
                ${changeHtml}
            </div>
            <div style="display: flex; justify-content: space-between; align-items: baseline;">
                <span style="font-size: 16px; font-weight: bold; color: ${color}; font-family: var(--font-mono);">${latestVal}</span>
                ${baselineHtml}
            </div>
            ${this.buildTrendSparkline(points, { color: color, baseline: baseline })}
            <div style="display: flex; justify-content: space-between; font-size: 9px; color: var(--color-text-muted); font-family: var(--font-mono);">
                <span>${points[0].date}</span>
                <span>${points[points.length - 1].date}</span>
            </div>
        </div>
    `;
};

// Fetch and draw the RHR/HRV trends plus planned-vs-completed adherence.
FrejaUIController.prototype.loadTrainerTrendsUI = async function () {
    const container = document.getElementById('trainer-trends-charts');
    if (!container) return;

    const rangeSel = document.getElementById('trainer-trend-range');
    const days = (rangeSel && rangeSel.value) || 28;

    container.innerHTML = '<div style="color: var(--color-text-muted); text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 16px;">Loading trends...</div>';

    try {
        let data = {};
        const res = await fetch(`/api/trainer/trends?days=${days}`);
        if (res.ok) {
            data = await res.json();
        } else if (res.status === 401) {
            // Say so plainly. This used to fall back to the stored profile baselines and,
            // failing those, to literal placeholders (62 bpm / 23 ms) - so an unauthenticated
            // HUD drew a confident-looking chart out of numbers that were never measured.
            container.innerHTML = '<div style="color: #ffb020; text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 16px; line-height: 1.5;">[NOT AUTHENTICATED]<br>Enter your access token in Settings to load trends.</div>';
            return;
        } else {
            throw new Error(`HTTP ${res.status}`);
        }

        const series = data.series || [];
        const trends = data.trends || {};
        const baselines = data.baselines || {};
        const adherence = data.adherence || {};

        // A metric is only plotted on days it actually has a reading, so gaps in the
        // data stay gaps instead of collapsing to zero.
        const pointsFor = key => series
            .filter(p => p[key] !== null && p[key] !== undefined)
            .map(p => ({ date: p.date, value: Number(p[key]) }));

        const rhrCard = this.buildTrendCard({
            label: 'RESTING HR (RHR)',
            points: pointsFor('rhr'),
            unit: ' bpm',
            color: '#00f2fe',
            baseline: baselines.resting_hr,
            changePct: trends.rhr_change_pct,
            goodDirection: 'down'
        });
        const hrvCard = this.buildTrendCard({
            label: 'HRV',
            points: pointsFor('hrv'),
            unit: ' ms',
            color: '#bf5af2',
            baseline: baselines.hrv,
            changePct: trends.hrv_change_pct,
            goodDirection: 'up'
        });

        // Adherence: planned sessions vs the ones actually completed on Strava.
        const planned = adherence.planned || 0;
        const completed = adherence.completed || 0;
        const pct = adherence.adherence_pct;
        const barPct = planned ? Math.min(100, Math.round((completed / planned) * 100)) : 0;
        const barColor = barPct >= 80 ? '#30d158' : (barPct >= 50 ? '#ffb020' : '#ff3b30');
        const missed = (adherence.missed_dates || []).slice(-6);

        const adherenceCard = planned === 0
            ? `<div class="trend-metric-card" style="padding: 10px;">
                   <div style="font-size: 9px; color: var(--color-text-muted); font-family: var(--font-display); letter-spacing: 0.5px;">ADHERENCE (PLANNED VS COMPLETED)</div>
                   <div style="font-size: 11px; color: var(--color-text-muted); font-family: var(--font-mono); padding: 8px 0;">[NO BOOKED SESSIONS IN THIS WINDOW]</div>
               </div>`
            : `<div class="trend-metric-card" style="padding: 10px; display: flex; flex-direction: column; gap: 8px;">
                   <div style="display: flex; justify-content: space-between; align-items: baseline;">
                       <span style="font-size: 9px; color: var(--color-text-muted); font-family: var(--font-display); letter-spacing: 0.5px;">ADHERENCE (PLANNED VS COMPLETED)</span>
                       <span style="font-size: 10px; font-family: var(--font-mono); color: ${barColor};">${pct !== null && pct !== undefined ? pct + '%' : '-'}</span>
                   </div>
                   <div style="font-family: var(--font-mono); font-size: 16px; color: var(--color-text-bright);">
                       ${completed}<span style="font-size: 11px; color: var(--color-text-muted);"> / ${planned} sessions</span>
                   </div>
                   <div style="height: 10px; background: rgba(255,255,255,0.06); border-radius: 5px; overflow: hidden;">
                       <div style="height: 100%; width: ${barPct}%; background: ${barColor}; border-radius: 5px; transition: width 0.4s ease;"></div>
                   </div>
                   ${missed.length ? `<div style="font-size: 9px; color: var(--color-text-muted); font-family: var(--font-mono);">Missed: ${missed.join(', ')}</div>` : ''}
               </div>`;

        container.innerHTML = `
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
                ${rhrCard}
                ${hrvCard}
            </div>
            ${adherenceCard}
        `;
    } catch (e) {
        console.error('[TRAINER] Trend chart load error:', e);
        container.innerHTML = '<div style="color: #ff3b30; text-align: center; font-family: var(--font-mono); font-size: 11px; padding: 16px;">[ERROR LOADING TRENDS]</div>';
    }
};

FrejaUIController.prototype.runTrainerOptimize = async function () {
    const btn = document.getElementById('btn-trainer-optimize');
    const out = document.getElementById('trainer-optimize-output');
    if (!out) return;

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> OPTIMIZING...';
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
        // Freja's own summary text, so the fallback is Swedish too.
        const briefing = data.briefing || 'No summary generated.';
        const changes = data.changes || [];

        const badgeStyle = "font-size: 10px; font-family: var(--font-mono); background: rgba(0,242,254,0.1); border: 1px solid rgba(0,242,254,0.2); color: var(--color-primary); border-radius: 3px; padding: 3px 8px;";
        let badges = `<span style="${badgeStyle}">🔍 ${data.considered || 0} sessions reviewed</span>`;
        badges += `<span style="${badgeStyle}">${data.changes_count ? '✅' : '➖'} ${data.changes_count || 0} adjusted</span>`;

        let changeList = '';
        if (changes.length) {
            changeList = '<ul style="margin: 10px 0 0; padding-left: 18px; font-size: 12px; color: var(--color-text-muted);">' +
                changes.map(c => `<li><strong>${c.date}</strong>: ${c.from_minutes}→${c.to_minutes} min - ${c.reason || c.title}</li>`).join('') +
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
            btn.innerHTML = '<i class="fa-solid fa-wand-sparkles"></i> OPTIMIZE UPCOMING SESSIONS NOW';
        }
    }
};

FrejaUIController.prototype.renderTrainerPlanDetails = function (planId, adviceText) {
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

        // Structured strength exercises (Issue #34), shown as a compact table when present.
        let exercisesHTML = '';
        if (Array.isArray(w.exercises) && w.exercises.length > 0) {
            const rows = w.exercises.map(ex => {
                const setsReps = `${ex.sets || 0}×${ex.reps || 0}`;
                let load = '';
                if (ex.target_weight && ex.target_weight > 0) {
                    load = `${ex.target_weight} kg`;
                } else if (ex.rpe && ex.rpe > 0) {
                    load = `RPE ${ex.rpe}`;
                }
                return `<div style="display: flex; justify-content: space-between; gap: 8px;">
                    <span>${ex.name || ''}</span>
                    <span style="color: var(--color-primary); white-space: nowrap;">${setsReps}${load ? ' @ ' + load : ''}</span>
                </div>`;
            }).join('');
            exercisesHTML = `
                <div style="margin-top: 6px; padding: 6px 8px; background: rgba(0,242,254,0.05); border-radius: 3px; font-size: 10px; font-family: var(--font-mono); color: var(--color-text-muted); display: flex; flex-direction: column; gap: 3px;">
                    <span style="color: var(--color-primary); font-family: var(--font-display); letter-spacing: 0.5px;"><i class="fa-solid fa-dumbbell"></i> ÖVNINGAR</span>
                    ${rows}
                </div>
            `;
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
                    ${exercisesHTML}
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
                        <i class="fa-solid fa-calendar-plus"></i> BOOK SESSIONS
                    </button>
                </div>
                <!-- Take the plan out of Freja: same start date drives both exports. -->
                <div style="display: flex; gap: 8px; align-items: center; border-top: 1px dashed rgba(0, 242, 254, 0.15); padding-top: 8px;">
                    <span style="font-size: 9px; color: var(--color-text-muted); font-family: var(--font-display); letter-spacing: 0.5px; flex: 1;">EXPORTERA PLANEN</span>
                    <button id="btn-trainer-export-ics" class="hud-btn btn-secondary" style="height: 30px; font-family: var(--font-display); font-size: 10px; padding: 0 12px; display: flex; align-items: center; gap: 5px;">
                        <i class="fa-solid fa-calendar-days"></i> .ICS
                    </button>
                    <button id="btn-trainer-export-pdf" class="hud-btn btn-secondary" style="height: 30px; font-family: var(--font-display); font-size: 10px; padding: 0 12px; display: flex; align-items: center; gap: 5px;">
                        <i class="fa-solid fa-file-pdf"></i> PDF
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
                alert(`Error communicating with the server: ${err.message}`);
            } finally {
                btnBook.disabled = false;
                btnBook.innerHTML = '<i class="fa-solid fa-calendar-plus"></i> BOOK SESSIONS';
            }
        });
    }

    // Plan export (Issue #39). The API is token-protected, so the file has to come down
    // through fetch (which the global wrapper adds the header to) rather than a plain
    // link - a bare href would be rejected with a 401.
    const wireExport = (buttonId, format, label) => {
        const btn = document.getElementById(buttonId);
        if (!btn) return;
        btn.addEventListener('click', async () => {
            soundSynth.playClick();
            const startDateVal = (document.getElementById('trainer-book-start-date') || {}).value || '';
            const originalHtml = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';

            let objectUrl = null;
            try {
                const params = new URLSearchParams({ plan_id: planId, format });
                if (startDateVal) params.set('start_date', startDateVal);
                const res = await fetch(`/api/trainer/plans/export?${params.toString()}`);
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    throw new Error(err.detail || `HTTP ${res.status}`);
                }

                // Prefer the filename the server chose (Content-Disposition).
                const disposition = res.headers.get('Content-Disposition') || '';
                const match = disposition.match(/filename="?([^"]+)"?/);
                const filename = match ? match[1] : `freja-plan-${planId}.${format}`;

                const blob = await res.blob();
                objectUrl = URL.createObjectURL(blob);
                const link = document.createElement('a');
                link.href = objectUrl;
                link.download = filename;
                document.body.appendChild(link);
                link.click();
                link.remove();

                // Revoking synchronously here can cancel the download before the browser
                // has read the blob, so let the current task finish first.
                const urlToRevoke = objectUrl;
                objectUrl = null;
                setTimeout(() => URL.revokeObjectURL(urlToRevoke), 10000);

                this.writeLog(`PLAN EXPORTED AS ${label} (${filename})`, "sys");
                soundSynth.playNotify();
            } catch (e) {
                this.writeLog(`PLAN EXPORT ERROR: ${e.message}`, "err");
                soundSynth.playError();
                alert(`Kunde inte exportera planen: ${e.message}`);
            } finally {
                // Only reached with a live URL when the download itself threw.
                if (objectUrl) URL.revokeObjectURL(objectUrl);
                btn.disabled = false;
                btn.innerHTML = originalHtml;
            }
        });
    };
    wireExport('btn-trainer-export-ics', 'ics', 'ICS');
    wireExport('btn-trainer-export-pdf', 'pdf', 'PDF');
};

FrejaUIController.prototype.loadGarminDashboardUI = async function () {
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

            // A day the watch recorded nothing stores NULL rather than 0, so render it as a
            // dash. Printing the raw value would show "null steps"; printing 0 would claim
            // the user genuinely took no steps.
            const metric = (value) => (value === null || value === undefined ? "–" : value);

            item.innerHTML = `
                <div style="flex: 1; color: var(--color-text-bright);">
                    <span style="color: var(--color-primary);">${log.date}</span>: ${metric(log.steps)} steps | ${metric(log.sleep_hours)}h sleep | ${metric(log.resting_hr)} bpm | ${metric(log.active_calories)} kcal${workoutInfo}${bbInfo}${hrvInfo}
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

FrejaUIController.prototype.loadStravaDashboardUI = async function () {
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
        const res = await fetch('/api/strava/data?limit=15');
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

FrejaUIController.prototype.loadWithingsDashboardUI = async function () {
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

FrejaUIController.prototype.loadGoogleCalendarDashboardUI = async function () {
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

FrejaUIController.prototype.pollSyncStatus = async function (provider) {
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

FrejaUIController.prototype.loadCredentialsUI = async function () {
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
                <button class="btn-delete-cred hud-btn-icon" data-clean="${cred.clean_domain}" style="height: 18px; width: 18px; font-size: 9px; line-height: 18px; display: inline-flex; justify-content: center; align-items: center; border-color: rgba(255, 59, 48, 0.3); color: #ff3b30;" title="Delete login">
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

FrejaUIController.prototype.loadLearningVaultUI = async function () {
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
                        <button class="btn-delete-knowledge hud-btn-icon" data-id="${entry.id}" style="height: 20px; width: 20px; font-size: 10px; border-color: rgba(255,59,48,0.3); color: #ff3b30;" title="Delete knowledge">
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

FrejaUIController.prototype.appendTrainerChatMessage = function(sender, text) {
    const trainerChatHistory = document.getElementById('trainer-chat-history');
    if (!trainerChatHistory) return;

    const msgDiv = document.createElement('div');
    msgDiv.className = `chat-msg ${sender}-msg`;

    const senderTag = sender === 'user' ? '[USER]' : '[FREJA]';
    const formattedText = window.FrejaMarkdown.parseMarkdown(text);

    if (sender === 'assistant') {
        msgDiv.innerHTML = `
            <div class="msg-sender">${senderTag}</div>
            <div class="msg-content" style="position: relative; padding-right: 28px;">
                ${formattedText}
                <button class="btn-copy-msg" title="Kopiera svar" style="position: absolute; top: 8px; right: 8px; background: transparent; border: none; color: var(--color-text-muted); cursor: pointer; font-size: 11px; transition: color 0.2s;" onmouseover="this.style.color='var(--color-primary)'" onmouseout="this.style.color='var(--color-text-muted)'">
                    <i class="fa-regular fa-copy"></i>
                </button>
            </div>
        `;
        const copyBtn = msgDiv.querySelector('.btn-copy-msg');
        if (copyBtn) {
            copyBtn.addEventListener('click', () => {
                const fallbackCopy = (val) => {
                    try {
                        const textarea = document.createElement('textarea');
                        textarea.value = val;
                        textarea.style.position = 'fixed';
                        textarea.style.top = '0';
                        textarea.style.left = '0';
                        textarea.style.opacity = '0';
                        document.body.appendChild(textarea);
                        textarea.select();
                        const res = document.execCommand('copy');
                        document.body.removeChild(textarea);
                        return res;
                    } catch (err) {
                        console.error("Fallback copy failed:", err);
                        return false;
                    }
                };

                let success = true;
                if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
                    navigator.clipboard.writeText(text).catch(err => {
                        console.error("Clipboard API failed, using fallback:", err);
                        fallbackCopy(text);
                    });
                } else {
                    success = fallbackCopy(text);
                }

                if (success && window.soundSynth) {
                    window.soundSynth.playClick();
                }
                const icon = copyBtn.querySelector('i');
                icon.className = 'fa-solid fa-check';
                copyBtn.style.color = '#00ff66';
                setTimeout(() => {
                    icon.className = 'fa-regular fa-copy';
                    copyBtn.style.color = 'var(--color-text-muted)';
                }, 2000);
            });
        }
    } else {
        msgDiv.innerHTML = `
            <div class="msg-sender">${senderTag}</div>
            <div class="msg-content">${formattedText}</div>
        `;
    }

    trainerChatHistory.appendChild(msgDiv);
    trainerChatHistory.scrollTop = trainerChatHistory.scrollHeight;
};

FrejaUIController.prototype.processTrainerChatQuery = async function(query) {
    const inputEl = document.getElementById('trainer-chat-input');
    const sendBtn = document.getElementById('btn-trainer-chat-send');
    if (inputEl) inputEl.disabled = true;
    if (sendBtn) sendBtn.disabled = true;

    this.writeLog(`TRAINER QUERY SUBMITTED: "${query}"`, "user");
    this.appendTrainerChatMessage("user", query);
    
    // Save message to database
    this.saveChatMessage("user", query);

    try {
        if (window.visualizer) {
            window.visualizer.state = 'PROCESSING';
        }
        
        this.writeLog("NEURAL COGNITION UPLINK ENGAGED", "gemini");
        
        // Request response from Google Gemini Client (no webcam snapshot)
        const response = await this.gemini.generateResponse(query, false);
        
        this.writeLog("RESPONSE SECURED. INITIATING AUDIO SYNTHESIS", "gemini");
        this.appendTrainerChatMessage("assistant", response);
        
        // Save response to database
        this.saveChatMessage("assistant", response);
        
        // Synthesize response speech audio
        await this.speech.speak(response);

        // Add exchange to memory store asynchronously in background
        if (this.memory && this.memory.enabled) {
            this.memory.addMemory(query, response).then((res) => {
                if (res) {
                    this.writeLog("NEURAL ENGRAM ENCODED SECURELY", "sys");
                }
            }).catch(e => {
                console.warn("[MEM0] Background memory extraction failed:", e);
            });
        }
    } catch (err) {
        console.error("Trainer chat query failure:", err);
        this.appendTrainerChatMessage("assistant", "[ANOMALI] Kunde inte behandla din förfrågan.");
    } finally {
        if (inputEl) {
            inputEl.disabled = false;
            inputEl.value = "";
            inputEl.focus();
        }
        if (sendBtn) sendBtn.disabled = false;
        if (window.visualizer) {
            window.visualizer.state = 'SLEEPING';
        }
    }
};
