"""Import existing SQLite data into Supabase. Run once."""
import sqlite3
import json
from dotenv import load_dotenv
load_dotenv()
import db

SQLITE_PATH = "market_intel.db"


def fix_event_date_type():
    """Idempotent: correct events.event_date to DATE on databases where an
    earlier schema created it as TEXT (CREATE TABLE IF NOT EXISTS won't fix it)."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT data_type FROM information_schema.columns
                WHERE table_name = 'events' AND column_name = 'event_date'
            """)
            row = cur.fetchone()
            if row and row["data_type"] != "date":
                cur.execute(
                    "ALTER TABLE events ALTER COLUMN event_date TYPE date "
                    "USING event_date::date"
                )
                print("Fixed events.event_date -> DATE")
        conn.commit()


def migrate():
    db.init_db()
    fix_event_date_type()

    src = sqlite3.connect(SQLITE_PATH)
    src.row_factory = sqlite3.Row

    # Migrate vendor_candidates
    candidates = src.execute("SELECT * FROM vendor_candidates").fetchall()
    print(f"Migrating {len(candidates)} candidates...")
    for c in candidates:
        db.insert_candidate(
            vendor_name=c["vendor_name"],
            vendor_category=c["vendor_category"],
            platform=c["platform"],
            handle=c["handle"],
            display_name=c["display_name"] or "",
            bio=c["bio"] or "",
            followers=c["followers"] or "",
            profile_url=c["profile_url"] or "",
            confidence=c["confidence"],
        )

    # Migrate confirmed vendors from vendors.json
    import json
    from pathlib import Path
    vendors = json.loads(Path("vendors.json").read_text())
    print(f"Migrating {len(vendors)} vendors...")
    for v in vendors:
        db.upsert_vendor(
            handle=v["handle"],
            platform=v.get("platform", "instagram"),
            category=v["category"],
            name=v["name"],
            tier=v.get("tier", "A"),
            active=v.get("active", True),
            notes=v.get("notes", ""),
        )

    src.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()