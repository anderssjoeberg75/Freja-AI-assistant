/**
 * F.R.E.J.A. - Neural Long-Term Memory Engine (Mem0 & Virtual Local Sandbox)
 * 
 * Interacts with Mem0.ai's REST-API v3 to store, delete, and retrieve semantic engrams.
 * If no API key is specified, it gracefully runs in Virtual Sandbox mode, persisting
 * facts inside the browser's localStorage and using the Gemini model in the background
 * to filter/extract new facts from conversation exchanges.
 */

class FrejaMemoryEngine {
    constructor(geminiClientReference = null) {
        this.apiKey = "";
        this.userId = "freja_user";
        this.enabled = true;
        this.gemini = geminiClientReference;
        this.loadSettings();
    }

    /**
     * Loads Mem0 API key and status states from LocalStorage.
     */
    loadSettings() {
        const storedKey = localStorage.getItem("freja_mem0_apikey");
        const storedEnabled = localStorage.getItem("freja_mem0_enabled");
        
        if (storedKey) {
            this.apiKey = storedKey;
            console.log("[MEM0] Loaded API key from LocalStorage");
        }
        
        this.enabled = storedEnabled !== "false"; // Default is true
        this.updateCapBadge();
    }

    /**
     * Commits Mem0 settings modifications to storage and updates capabilities badges.
     */
    saveSettings(key, enabled) {
        this.apiKey = key;
        this.enabled = enabled;
        
        if (key) {
            localStorage.setItem("freja_mem0_apikey", key);
        } else {
            localStorage.removeItem("freja_mem0_apikey");
        }
        
        localStorage.setItem("freja_mem0_enabled", enabled ? "true" : "false");
        this.updateCapBadge();
    }

    /**
     * Synchronizes the capability grid badge in the HUD layout.
     */
    updateCapBadge() {
        const capBadge = document.getElementById('cap-memory');
        if (capBadge) {
            if (this.enabled) {
                capBadge.classList.add('active');
            } else {
                capBadge.classList.remove('active');
            }
        }
    }

    /**
     * Checks if the engine is running in sandbox mode (i.e. no API key configured).
     */
    isSandboxMode() {
        return !this.apiKey;
    }

    /**
     * Returns a status text descriptor of the active memory mode.
     */
    getEngineStatusText() {
        if (!this.enabled) return "OFFLINE (DEACTIVATED)";
        if (this.isSandboxMode()) return "ACTIVE (VIRTUAL SANDBOX)";
        return "ONLINE (MEM0.AI)";
    }

    /**
     * Extracts and saves facts from a user-assistant conversation exchange.
     */
    async addMemory(userMsg, assistantMsg) {
        if (!this.enabled) return null;
        
        if (!this.isSandboxMode()) {
            // Channel A: Real Mem0 REST API transaction via local backend proxy
            try {
                const response = await fetch("/api/mem0/add", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({
                        messages: [
                            { role: "user", content: userMsg },
                            { role: "assistant", content: assistantMsg }
                        ],
                        user_id: this.userId
                    })
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const data = await response.json();
                console.log("[MEM0] Memory added successfully:", data);
                return data;
            } catch (e) {
                console.error("[MEM0] API Error adding memory:", e);
            }
        }

        // Channel B: Fall back to local virtual Sandbox
        return this.addLocalSandboxMemory(userMsg, assistantMsg);
    }

    /**
     * Uses Gemini (if available) or linguistic heuristics to harvest facts, saving them locally.
     */
    async addLocalSandboxMemory(userMsg, assistantMsg) {
        let extractedFacts = [];
        
        // Method A: Ask Gemini API to parse facts out of the interaction
        if (this.gemini && this.gemini.apiKey) {
            try {
                console.log("[SANDBOX] Extracting facts using Gemini model...");
                const endpoint = `/api/gemini/generate?model=${encodeURIComponent(this.gemini.model)}`;
                const prompt = `Extrahera nya personliga fakta om användaren (som namn, ålder, yrke, preferenser, intressen) från följande samtal. Returnera dem i JSON-schemat under nyckeln 'facts'. Lämna listan tom om inga nya fakta hittas.
Konversation:
Användare: "${userMsg}"
Assistent: "${assistantMsg}"`;

                const response = await fetch(endpoint, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        contents: [{ role: "user", parts: [{ text: prompt }] }],
                        generationConfig: {
                            temperature: 0.1,
                            maxOutputTokens: 200,
                            responseMimeType: "application/json",
                            responseSchema: {
                                type: "OBJECT",
                                properties: {
                                    facts: {
                                        type: "ARRAY",
                                        items: {
                                            type: "STRING"
                                        }
                                    }
                                },
                                required: ["facts"]
                            }
                        }
                    })
                });
                
                if (response.ok) {
                    const data = await response.json();
                    const text = data.candidates?.[0]?.content?.parts?.[0]?.text;
                    if (text) {
                        const parsed = JSON.parse(text);
                        if (parsed && Array.isArray(parsed.facts)) {
                            extractedFacts = parsed.facts
                                .map(fact => fact.trim())
                                .filter(fact => fact.length > 3);
                            console.log("[SANDBOX] Gemini structured facts:", extractedFacts);
                        }
                    }
                }
            } catch (e) {
                console.warn("[SANDBOX] Gemini extraction failed, falling back to heuristics:", e);
            }
        }

        // Method B: Heuristic string processing rules as a lightweight offline backup
        if (extractedFacts.length === 0) {
            const text = userMsg.toLowerCase();
            if (text.includes("jag heter ") || text.includes("mitt namn är ")) {
                const name = userMsg.replace(/jag heter |mitt namn är /i, "").trim().split(/[ .,!]/)[0];
                extractedFacts.push(`Användarens namn är ${name}.`);
            }
            if (text.includes("gillar att ") || text.includes("tycker om att ")) {
                const activity = userMsg.substring(userMsg.toLowerCase().indexOf("att ") + 4).trim();
                extractedFacts.push(`Användaren gillar att ${activity}.`);
            } else if (text.includes("gillar ") || text.includes("älskar ")) {
                const item = userMsg.replace(/jag gillar |jag älskar /i, "").trim();
                extractedFacts.push(`Användaren gillar ${item}.`);
            }
            if (text.includes("bor i ")) {
                const city = userMsg.substring(userMsg.toLowerCase().indexOf("i ") + 2).trim().split(/[ .,!]/)[0];
                extractedFacts.push(`Användaren bor i ${city}.`);
            }
        }

        // Commit facts to localStorage if newly discovered
        if (extractedFacts.length > 0) {
            let localMemories = this.getLocalMemories();
            extractedFacts.forEach(fact => {
                if (!localMemories.some(m => m.text.toLowerCase() === fact.toLowerCase())) {
                    localMemories.push({
                        id: 'mem_' + Date.now() + '_' + Math.random().toString(36).substr(2, 5),
                        text: fact,
                        created_at: new Date().toISOString()
                    });
                }
            });
            localStorage.setItem("freja_local_memories", JSON.stringify(localMemories));
            console.log("[SANDBOX] Local memories updated:", localMemories);
            return { message: "Local facts stored." };
        }
        return null;
    }

    /**
     * Reads saved local engrams array from localStorage.
     */
    getLocalMemories() {
        const stored = localStorage.getItem("freja_local_memories");
        return stored ? JSON.parse(stored) : [];
    }

    /**
     * Manually creates a new engram card within active storage channels.
     */
    async addMemoryManual(text) {
        if (!text || !this.enabled) return false;
        
        if (!this.isSandboxMode()) {
            try {
                const response = await fetch("/api/mem0/add", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({
                        messages: [{ role: "user", content: text }],
                        user_id: this.userId
                    })
                });
                return response.ok;
            } catch (e) {
                console.error("[MEM0] API manual insert failed:", e);
            }
        }
        
        let localMemories = this.getLocalMemories();
        localMemories.push({
            id: 'mem_' + Date.now() + '_' + Math.random().toString(36).substr(2, 5),
            text: text,
            created_at: new Date().toISOString()
        });
        localStorage.setItem("freja_local_memories", JSON.stringify(localMemories));
        return true;
    }

    /**
     * Queries memories matching user queries (injects results into the system prompts).
     */
    async searchMemory(query) {
        if (!this.enabled) return [];
        
        if (!this.isSandboxMode()) {
            try {
                const response = await fetch("/api/mem0/search", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({
                        query: query,
                        filters: { user_id: this.userId },
                        top_k: 5
                    })
                });
                if (response.ok) {
                    const data = await response.json();
                    return Array.isArray(data) ? data : [];
                }
            } catch (e) {
                console.error("[MEM0] API Search failed:", e);
            }
        }

        // Sandbox: Keyword matching fallback
        const localMemories = this.getLocalMemories();
        const keywords = query.toLowerCase().split(" ").filter(w => w.length > 2);
        
        if (keywords.length === 0) return localMemories.slice(0, 5).map(m => ({ memory: m.text }));
        
        return localMemories
            .map(m => {
                let score = 0;
                keywords.forEach(keyword => {
                    if (m.text.toLowerCase().includes(keyword)) score += 1;
                });
                return { ...m, score };
            })
            .filter(m => m.score > 0)
            .sort((a, b) => b.score - a.score)
            .slice(0, 5)
            .map(m => ({ id: m.id, memory: m.text }));
    }

    /**
     * Fetches all registered memories from active channels.
     */
    async getAllMemories() {
        if (!this.enabled) return [];

        if (!this.isSandboxMode()) {
            try {
                const response = await fetch("/api/mem0/all", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({
                        filters: { user_id: this.userId }
                    })
                });
                if (response.ok) {
                    const data = await response.json();
                    return Array.isArray(data) ? data.map(m => ({ id: m.id, memory: m.memory })) : [];
                }
            } catch (e) {
                console.error("[MEM0] API Get All failed:", e);
            }
        }

        return this.getLocalMemories().map(m => ({ id: m.id, memory: m.text }));
    }

    /**
     * Purges a single memory engram by its unique identifier.
     */
    async deleteMemory(memoryId) {
        if (!this.enabled) return false;

        if (!this.isSandboxMode() && isNaN(parseInt(memoryId)) && !memoryId.startsWith("mem_")) {
            try {
                const response = await fetch(`/api/mem0/delete/${encodeURIComponent(memoryId)}`, {
                    method: "DELETE"
                });
                return response.ok;
            } catch (e) {
                console.error("[MEM0] API Delete failed:", e);
            }
        }

        let localMemories = this.getLocalMemories();
        const originalLength = localMemories.length;
        localMemories = localMemories.filter(m => m.id !== memoryId);
        localStorage.setItem("freja_local_memories", JSON.stringify(localMemories));
        return localMemories.length < originalLength;
    }

    /**
     * Clears all saved engrams from servers and local storage arrays.
     */
    async deleteAllMemories() {
        if (!this.enabled) return false;

        if (!this.isSandboxMode()) {
            try {
                const response = await fetch(`/api/mem0/wipe?user_id=${encodeURIComponent(this.userId)}`, {
                    method: "DELETE"
                });
                if (response.ok) {
                    console.log("[MEM0] Wiped server memories.");
                }
            } catch (e) {
                console.error("[MEM0] API Wipe All failed:", e);
            }
        }

        localStorage.removeItem("freja_local_memories");
        console.log("[SANDBOX] Wiped local memories.");
        return true;
    }
}
