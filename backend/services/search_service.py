"""Web search integration service."""

import urllib.parse
import httpx
from bs4 import BeautifulSoup
from starlette.concurrency import run_in_threadpool

def perform_ddg_api_search(query: str):
    """Query DuckDuckGo using the official duckduckgo_search DDGS library synchronously."""
    from duckduckgo_search import DDGS
    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=5):
            results.append({
                'title': r.get('title', ''),
                'snippet': r.get('body', ''),
                'link': r.get('href', '')
            })
    return results

async def perform_search(query):
    """Perform a web search using DDG JSON API (primary) or HTML scraping (fallback)."""
    # 1. Try stable JSON API via duckduckgo_search first
    try:
        results = await run_in_threadpool(perform_ddg_api_search, query)
        if results:
            print(f"[Search Service] Successfully fetched {len(results)} results using DDGS API.")
            return results
    except Exception as api_err:
        print(f"[Search Service] DDG API search failed, falling back to HTML scraper: {api_err}")

    # 2. Fallback to HTML BeautifulSoup scraping
    url = 'https://html.duckduckgo.com/html/?' + urllib.parse.urlencode({'q': query})
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    results = []
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=8.0)
            html_content = response.text
            soup = BeautifulSoup(html_content, 'html.parser')
            for result_div in soup.find_all('div', class_='result'):
                title_a = result_div.find('a', class_='result__a')
                snippet_a = result_div.find('a', class_='result__snippet')
                if title_a:
                    title = title_a.get_text(strip=True)
                    href = title_a.get('href', '')
                    if 'uddg=' in href:
                        try:
                            parsed = urllib.parse.urlparse(href)
                            queries = urllib.parse.parse_qs(parsed.query)
                            if 'uddg' in queries:
                                href = queries['uddg'][0]
                        except Exception:
                            pass
                    if 'duckduckgo.com/y.js' in href:
                        continue
                    snippet = snippet_a.get_text(strip=True) if snippet_a else ''
                    results.append({'title': title, 'snippet': snippet, 'link': href})
                    if len(results) >= 5:
                        break
    except Exception as e:
        print(f'Search backend error (Scraper): {e}')
        return {'error': str(e)}
        
    return results


