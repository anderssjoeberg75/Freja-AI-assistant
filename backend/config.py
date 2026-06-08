"""Runtime configuration for the Freja backend."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("FREJA_PORT", "8000"))
DB_FILE = os.environ.get("FREJA_DB_FILE", str(PROJECT_ROOT / "keys.db"))
