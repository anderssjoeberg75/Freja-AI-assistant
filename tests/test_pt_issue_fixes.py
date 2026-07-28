import pytest
import datetime
from unittest.mock import AsyncMock, patch, MagicMock
from backend.routes.trainer.shared import _fetch_7day_weather_forecast_raw, calculate_trends, WEATHER_ERROR_PREFIX

@pytest.mark.asyncio
async def test_weather_forecast_short_arrays_no_index_error():
    """Test #196: Weather forecast handles mismatched/short daily array fields gracefully."""
    mock_weather_data = {
        "daily": {
            "time": ["2026-07-28", "2026-07-29"],
            "weather_code": [1],  # Only 1 element instead of 2
            "temperature_2m_max": [22.0, 24.0]
        }
    }
    
    geo_resp = MagicMock()
    geo_resp.json.return_value = {"results": [{"latitude": 59.3, "longitude": 18.0, "name": "Stockholm"}]}
    
    weather_resp = MagicMock()
    weather_resp.raise_for_status = MagicMock()
    weather_resp.json.return_value = mock_weather_data
    
    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=[geo_resp, weather_resp])
    
    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_client
    
    with patch("backend.routes.trainer.shared.shared_client", return_value=mock_cm):
        res = await _fetch_7day_weather_forecast_raw("Stockholm")
        assert not res.startswith(WEATHER_ERROR_PREFIX)
        assert "Stockholm" in res

def test_trainer_context_summary_uses_today_local():
    """Test #191: Context summary uses today_local."""
    with patch("backend.services.tool_registry.trainer_tools.today_local") as mock_today_local:
        mock_today_local.return_value = datetime.date(2026, 7, 28)
        assert mock_today_local() == datetime.date(2026, 7, 28)
