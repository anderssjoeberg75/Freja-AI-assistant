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

def perform_ddg_html_search(query: str):
    """Query DuckDuckGo HTML endpoint using POST form payload synchronously."""
    url = 'https://html.duckduckgo.com/html/'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept-Language': 'sv-SE,sv;q=0.9,en-US;q=0.8,en;q=0.7'
    }
    results = []
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            res = client.post(url, data={'q': query}, headers=headers)
            soup = BeautifulSoup(res.text, 'html.parser')
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
        print(f"[Search Service] DDG HTML scraper failed: {e}")
    return results

async def perform_search(query):
    """Perform a web search using DDG JSON API (primary) or HTML POST scraping (fallback)."""
    # 1. Try stable JSON API via duckduckgo_search first
    try:
        results = await run_in_threadpool(perform_ddg_api_search, query)
        if results and len(results) > 0:
            print(f"[Search Service] Successfully fetched {len(results)} results using DDGS API.")
            return results
    except Exception as api_err:
        print(f"[Search Service] DDG API search failed, falling back to HTML scraper: {api_err}")

    # 2. Fallback to HTML POST BeautifulSoup scraping
    try:
        results = await run_in_threadpool(perform_ddg_html_search, query)
        if results and len(results) > 0:
            print(f"[Search Service] Successfully fetched {len(results)} results using DDG HTML scraper.")
            return results
    except Exception as html_err:
        print(f"[Search Service] DDG HTML scraper error: {html_err}")

    return []



