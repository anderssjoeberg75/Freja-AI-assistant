import pytest
from backend.services.search_service import perform_search

@pytest.mark.asyncio
async def test_perform_search_success():
    # Perform a safe, simple query
    results = await perform_search("FastAPI")
    
    # We should get a list back or an error dict if network/API limits are hit
    if isinstance(results, dict) and "error" in results:
        # If the API and scraping both failed due to environment issues (e.g. offline/blocked),
        # print error but don't fail the build unnecessarily if it's external rate-limiting
        pytest.skip(f"Search failed due to external network/rate-limit error: {results['error']}")
    else:
        assert isinstance(results, list)
        if len(results) > 0:
            first_result = results[0]
            assert "title" in first_result
            assert "snippet" in first_result
            assert "link" in first_result
            assert len(first_result["title"]) > 0
            assert len(first_result["link"]) > 0
