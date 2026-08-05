"""Runtime configuration for the Freja backend."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("FREJA_PORT", "8000"))
DB_FILE = os.environ.get("FREJA_DB_FILE", str(PROJECT_ROOT / "keys.db"))

def _get_database_url():
    if "DATABASE_URL" in os.environ:
        return os.environ["DATABASE_URL"]
    if os.path.exists(DB_FILE):
        try:
            import sqlite3
            from backend.crypto_utils import decrypt_value
            conn = sqlite3.connect(DB_FILE)
            cur = conn.cursor()
            cur.execute("SELECT key_value FROM api_keys WHERE key_name = 'freja_database_url'")
            row = cur.fetchone()
            conn.close()
            if row and row[0]:
                val = decrypt_value(row[0]).strip()
                if val:
                    return val
        except Exception:
            pass
    return f"sqlite:///{DB_FILE}"

DATABASE_URL = _get_database_url()
JWT_SECRET = os.environ.get("JWT_SECRET", "freja_jwt_secret_key_change_in_production")
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
JWT_EXPIRATION_MINUTES = int(os.environ.get("JWT_EXPIRATION_MINUTES", "10080")) # 7 days

# Meta Graph API version, shared by the Instagram service and its OAuth routes so the
# version is bumped in exactly one place. Overridable via env for testing new versions.
GRAPH_API_VERSION = os.environ.get("FREJA_GRAPH_API_VERSION", "v19.0")
GRAPH_BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# Seed/placeholder value written into keys.db for freja_eleven_apikey before a real ElevenLabs
# key is configured. client/js/ui-init.js has its own copy of this same literal (it clears a
# stale localStorage copy on load) - keep both in sync if this is ever rotated.
ELEVENLABS_PLACEHOLDER_KEY_HASH = "e4984cf824dd4f39f489d3dd4ed6f22518700d4ad0f9a8077a7915a85b23b81d"
