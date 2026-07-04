"""F.R.E.J.A. Standalone Client Launcher Script.

Launches a dedicated local web server serving the frontend Client HUD
independently of the backend server.
"""

import os
import sys
import http.server
import socketserver
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
CLIENT_DIR = PROJECT_ROOT / "client"
PORT = int(os.environ.get("CLIENT_PORT", "5000"))

class CustomHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(CLIENT_DIR), **kwargs)

    def log_message(self, format, *args):
        # Clean custom logger
        print(f"[FREJA CLIENT] {self.address_string()} - {format % args}")

def run_client_server(auto_open=True):
    if not CLIENT_DIR.exists():
        print(f"[ERROR] Client directory not found at: {CLIENT_DIR}")
        sys.exit(1)

    url = f"http://localhost:{PORT}"
    print("===========================================================")
    print("  F.R.E.J.A. Holographic Client Interface (Standalone)")
    print(f"  Running at: {url}")
    print("  Connecting to Backend server on http://localhost:8000")
    print("===========================================================")

    if auto_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    handler = CustomHTTPRequestHandler
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", PORT), handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[FREJA CLIENT] Server stopped.")

if __name__ == "__main__":
    run_client_server()
