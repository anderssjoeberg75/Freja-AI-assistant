/**
 * F.R.E.J.A. Tool: Withings Health Integration
 *
 * Fetches and aggregates health measurements from the Withings API via the backend.
 * Provides recent weight, body composition and pulse data.
 */
(function() {
    const withingsTool = {
        name: "get_withings_health",
        displayName: "Withings Hälsocoach",
        description: "Hämtar och analyserar användarens Withings-mätningar (vikt, fettprocent, benmassa, puls) för att ge hälso- och träningsrekommendationer. Standard är de senaste 7 dagarna om inte annan period efterfrågas.",
        permissionKey: "freja_tool_get_withings_health_allowed",
        declaration: {
            name: "get_withings_health",
            description: "Hämtar användarens senaste Withings mätningar (vikt, fettprocent, benmassa, puls). Parameter 'days' anger antal dagar historik att hämta (standard 7).",
            parameters: {
                type: "OBJECT",
                properties: {
                    days: {
                        type: "INTEGER",
                        description: "Antal dagar historik att hämta (standard 7)."
                    }
                }
            }
        },
        /**
         * Executes the Withings data retrieval.
         */
        async execute(args) {
            const days = (args && args.days) !== undefined ? args.days : 7;
            let syncStatus = "inte genomförd";
            let syncMessage = "";

            console.log(`[FREJA TOOL: Withings] Syncing device data first...`);
            try {
                const syncRes = await fetch('/api/withings/sync');
                const syncData = await syncRes.json();
                if (syncRes.ok && syncData.status === "success") {
                    syncStatus = "success";
                    syncMessage = syncData.message;
                    console.log(`[FREJA TOOL: Withings] Sync successful: ${syncMessage}`);
                } else {
                    syncStatus = "failed";
                    syncMessage = syncData.message || `HTTP ${syncRes.status}`;
                    console.warn(`[FREJA TOOL: Withings] Sync failed: ${syncMessage}`);
                }
            } catch (syncErr) {
                syncStatus = "failed";
                syncMessage = syncErr.message;
                console.error(`[FREJA TOOL: Withings] Sync request error:`, syncErr);
            }

            console.log(`[FREJA TOOL: Withings] Retrieving ${days} days of health history...`);
            try {
                const url = `/api/withings/data?days=${days}`;
                const res = await fetch(url);
                if (!res.ok) {
                    throw new Error(`Withings API HTTP ${res.status}`);
                }
                const data = await res.json();
                if (!data || data.length === 0) {
                    return {
                        sync_status: syncStatus,
                        sync_message: syncMessage,
                        message: "Ingen Withings-data hittades i databasen."
                    };
                }

                // Compute simple averages for quick insight
                let totalWeight = 0, totalFat = 0, totalBone = 0, totalPulse = 0;
                let countWeight = 0, countFat = 0, countBone = 0, countPulse = 0;
                data.forEach(entry => {
                    if (entry.weight !== null && entry.weight !== undefined) { totalWeight += entry.weight; countWeight++; }
                    if (entry.fat_ratio !== null && entry.fat_ratio !== undefined) { totalFat += entry.fat_ratio; countFat++; }
                    if (entry.bone_mass !== null && entry.bone_mass !== undefined) { totalBone += entry.bone_mass; countBone++; }
                    if (entry.heart_pulse !== null && entry.heart_pulse !== undefined) { totalPulse += entry.heart_pulse; countPulse++; }
                });
                const avgWeight = countWeight ? (totalWeight / countWeight).toFixed(2) : null;
                const avgFat = countFat ? (totalFat / countFat).toFixed(1) : null;
                const avgBone = countBone ? (totalBone / countBone).toFixed(2) : null;
                const avgPulse = countPulse ? Math.round(totalPulse / countPulse) : null;

                return {
                    sync_status: syncStatus,
                    sync_message: syncMessage,
                    period_days: data.length,
                    averages: {
                        avg_weight: avgWeight,
                        avg_fat_ratio: avgFat,
                        avg_bone_mass: avgBone,
                        avg_heart_pulse: avgPulse
                    },
                    measurements: data
                };
            } catch (err) {
                console.error(`[FREJA TOOL: Withings] Failed to retrieve Withings data:`, err);
                return { error: `Misslyckades att hämta Withings-data: ${err.message}` };
            }
        }
    };
    // Register tool globally
    window.FrejaTools = window.FrejaTools || {};
    window.FrejaTools[withingsTool.name] = withingsTool;
    console.log(`[FREJA TOOLS] Module '${withingsTool.name}' compiled and initialized.`);
})();
