"""F.R.E.J.A. Standalone Client Launcher Script.

Launches a dedicated local web server serving the frontend Client HUD
independently of the backend server and proxying /api/ requests to the backend.
"""

import os
import sys
import http.server
import socketserver
import webbrowser
import urllib.request
import urllib.error
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
CLIENT_DIR = PROJECT_ROOT / "client"
PORT = int(os.environ.get("CLIENT_PORT", "5000"))
BACKEND_TARGET = os.environ.get("BACKEND_URL", "http://localhost:8000")

class ProxyHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(CLIENT_DIR), **kwargs)

    def log_message(self, format, *args):
        print(f"[FREJA CLIENT] {self.address_string()} - {format % args}")

    def do_PROXY(self, method):
        if self.path.startswith("/api/"):
            target_url = f"{BACKEND_TARGET.rstrip('/')}{self.path}"
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else None

            headers = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length")}

            # Auto-inject valid access token from local DB if header is missing or masked
            token = headers.get("X-Freja-Token", "")
            if not token or "•" in token:
                try:
                    os.environ.setdefault("FREJA_ENV", "dev")
                    from backend.database import get_api_key
                    db_token = get_api_key("freja_access_token")
                    if db_token:
                        headers["X-Freja-Token"] = db_token
                except Exception:
                    pass

            req = urllib.request.Request(target_url, data=body, headers=headers, method=method)

            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    self.send_response(resp.status)
                    for k, v in resp.headers.items():
                        if k.lower() not in ("transfer-encoding", "content-length"):
                            self.send_header(k, v)
                    resp_body = resp.read()
                    self.send_header("Content-Length", str(len(resp_body)))
                    self.end_headers()
                    self.wfile.write(resp_body)
            except urllib.error.HTTPError as e:
                self.send_response(e.code)
                for k, v in e.headers.items():
                    if k.lower() not in ("transfer-encoding", "content-length"):
                        self.send_header(k, v)
                resp_body = e.read()
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)
            except Exception as err:
                self.send_error(502, f"Bad Gateway (Could not connect to backend): {err}")
        else:
            if method == "GET":
                super().do_GET()
            elif method == "HEAD":
                super().do_HEAD()
            else:
                self.send_error(405, f"Method {method} Not Allowed")

    def do_GET(self):
        if self.path == "/local-hostname":
            import json
            import socket
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"hostname": socket.gethostname()}).encode("utf-8"))
        elif self.path.startswith("/api/"):
            self.do_PROXY("GET")
        else:
            super().do_GET()

    def do_HEAD(self):
        if self.path.startswith("/api/"):
            self.do_PROXY("HEAD")
        else:
            super().do_HEAD()

    def do_POST(self):
        self.do_PROXY("POST")

    def do_PUT(self):
        self.do_PROXY("PUT")

    def do_DELETE(self):
        self.do_PROXY("DELETE")

    def do_OPTIONS(self):
        self.do_PROXY("OPTIONS")

class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True

def run_client_server(auto_open=True):
    if not CLIENT_DIR.exists():
        print(f"[ERROR] Client directory not found at: {CLIENT_DIR}")
        sys.exit(1)

    url = f"http://localhost:{PORT}"
    print("===========================================================")
    print("  F.R.E.J.A. Holographic Client Interface (Standalone)")
    print(f"  Running at: {url}")
    print(f"  Proxying /api/ to Backend server on {BACKEND_TARGET}")
    print("===========================================================")

    if auto_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    handler = ProxyHTTPRequestHandler
    ThreadingTCPServer.allow_reuse_address = True
    with ThreadingTCPServer(("0.0.0.0", PORT), handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[FREJA CLIENT] Server stopped.")

if __name__ == "__main__":
    run_client_server()
