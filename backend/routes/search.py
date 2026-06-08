"""Search HTTP route handlers."""

import json
import urllib.parse

from backend.services.search_service import perform_search


def handle_get_search(handler):
    parsed_path = urllib.parse.urlparse(handler.path)
    params = urllib.parse.parse_qs(parsed_path.query)
    query = params.get('q', [''])[0].strip()
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
    handler.end_headers()
    if not query:
        handler.wfile.write(json.dumps([]).encode('utf-8'))
        return
    results = perform_search(query)
    handler.wfile.write(json.dumps(results).encode('utf-8'))
