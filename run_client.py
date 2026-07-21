"""F.R.E.J.A. Standalone Client Launcher Script.

Launches a dedicated local web server serving the frontend Client HUD
independently of the backend server and proxying /api/ requests to the backend.
"""

import json
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

# Most API calls are database reads that answer in milliseconds, so a short timeout is the
# right way to notice a dead backend. Model-backed endpoints are a different animal: they
# analyse months of health data and wait on Gemini, which routinely takes well over a
# minute. A flat 30s cut those off mid-flight and surfaced as "Bad Gateway", which reads as
# a broken backend rather than "your coach is still thinking" - onboarding could not
# complete a single run.
DEFAULT_PROXY_TIMEOUT = 30
LLM_PROXY_TIMEOUT = 300

# Seconds the startup reachability probe waits for the backend. Kept short: it only prints a
# status line so a misconfigured BACKEND_URL is obvious immediately instead of surfacing as a
# per-request 502 later. It never blocks or aborts startup - the client is allowed to run
# before the backend is up.
BACKEND_PROBE_TIMEOUT = 3
LLM_PATH_PREFIXES = (
    "/api/trainer/onboarding",
    "/api/trainer/generate",
    "/api/trainer/checkin",
    "/api/trainer/optimize",
    "/api/gemini/",
    "/api/chat",
    "/api/learning/",
    "/api/tools/execute",
)


def proxy_timeout_for(path: str) -> int:
    """Seconds the proxy waits for the backend, based on what the path actually does."""
    return LLM_PROXY_TIMEOUT if str(path or "").startswith(LLM_PATH_PREFIXES) else DEFAULT_PROXY_TIMEOUT


def probe_backend() -> bool:
    """Best-effort reachability check, printed once at startup.

    The client and the backend routinely live on different machines (the HUD here, the
    backend on a LAN server), so the single most common failure is BACKEND_URL pointing
    somewhere the backend isn't - a wrong host, a different subnet, or a firewall. Without a
    probe that only shows up later as a 502 on the first /api/ call, which reads as a broken
    backend rather than a misconfigured address.

    This is deliberately non-fatal: the client tolerates a backend that is not up yet (it
    returns 502 until the backend answers), so a failed probe prints a clear hint and startup
    continues. Any HTTP status counts as reachable - even a 401 means the backend answered;
    only a connection-level error (refused, timed out, no route) counts as unreachable.
    """
    target = BACKEND_TARGET.rstrip("/") + "/"
    try:
        req = urllib.request.Request(target, method="GET")
        urllib.request.urlopen(req, timeout=BACKEND_PROBE_TIMEOUT).close()
        print(f"  Backend check: OK - {BACKEND_TARGET} is reachable.")
        return True
    except urllib.error.HTTPError:
        # The backend answered with an HTTP error (e.g. 401/404); it is up and reachable.
        print(f"  Backend check: OK - {BACKEND_TARGET} is reachable.")
        return True
    except Exception as err:
        print(f"  Backend check: WARNING - could not reach {BACKEND_TARGET}: {err}")
        print( "                 /api/ calls will return 502 until the backend is reachable.")
        print( "                 Point the client at the right backend, e.g.:")
        print( "                   set BACKEND_URL=http://192.168.107.15:8000   (or edit start-freja.bat)")
        print( "                 and check the two machines can reach each other (subnet/firewall).")
        return False

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
                with urllib.request.urlopen(req, timeout=proxy_timeout_for(self.path)) as resp:
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
                # Every caller here is a JSON API client, and the HUD reads `detail` off the
                # body to explain the failure. send_error() emits an HTML page instead, so
                # the panel could only ever show a bare "HTTP 502" with no reason.
                timed_out = isinstance(err, TimeoutError) or isinstance(
                    getattr(err, "reason", None), TimeoutError
                )
                if timed_out:
                    waited = proxy_timeout_for(self.path)
                    hint = ("The coach was still generating - check the backend log and that its "
                            "Gemini key is valid." if waited == LLM_PROXY_TIMEOUT else
                            f"Check that the backend at {BACKEND_TARGET} is running and reachable.")
                    self.send_json_error(504, f"The backend did not answer within {waited}s. {hint}")
                else:
                    self.send_json_error(502, f"Could not reach the backend at {BACKEND_TARGET}: {err}")
        else:
            if method == "GET":
                super().do_GET()
            elif method == "HEAD":
                super().do_HEAD()
            else:
                self.send_error(405, f"Method {method} Not Allowed")

    def send_json_error(self, code: int, detail: str):
        """Answers with a FastAPI-shaped {"detail": ...} body so the HUD can show the reason."""
        payload = json.dumps({"detail": detail}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

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
    probe_backend()
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
