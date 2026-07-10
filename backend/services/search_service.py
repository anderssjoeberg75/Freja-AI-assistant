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

def perform_ddg_lite_search(query: str):
    """Query DuckDuckGo Lite endpoint using POST form payload synchronously."""
    url = 'https://lite.duckduckgo.com/lite/'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept-Language': 'sv-SE,sv;q=0.9,en-US;q=0.8,en;q=0.7'
    }
    results = []
    try:
        with httpx.Client(timeout=6.0, follow_redirects=True) as client:
            res = client.post(url, data={'q': query}, headers=headers)
            soup = BeautifulSoup(res.text, 'html.parser')
            rows = soup.find_all('tr')
            for i, tr in enumerate(rows):
                a = tr.find('a', class_='result-link')
                if a and a.get('href'):
                    title = a.get_text(strip=True)
                    href = a.get('href', '')
                    if 'uddg=' in href:
                        try:
                            parsed = urllib.parse.urlparse(href)
                            queries = urllib.parse.parse_qs(parsed.query)
                            if 'uddg' in queries:
                                href = queries['uddg'][0]
                        except Exception:
                            pass
                    snippet = ''
                    if i + 1 < len(rows):
                        snippet_td = rows[i + 1].find('td', class_='result-snippet')
                        if snippet_td:
                            snippet = snippet_td.get_text(strip=True)
                    results.append({'title': title, 'snippet': snippet, 'link': href})
                    if len(results) >= 5:
                        break
    except Exception as e:
        print(f"[Search Service] DDG Lite scraper failed: {e}")
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
        with httpx.Client(timeout=6.0, follow_redirects=True) as client:
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

async def perform_search(query: str):
    """Perform a web search using primary engines, query enrichment, and multi-tier fallbacks."""
    query = (query or "").strip()
    if not query:
        return []

    primary_results = []
    # 1. Try DDG Lite (fastest) or DDGS API
    try:
        primary_results = await run_in_threadpool(perform_ddg_lite_search, query)
        if not primary_results:
            primary_results = await run_in_threadpool(perform_ddg_api_search, query)
    except Exception as e:
        print(f"[Search Service] Primary search failed for '{query}': {e}")

    # 2. Build a secondary query for enrichment (sports, news, facts, translation).
    #
    # The user speaks Swedish to Freja, but the English-language web has far more results for
    # factual questions. So when the query opens with a Swedish question word, we translate the
    # question stem to English and run a second search, then merge both result sets below.
    # The Swedish keys below are matched against user input - they are data, not UI copy.
    secondary_query = ""
    lower = query.lower()
    if "tour de france" in lower:
        secondary_query = "who is leading tour de france 2025 2026 yellow jersey winner"
    elif any(w in lower for w in ["vem", "vad", "hur", "när"]):
        swedish_question_stems = {
            "vem leder": "who is leading",
            "vem vann": "who won",
            "vem är": "who is",
            "vad är": "what is",
            "när är": "when is",
            "hur mycket": "how much",
            "senaste": "latest news"
        }
        for swedish_stem, english_stem in swedish_question_stems.items():
            if swedish_stem in lower:
                secondary_query = lower.replace(swedish_stem, english_stem)
                break

    secondary_results = []
    if secondary_query and secondary_query != query:
        print(f"[Search Service] Enriching search with secondary query: '{secondary_query}'")
        try:
            secondary_results = await run_in_threadpool(perform_ddg_lite_search, secondary_query)
        except Exception as e:
            print(f"[Search Service] Secondary search failed: {e}")

    # Combine and deduplicate
    combined = []
    seen_links = set()
    for r in (primary_results or []) + (secondary_results or []):
        link = r.get('link', '').rstrip('/')
        title = r.get('title', '').strip()
        if link and link not in seen_links:
            seen_links.add(link)
            combined.append(r)
        elif title and title not in seen_links:
            seen_links.add(title)
            combined.append(r)

    # 3. If still empty, try standard DDG HTML POST scraper fallback
    if not combined:
        try:
            combined = await run_in_threadpool(perform_ddg_html_search, query)
        except Exception as e:
            print(f"[Search Service] Fallback scraper error: {e}")

    print(f"[Search Service] Returning {len(combined)} enriched search results.")
    return combined[:10]





