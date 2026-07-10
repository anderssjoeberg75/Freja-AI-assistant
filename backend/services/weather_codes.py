"""WMO weather interpretation codes used by the Open-Meteo API.

Open-Meteo returns weather as a numeric `weather_code`. Both the `get_weather` tool
(current conditions) and the AI Personal Trainer (7-day forecast) need to turn that
number into text, so the mapping lives here rather than being duplicated.

The text is English; Freja translates it to Swedish when it answers the user.
Unknown codes fall back to `DEFAULT_WEATHER_DESCRIPTION`.
"""

WMO_WEATHER_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Light rain",
    63: "Moderate rain",
    65: "Heavy rain",
    71: "Light snowfall",
    73: "Moderate snowfall",
    75: "Heavy snowfall",
    77: "Snow grains",
    80: "Light rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Light snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with light hail",
    99: "Thunderstorm with heavy hail",
}

DEFAULT_WEATHER_DESCRIPTION = "Atmospheric fluctuations"


def describe_weather_code(code) -> str:
    """Returns the English description for an Open-Meteo WMO code."""
    return WMO_WEATHER_CODES.get(code, DEFAULT_WEATHER_DESCRIPTION)
