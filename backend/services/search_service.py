"""Web search integration service."""

import urllib.parse
import urllib.request

def perform_search(query):
    """Perform a web search and parse the top organic results."""
    from bs4 import BeautifulSoup
    url = 'https://html.duckduckgo.com/html/?' + urllib.parse.urlencode({'q': query})
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'})
    results = []
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            html_content = response.read().decode('utf-8')
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
        print(f'Search backend error: {e}')
        return {'error': str(e)}
    return results
