import pytest
from unittest.mock import patch
from backend.services.search_service import perform_search

@pytest.mark.asyncio
async def test_perform_search_success():
    fake_results = [{
        "title": "FastAPI Web Framework",
        "snippet": "FastAPI framework, high performance, easy to learn, fast to code, ready for production",
        "link": "https://fastapi.tiangolo.com/"
    }]
    with patch("backend.services.search_service.perform_ddg_lite_search", return_value=fake_results):
        results = await perform_search("FastAPI")
        assert isinstance(results, list)
        assert len(results) >= 1
        assert results[0]["title"] == "FastAPI Web Framework"
        assert results[0]["link"] == "https://fastapi.tiangolo.com/"

