/**
 * F.R.E.J.A. Tool: Garmin Health Integration
 * 
 * Fetches and aggregates health, activity, sleep and workout metrics from
 * the local SQLite database cache (representing synced Garmin Connect data).
 */
(function() {
    const garminTool = {
        name: "get_garmin_health",
        displayName: "Garmin Hälsocoach",
        description: "Hämtar och analyserar användarens Garmin-statistik för att ge tränings- och hälsoråd.",
        permissionKey: "freja_tool_get_garmin_health_allowed",
        declaration: {
            name: "get_garmin_health",
            description: "Hämtar användarens senaste Garmin hälso- och träningsdata (steg, sömn, vilopuls, kalorier, body battery, HRV och träningspass). Standard är 1 dag (enbart senaste dygnet) om inte användaren uttryckligen ber om en längre period som t.ex. senaste veckan.",
            parameters: {
                type: "OBJECT",
                properties: {
                    days: {
                        type: "INTEGER",
                        description: "Antal dagar historik att hämta (standard är 1 för enbart senaste dagen)."
                    }
                }
            }
        },
        
        /**
         * Executes the Garmin data retrieval.
         */
        async execute(args) {
            const days = (args && args.days) !== undefined ? args.days : 1;
            let syncStatus = "inte genomförd";
            let syncMessage = "";
            
            console.log(`[FREJA TOOL: Garmin] Syncing device data first...`);
            try {
                const syncRes = await fetch('/api/garmin/sync');
                const syncData = await syncRes.json();
                if (syncRes.ok && syncData.status === "success") {
                    syncStatus = "success";
                    syncMessage = syncData.message;
                    console.log("[FREJA TOOL: Garmin] Sync successful:", syncMessage);
                } else {
                    syncStatus = "failed";
                    syncMessage = syncData.message || `HTTP ${syncRes.status}`;
                    console.warn("[FREJA TOOL: Garmin] Sync failed:", syncMessage);
                }
            } catch (syncErr) {
                syncStatus = "failed";
                syncMessage = syncErr.message;
                console.error("[FREJA TOOL: Garmin] Sync request error:", syncErr);
            }

            console.log(`[FREJA TOOL: Garmin] Retrieving ${days} days of health history...`);
            try {
                const url = `/api/garmin/data?days=${days}`;
                const res = await fetch(url);
                
                if (!res.ok) {
                    throw new Error(`Garmin API HTTP ${res.status}`);
                }
                
                const data = await res.json();
                if (!data || data.length === 0) {
                    return { 
                        sync_status: syncStatus,
                        sync_message: syncMessage,
                        message: "Ingen Garmin-data hittades i databasen." 
                    };
                }
                
                // Calculate basic summary statistics to help the model process faster
                let totalSteps = 0;
                let totalSleep = 0.0;
                let totalHR = 0;
                let totalCalories = 0;
                let workoutDays = 0;
                let totalWorkoutMin = 0;
                let totalBB = 0;
                let bbCount = 0;
                let totalHRV = 0;
                let hrvCount = 0;
                
                data.forEach(day => {
                    totalSteps += day.steps;
                    totalSleep += day.sleep_hours;
                    totalHR += day.resting_hr;
                    totalCalories += day.active_calories;
                    if (day.workout_type && day.workout_type !== "Ingen") {
                        workoutDays++;
                        totalWorkoutMin += day.workout_duration;
                    }
                    if (day.body_battery !== null && day.body_battery !== undefined) {
                        totalBB += day.body_battery;
                        bbCount++;
                    }
                    if (day.hrv !== null && day.hrv !== undefined) {
                        totalHRV += day.hrv;
                        hrvCount++;
                    }
                });
                
                const avgSteps = Math.round(totalSteps / data.length);
                const avgSleep = (totalSleep / data.length).toFixed(1);
                const avgHR = Math.round(totalHR / data.length);
                const avgCalories = Math.round(totalCalories / data.length);
                const avgBB = bbCount > 0 ? Math.round(totalBB / bbCount) : null;
                const avgHRV = hrvCount > 0 ? Math.round(totalHRV / hrvCount) : null;
                
                return {
                    sync_status: syncStatus,
                    sync_message: syncMessage,
                    period_days: data.length,
                    averages: {
                        avg_daily_steps: avgSteps,
                        avg_sleep_hours: avgSleep,
                        avg_resting_heart_rate: avgHR,
                        avg_active_calories: avgCalories,
                        avg_body_battery: avgBB,
                        avg_hrv: avgHRV,
                        total_workouts: workoutDays,
                        total_workout_minutes: totalWorkoutMin
                    },
                    daily_logs: data
                };
            } catch (err) {
                console.error("[FREJA TOOL: Garmin] Failed to retrieve Garmin data:", err);
                return { error: `Misslyckades att hämta Garmin-data: ${err.message}` };
            }
        }
    };
    
    // Register tool globally
    window.FrejaTools = window.FrejaTools || {};
    window.FrejaTools[garminTool.name] = garminTool;
    console.log(`[FREJA TOOLS] Module '${garminTool.name}' compiled and initialized.`);
})();
