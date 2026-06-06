/**
 * F.R.E.J.A. Tool: Strava Integration
 * 
 * Fetches and aggregates workout activities (running, cycling, swimming, etc.)
 * from the local SQLite database cache (representing synced Strava API data).
 */
(function() {
    const stravaTool = {
        name: "get_strava_data",
        displayName: "Strava Träningscoach",
        description: "Hämtar och analyserar användarens Strava-träningsaktiviteter för att ge träningsråd.",
        permissionKey: "freja_tool_get_strava_data_allowed",
        declaration: {
            name: "get_strava_data",
            description: "Hämtar användarens senaste Strava-aktiviteter (namn, typ, distans, träningstid, höjdmeter, genomsnittlig puls, maxpuls och kalorier). Standard är 7 dagar historik om inte användaren uttryckligen ber om en längre period som t.ex. 14 eller 30 dagar.",
            parameters: {
                type: "OBJECT",
                properties: {
                    days: {
                        type: "INTEGER",
                        description: "Antal dagar historik att hämta (standard är 7)."
                    }
                }
            }
        },
        
        /**
         * Executes the Strava data retrieval.
         */
        async execute(args) {
            const days = (args && args.days) !== undefined ? args.days : 7;
            let syncStatus = "inte genomförd";
            let syncMessage = "";
            
            console.log(`[FREJA TOOL: Strava] Syncing device data first...`);
            try {
                const syncRes = await fetch('/api/strava/sync');
                const syncData = await syncRes.json();
                if (syncRes.ok && syncData.status === "success") {
                    syncStatus = "success";
                    syncMessage = syncData.message;
                    console.log("[FREJA TOOL: Strava] Sync successful:", syncMessage);
                } else {
                    syncStatus = "failed";
                    syncMessage = syncData.message || `HTTP ${syncRes.status}`;
                    console.warn("[FREJA TOOL: Strava] Sync failed:", syncMessage);
                }
            } catch (syncErr) {
                syncStatus = "failed";
                syncMessage = syncErr.message;
                console.error("[FREJA TOOL: Strava] Sync request error:", syncErr);
            }

            console.log(`[FREJA TOOL: Strava] Retrieving ${days} days of activity history...`);
            try {
                const url = `/api/strava/data?days=${days}`;
                const res = await fetch(url);
                
                if (!res.ok) {
                    throw new Error(`Strava API HTTP ${res.status}`);
                }
                
                const data = await res.json();
                if (!data || data.length === 0) {
                    return { 
                        sync_status: syncStatus,
                        sync_message: syncMessage,
                        message: "Inga Strava-aktiviteter hittades i databasen." 
                    };
                }
                
                // Calculate basic summary statistics
                let totalDistance = 0.0;
                let totalMovingTime = 0;
                let totalElevation = 0.0;
                let totalCalories = 0.0;
                let heartRateSum = 0.0;
                let heartRateCount = 0;
                let maxHeartRatePeak = 0;
                let activityCount = data.length;
                
                data.forEach(act => {
                    totalDistance += act.distance || 0.0;
                    totalMovingTime += act.moving_time || 0;
                    totalElevation += act.total_elevation_gain || 0.0;
                    totalCalories += act.calories || 0.0;
                    
                    if (act.average_heartrate !== null && act.average_heartrate !== undefined) {
                        heartRateSum += act.average_heartrate;
                        heartRateCount++;
                    }
                    if (act.max_heartrate !== null && act.max_heartrate !== undefined) {
                        if (act.max_heartrate > maxHeartRatePeak) {
                            maxHeartRatePeak = act.max_heartrate;
                        }
                    }
                });
                
                const avgHeartRate = heartRateCount > 0 ? Math.round(heartRateSum / heartRateCount) : null;
                
                return {
                    sync_status: syncStatus,
                    sync_message: syncMessage,
                    period_days: days,
                    summary: {
                        activity_count: activityCount,
                        total_distance_meters: Math.round(totalDistance),
                        total_distance_km: parseFloat((totalDistance / 1000).toFixed(2)),
                        total_moving_time_seconds: totalMovingTime,
                        total_moving_time_minutes: Math.round(totalMovingTime / 60),
                        total_elevation_gain_meters: Math.round(totalElevation),
                        total_calories_kcal: Math.round(totalCalories),
                        average_heartrate: avgHeartRate,
                        max_heartrate_peak: maxHeartRatePeak > 0 ? maxHeartRatePeak : null
                    },
                    activities: data
                };
            } catch (err) {
                console.error("[FREJA TOOL: Strava] Failed to retrieve Strava data:", err);
                return { error: `Misslyckades att hämta Strava-data: ${err.message}` };
            }
        }
    };
    
    // Register tool globally
    window.FrejaTools = window.FrejaTools || {};
    window.FrejaTools[stravaTool.name] = stravaTool;
    console.log(`[FREJA TOOLS] Module '${stravaTool.name}' compiled and initialized.`);
})();
