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

    const stravaActivityAnalysisTool = {
        name: "get_strava_activity_analysis",
        displayName: "Strava Detaljerad Aktivitet",
        description: "Hämtar djupgående information om en specifik Strava-aktivitet inklusive varvtider (splits) och puls-/kraftzoner.",
        permissionKey: "freja_tool_get_strava_activity_analysis_allowed",
        declaration: {
            name: "get_strava_activity_analysis",
            description: "Hämtar varvtider (laps/splits) samt puls- och kraftzoner (heartrate/power distribution) för en specifik aktivitet med angivet ID. Detta gör det möjligt att analysera tempo, pacing, samt aerob och anaerob belastning under passet.",
            parameters: {
                type: "OBJECT",
                properties: {
                    activity_id: {
                        type: "STRING",
                        description: "Det unika aktivitets-ID:t (från Strava, t.ex. hämtat via get_strava_data)."
                    }
                },
                required: ["activity_id"]
            }
        },
        async execute(args) {
            if (!args || !args.activity_id) {
                return { error: "Aktivitets-ID saknas." };
            }
            console.log(`[FREJA TOOL: Strava] Retrieving details for activity ${args.activity_id}...`);
            try {
                const res = await fetch(`/api/strava/activity_details?id=${args.activity_id}`);
                if (!res.ok) {
                    throw new Error(`HTTP ${res.status}`);
                }
                return await res.json();
            } catch (err) {
                console.error("[FREJA TOOL: Strava] Failed to retrieve activity details:", err);
                return { error: `Misslyckades att hämta detaljerad aktivitet: ${err.message}` };
            }
        }
    };

    const stravaAthleteStatsTool = {
        name: "get_strava_athlete_stats",
        displayName: "Strava Atlet-statistik",
        description: "Hämtar användarens ackumulerade träningsstatistik (totalt och senaste 4 veckorna) från Strava.",
        permissionKey: "freja_tool_get_strava_athlete_stats_allowed",
        declaration: {
            name: "get_strava_athlete_stats",
            description: "Hämtar användarens ackumulerade träningsmängder, inklusive årliga (YTD) och historiska totaler samt statistik för de senaste 4 veckorna uppdelat på löpning, cykling och simning.",
            parameters: {
                type: "OBJECT",
                properties: {}
            }
        },
        async execute(args) {
            console.log(`[FREJA TOOL: Strava] Retrieving athlete stats...`);
            try {
                const res = await fetch('/api/strava/athlete_stats');
                if (!res.ok) {
                    throw new Error(`HTTP ${res.status}`);
                }
                return await res.json();
            } catch (err) {
                console.error("[FREJA TOOL: Strava] Failed to retrieve athlete stats:", err);
                return { error: `Misslyckades att hämta atlet-statistik: ${err.message}` };
            }
        }
    };
    
    // Register tools globally
    window.FrejaTools = window.FrejaTools || {};
    window.FrejaTools[stravaTool.name] = stravaTool;
    window.FrejaTools[stravaActivityAnalysisTool.name] = stravaActivityAnalysisTool;
    window.FrejaTools[stravaAthleteStatsTool.name] = stravaAthleteStatsTool;
    console.log(`[FREJA TOOLS] Module '${stravaTool.name}', '${stravaActivityAnalysisTool.name}', '${stravaAthleteStatsTool.name}' compiled and initialized.`);
})();
