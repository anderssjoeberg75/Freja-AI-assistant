/**
 * F.R.E.J.A. Tool: Google Search
 * 
 * Performs a web search using the backend's search API and returns organic search results.
 */
(function() {
    const searchTool = {
        name: "google_search",
        displayName: "Google Sökning",
        description: "Använder Google för att söka efter realtidsinformation eller besvara frågor du inte har information om.",
        permissionKey: "freja_tool_google_search_allowed",
        declaration: {
            name: "google_search",
            description: "Sök på webben efter information, nyheter eller fakta.",
            parameters: {
                type: "OBJECT",
                properties: {
                    query: {
                        type: "STRING",
                        description: "Sökfrågan att söka efter på Google."
                    }
                },
                required: ["query"]
            }
        },
        
        /**
         * Executes the search query.
         */
        async execute(args) {
            const query = (args && args.query) || "";
            console.log(`[FREJA TOOL: Search] Performing Google search for '${query}'`);
            
            try {
                const url = `/api/search?q=${encodeURIComponent(query)}`;
                const response = await fetch(url);
                if (!response.ok) {
                    throw new Error(`Search API HTTP ${response.status}`);
                }
                const results = await response.json();
                
                if (results.error) {
                    throw new Error(results.error);
                }
                
                return {
                    results: results.map(r => ({
                        title: r.title,
                        snippet: r.snippet,
                        link: r.link
                    }))
                };
            } catch (err) {
                console.error("[FREJA TOOL: Search] Google search failed:", err);
                return { error: `Misslyckades att söka på webben: ${err.message}` };
            }
        }
    };
    
    // Register tool globally
    window.FrejaTools = window.FrejaTools || {};
    window.FrejaTools[searchTool.name] = searchTool;
    console.log(`[FREJA TOOLS] Module '${searchTool.name}' compiled and initialized.`);
})();
