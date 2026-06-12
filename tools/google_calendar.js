/**
 * F.R.E.J.A. Tool: Google Calendar Integration
 * 
 * Provides capabilities to book (create), edit (update), delete, and list calendar events
 * directly from the secure SQLite backend (simulated/cached Google Calendar).
 */
(function() {
    const googleCalendarTool = {
        name: "manage_google_calendar",
        displayName: "Google Kalender",
        description: "Hantera användarens Google Calendar-händelser: boka, editera, ta bort och lista händelser.",
        permissionKey: "freja_tool_manage_google_calendar_allowed",
        declaration: {
            name: "manage_google_calendar",
            description: "Hanterar användarens kalenderhändelser. Du kan boka/skapa nya händelser, ändra/editera befintliga händelser, radera/ta bort händelser eller lista händelser under en viss tidsperiod (dagar).",
            parameters: {
                type: "OBJECT",
                properties: {
                    action: {
                        type: "STRING",
                        description: "Åtgärd att utföra: 'list', 'create', 'edit', eller 'delete'.",
                        enum: ["list", "create", "edit", "delete"]
                    },
                    event_id: {
                        type: "INTEGER",
                        description: "Det unika databas-ID:t för händelsen (krävs vid 'edit' och 'delete')."
                    },
                    summary: {
                        type: "STRING",
                        description: "Händelsens titel eller sammanfattning (krävs vid 'create' och 'edit')."
                    },
                    start_time: {
                        type: "STRING",
                        description: "Starttid i ISO-format (t.ex. '2026-06-12T14:00:00', krävs vid 'create' och 'edit')."
                    },
                    end_time: {
                        type: "STRING",
                        description: "Sluttid i ISO-format (t.ex. '2026-06-12T15:00:00', krävs vid 'create' och 'edit')."
                    },
                    description: {
                        type: "STRING",
                        description: "Detaljerad beskrivning eller mötesanteckningar (valfritt)."
                    },
                    location: {
                        type: "STRING",
                        description: "Plats eller möteslänk (valfritt)."
                    },
                    days: {
                        type: "INTEGER",
                        description: "Antal dagar bakåt och framåt från idag att hämta vid 'list'. Standard är 30 dagar."
                    }
                },
                required: ["action"]
            }
        },

        /**
         * Executes the calendar operations.
         */
        async execute(args) {
            if (!args || !args.action) {
                return { error: "Åtgärd (action) saknas." };
            }

            const action = args.action.toLowerCase();
            console.log(`[FREJA TOOL: Google Calendar] Executing action: ${action}`);

            try {
                if (action === "list") {
                    const days = args.days !== undefined ? args.days : 30;
                    const res = await fetch(`/api/google_calendar/data?days=${days}`);
                    if (!res.ok) throw new Error(`HTTP ${res.status}`);
                    const data = await res.json();
                    
                    if (!data || data.length === 0) {
                        return { message: "Inga kalenderhändelser hittades för den valda perioden." };
                    }
                    return {
                        message: `Hittade ${data.length} kalenderhändelser.`,
                        events: data
                    };
                }
                
                else if (action === "create" || action === "edit") {
                    if (!args.summary || !args.start_time || !args.end_time) {
                        return { error: "Titel (summary), starttid (start_time) och sluttid (end_time) krävs för att boka/editera." };
                    }
                    
                    const payload = {
                        summary: args.summary,
                        start_time: args.start_time,
                        end_time: args.end_time,
                        description: args.description || "",
                        location: args.location || ""
                    };
                    
                    if (action === "edit") {
                        if (!args.event_id) {
                            return { error: "Händelse-ID (event_id) krävs för att redigera en händelse." };
                        }
                        payload.id = parseInt(args.event_id);
                    }
                    
                    const res = await fetch("/api/google_calendar/data", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(payload)
                    });
                    
                    if (!res.ok) {
                        const errText = await res.text();
                        throw new Error(`HTTP ${res.status}: ${errText}`);
                    }
                    
                    const resData = await res.json();
                    
                    // Dispatch event to refresh UI if open
                    window.dispatchEvent(new CustomEvent("freja-calendar-updated"));
                    
                    return resData;
                }
                
                else if (action === "delete") {
                    if (!args.event_id) {
                        return { error: "Händelse-ID (event_id) krävs för att radera." };
                    }
                    
                    const res = await fetch(`/api/google_calendar/delete?id=${args.event_id}`);
                    if (!res.ok) throw new Error(`HTTP ${res.status}`);
                    const resData = await res.json();
                    
                    // Dispatch event to refresh UI if open
                    window.dispatchEvent(new CustomEvent("freja-calendar-updated"));
                    
                    return resData;
                }
                
                else {
                    return { error: `Okänd åtgärd: ${action}` };
                }
            } catch (err) {
                console.error("[FREJA TOOL: Google Calendar] Error:", err);
                return { error: `Fel vid hantering av kalender: ${err.message}` };
            }
        }
    };

    // Register tool globally
    window.FrejaTools = window.FrejaTools || {};
    window.FrejaTools[googleCalendarTool.name] = googleCalendarTool;
    console.log(`[FREJA TOOLS] Module '${googleCalendarTool.name}' compiled and initialized.`);
})();
