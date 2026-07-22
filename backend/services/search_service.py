"""Web search integration service."""

import logging
import urllib.parse
import httpx
from bs4 import BeautifulSoup
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

_MAX_FIELD_CHARS = 500  # Caps title/snippet length so one bloated scrape doesn't blow up tool-response tokens.


def _cap(text: str) -> str:
    return (text or "")[:_MAX_FIELD_CHARS]


def perform_ddg_api_search(query: str):
    """Query DuckDuckGo using the official duckduckgo_search DDGS library synchronously."""
    from duckduckgo_search import DDGS
    results = []
    with DDGS(timeout=10) as ddgs:
        for r in ddgs.text(query, max_results=5):
            results.append({
                'title': _cap(r.get('title', '')),
                'snippet': _cap(r.get('body', '')),
                'link': r.get('href', '')
            })
    return results

def perform_ddg_lite_search(query: str):
    """Query DuckDuckGo Lite endpoint using POST form payload synchronously.

    Deliberately does not catch its own exceptions - the caller (perform_search_detailed)
    needs to know whether a tier genuinely found zero results or whether it broke, so it can
    tell the difference apart instead of collapsing both into an empty list."""
    url = 'https://lite.duckduckgo.com/lite/'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept-Language': 'sv-SE,sv;q=0.9,en-US;q=0.8,en;q=0.7'
    }
    results = []
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
                results.append({'title': _cap(title), 'snippet': _cap(snippet), 'link': href})
                if len(results) >= 5:
                    break
    return results

def perform_ddg_html_search(query: str):
    """Query DuckDuckGo HTML endpoint using POST form payload synchronously.

    Also lets exceptions propagate - see perform_ddg_lite_search's docstring."""
    url = 'https://html.duckduckgo.com/html/'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept-Language': 'sv-SE,sv;q=0.9,en-US;q=0.8,en;q=0.7'
    }
    results = []
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
                results.append({'title': _cap(title), 'snippet': _cap(snippet), 'link': href})
                if len(results) >= 5:
                    break
    return results

async def perform_search_detailed(query: str):
    """Runs the full search pipeline, also reporting whether the backend actually broke (every
    tier raised) as opposed to genuinely finding zero results - `perform_search` collapses both
    cases to `[]` for backward compatibility, which made a broken scraper indistinguishable from
    "no results" to both callers and the LLM. Returns (results, degraded)."""
    query = (query or "").strip()
    if not query:
        return [], False

    degraded = False

    # 1. Try DDG Lite (fastest); fall back to the DDGS API only if Lite came back empty
    # (whether from an error or genuinely no hits).
    primary_results = []
    try:
        primary_results = await run_in_threadpool(perform_ddg_lite_search, query)
    except Exception as e:
        logger.warning(f"[Search Service] Primary (DDG Lite) search failed for '{query}': {e}")

    if not primary_results:
        try:
            primary_results = await run_in_threadpool(perform_ddg_api_search, query)
        except Exception as e:
            logger.warning(f"[Search Service] Primary (DDGS API) search failed for '{query}': {e}")
            degraded = True

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
        logger.info(f"[Search Service] Enriching search with secondary query: '{secondary_query}'")
        try:
            secondary_results = await run_in_threadpool(perform_ddg_lite_search, secondary_query)
        except Exception as e:
            logger.warning(f"[Search Service] Secondary search failed: {e}")

    # Combine and deduplicate. `link` is the primary dedup key; `title` is only a fallback for
    # link-less results - it must not also swallow a genuine duplicate whose link was already
    # seen but whose title differs slightly (re-fetched snippet, tracking params, etc).
    combined = []
    seen_links = set()
    for r in (primary_results or []) + (secondary_results or []):
        link = r.get('link', '').rstrip('/')
        title = r.get('title', '').strip()
        if link:
            if link not in seen_links:
                seen_links.add(link)
                combined.append(r)
        elif title and title not in seen_links:
            seen_links.add(title)
            combined.append(r)

    # 3. If still empty, try standard DDG HTML POST scraper fallback. Reaching this point with
    # every earlier tier having raised (not just found nothing) means the search backend itself
    # is broken - if this last tier also fails, that's a real degradation, not a genuine
    # zero-result search.
    if not combined:
        try:
            combined = await run_in_threadpool(perform_ddg_html_search, query)
        except Exception as e:
            logger.warning(f"[Search Service] Fallback scraper error: {e}")
            degraded = True

    logger.info(f"[Search Service] Returning {len(combined)} enriched search results.")
    return combined[:10], degraded


async def perform_search(query: str):
    """Backward-compatible wrapper: returns just the result list (existing callers/tests rely
    on this exact `list` contract). Use perform_search_detailed() to also learn whether the
    search backend degraded rather than genuinely finding zero results."""
    results, _degraded = await perform_search_detailed(query)
    return results
