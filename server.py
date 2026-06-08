"""Freja backend entry point."""

import os
import socketserver

from backend.config import PORT, PROJECT_ROOT
from backend.database import init_db
from backend.request_handler import CustomHandler


class FrejaHTTPServer(socketserver.ThreadingTCPServer):
    """Concurrent HTTP server configured for clean local restarts."""

    allow_reuse_address = True
    daemon_threads = True


def run_server():
    """Initialize storage and start the Freja HTTP server."""
    os.chdir(PROJECT_ROOT)
    init_db()
    try:
        with FrejaHTTPServer(("", PORT), CustomHandler) as httpd:
            print("===========================================================")
            print(f"  F.R.E.J.A. Neural Server running on http://localhost:{PORT}")
            print("  API keys database active")
            print("===========================================================")
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down F.R.E.J.A. Server.")
    except Exception as error:
        print(f"Server error: {error}")


if __name__ == "__main__":
    run_server()
