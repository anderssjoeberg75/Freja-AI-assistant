"""Database Migration Tool for Freja.

Migrates all data from a source database (e.g. SQLite keys.db) to a target database
(e.g. PostgreSQL or another SQLite database) defined by SQLAlchemy models.

Usage:
    python -m scripts.migrate_db --target "postgresql://user:pass@localhost:5432/freja"
    python -m scripts.migrate_db --source "sqlite:///keys.db" --target "postgresql://user:pass@192.168.107.15:5432/frejadb"
"""

import sys
import argparse
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine, MetaData, Table
from sqlalchemy.orm import sessionmaker
from backend.models import Base
from backend.config import DB_FILE

def migrate_database(source_url: str, target_url: str):
    print(f"[FREJA DB MIGRATE] Source: {source_url}")
    print(f"[FREJA DB MIGRATE] Target: {target_url}")

    source_engine = create_engine(source_url)
    target_engine = create_engine(target_url)

    # Step 1: Ensure target tables match current models (drop existing if old schema)
    print("[FREJA DB MIGRATE] Recreating target tables...")
    try:
        Base.metadata.drop_all(bind=target_engine)
    except Exception as e:
        print(f"  Warning during drop_all: {e}")
    Base.metadata.create_all(bind=target_engine)

    source_meta = MetaData()
    source_meta.reflect(bind=source_engine)

    target_meta = MetaData()
    target_meta.reflect(bind=target_engine)

    total_rows = 0

    # Iterate over tables in dependency order
    for table_name in Base.metadata.tables.keys():
        if table_name not in source_meta.tables or table_name not in target_meta.tables:
            print(f"  - Table {table_name}: missing in source or target, skipping.")
            continue

        src_table = source_meta.tables[table_name]
        tgt_table = target_meta.tables[table_name]

        common_cols = set(src_table.columns.keys()).intersection(set(tgt_table.columns.keys()))

        with source_engine.connect() as src_conn:
            rows = src_conn.execute(src_table.select()).mappings().all()

        if not rows:
            print(f"  - Table {table_name}: 0 rows.")
            continue

        dict_rows = [{k: row[k] for k in common_cols if k in row} for row in rows]

        with target_engine.begin() as tgt_conn:
            # Delete existing rows on target for clean overwrite
            tgt_conn.execute(tgt_table.delete())
            # Insert in chunks of 500
            chunk_size = 500
            for i in range(0, len(dict_rows), chunk_size):
                chunk = dict_rows[i:i + chunk_size]
                tgt_conn.execute(tgt_table.insert(), chunk)

        print(f"  [OK] Table {table_name}: {len(rows)} rows migrated.")
        total_rows += len(rows)

    print(f"\n[FREJA DB MIGRATE] Successfully migrated {total_rows} rows total across all tables.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate Freja SQLite database to another database (PostgreSQL/SQLite).")
    parser.add_argument("--source", default=f"sqlite:///{DB_FILE}", help="Source database URL (default: keys.db)")
    parser.add_argument("--target", required=True, help="Target database URL (e.g. postgresql://user:pass@host/dbname)")

    args = parser.parse_args()
    migrate_database(args.source, args.target)
