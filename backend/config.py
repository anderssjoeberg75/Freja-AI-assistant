"""Runtime configuration for the Freja backend."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("FREJA_PORT", "8000"))
DB_FILE = os.environ.get("FREJA_DB_FILE", str(PROJECT_ROOT / "keys.db"))

# Meta Graph API version, shared by the Instagram service and its OAuth routes so the
# version is bumped in exactly one place. Overridable via env for testing new versions.
GRAPH_API_VERSION = os.environ.get("FREJA_GRAPH_API_VERSION", "v19.0")
GRAPH_BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
