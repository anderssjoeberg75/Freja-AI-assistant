/**
 * F.R.E.J.A. Tool: Weather Service
 * 
 * Fetches real-time weather information for any given location using the free
 * Open-Meteo API (without API Key requirement) and geocoding services.
 */
(function() {
    const weatherTool = {
        name: "get_weather",
        displayName: "Väderrapportör",
        description: "Hämtar aktuellt väder för en viss stad eller geografisk plats.",
        permissionKey: "freja_tool_get_weather_allowed",
        declaration: {
            name: "get_weather",
            description: "Hämtar aktuellt väder för en viss stad eller geografisk plats.",
            parameters: {
                type: "OBJECT",
                properties: {
                    location: {
                        type: "STRING",
                        description: "Namnet på staden eller platsen att söka efter, t.ex. Stockholm, Göteborg, London."
                    }
                },
                required: ["location"]
            }
        },
        
        /**
         * Executes the weather query.
         */
        async execute(args) {
            const location = (args && args.location) || "Stockholm";
            console.log(`[FREJA TOOL: Weather] Resolving weather forecast for '${location}'`);
            
            try {
                // 1. Resolve coordinates via Open-Meteo Geocoding API
                const geoUrl = `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(location)}&count=1&language=sv&format=json`;
                const geoRes = await fetch(geoUrl);
                
                if (!geoRes.ok) {
                    throw new Error(`Geocoding HTTP ${geoRes.status}`);
                }
                
                const geoData = await geoRes.json();
                if (!geoData.results || geoData.results.length === 0) {
                    return { error: `Kunde inte hitta platsen: '${location}' i geografiska databaser.` };
                }
                
                const result = geoData.results[0];
                const lat = result.latitude;
                const lon = result.longitude;
                const name = result.name;
                const country = result.country;
                
                // 2. Fetch real-time weather forecast coordinates (requesting wind speed in m/s)
                const weatherUrl = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&current=temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,wind_speed_10m&wind_speed_unit=ms&timezone=auto`;
                const weatherRes = await fetch(weatherUrl);
                
                if (!weatherRes.ok) {
                    throw new Error(`Weather API HTTP ${weatherRes.status}`);
                }
                
                const weatherData = await weatherRes.json();
                const current = weatherData.current;
                
                if (!current) {
                    throw new Error("No current weather data block returned.");
                }
                
                // 3. Translate WMO weather code to Swedish description
                const wmoCodes = {
                    0: "Klart väder och molnfritt",
                    1: "Mestadels klart",
                    2: "Växlande molnighet",
                    3: "Mulet",
                    45: "Dimma",
                    48: "Rimfrost-dimma",
                    51: "Lätt duggregn",
                    53: "Måttligt duggregn",
                    55: "Tätt duggregn",
                    61: "Lätt regn",
                    63: "Måttligt regn",
                    65: "Kraftigt regn",
                    71: "Lätt snöfall",
                    73: "Måttligt snöfall",
                    75: "Kraftigt snöfall",
                    77: "Snökorn",
                    80: "Lätta regnskurar",
                    81: "Måttliga regnskurar",
                    82: "Kraftiga regnskurar",
                    85: "Lätta snöskurar",
                    86: "Kraftiga snöskurar",
                    95: "Åska",
                    96: "Åska med lätt hagel",
                    99: "Åska med kraftigt hagel"
                };
                
                const desc = wmoCodes[current.weather_code] || "Atmosfäriska fluktuationer";
                
                return {
                    location: `${name}, ${country}`,
                    temperature: `${current.temperature_2m}°C`,
                    feels_like: `${current.apparent_temperature}°C`,
                    description: desc,
                    humidity: `${current.relative_humidity_2m}%`,
                    wind_speed: `${current.wind_speed_10m} m/s`,
                    is_day: current.is_day === 1 ? "Dag" : "Natt"
                };
            } catch (err) {
                console.error("[FREJA TOOL: Weather] Failed to fetch weather:", err);
                return { error: `Misslyckades att hämta väderdata: ${err.message}` };
            }
        }
    };
    
    // Register tool globally
    window.FrejaTools = window.FrejaTools || {};
    window.FrejaTools[weatherTool.name] = weatherTool;
    console.log(`[FREJA TOOLS] Module '${weatherTool.name}' compiled and initialized.`);
})();
