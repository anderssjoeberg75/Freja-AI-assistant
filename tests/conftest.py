"""Global pytest configuration and isolation fixtures."""

import os
import sys
import tempfile
from pathlib import Path

# Ensure FREJA_ENV is testing
os.environ["FREJA_ENV"] = "testing"

# Create a temporary database file for test runs so real keys.db is never touched
temp_dir = tempfile.mkdtemp()
temp_db = Path(temp_dir) / "test_keys.db"
os.environ["FREJA_DB_FILE"] = str(temp_db)

# Initialize the test database schema
from backend.database import init_db
init_db()
