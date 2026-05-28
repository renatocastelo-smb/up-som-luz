"""SQLite database layer for market intelligence."""

import sqlite3
import json
from pathlib import Path

DB_PATH = Path(__file__).parent / "market_intel.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS vendors (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                handle      TEXT NOT NULL UNIQUE,
                platform    TEXT NOT NULL DEFAULT 'instagram',
                category    TEXT NOT NULL,
                name        TEXT NOT NULL,
                active      INTEGER NOT NULL DEFAULT 1,
                notes       TEXT
            );

            CREATE TABLE IF NOT EXISTS posts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                vendor_id       INTEGER NOT NULL REFERENCES vendors(id),
                post_shortcode  TEXT NOT NULL UNIQUE,
                caption         TEXT,
                posted_at       TEXT,
                likes           INTEGER,
                tagged_handles  TEXT,   -- JSON array of strings
                media_url       TEXT,
                raw_path        TEXT,
                processed       INTEGER NOT NULL DEFAULT 0,
                fetched_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type      TEXT,   -- wedding, graduation, corporate, birthday, other
                event_date      TEXT,   -- YYYY-MM-DD or NULL
                venue_name      TEXT,
                confidence      REAL NOT NULL DEFAULT 1.0,
                first_seen_at   TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS event_vendors (
                event_id    INTEGER NOT NULL REFERENCES events(id),
                vendor_id   INTEGER NOT NULL REFERENCES vendors(id),
                post_id     INTEGER NOT NULL REFERENCES posts(id),
                PRIMARY KEY (event_id, vendor_id, post_id)
            );

            CREATE INDEX IF NOT EXISTS idx_posts_vendor ON posts(vendor_id);
            CREATE INDEX IF NOT EXISTS idx_posts_posted_at ON posts(posted_at);
            CREATE INDEX IF NOT EXISTS idx_events_date ON events(event_date);

            CREATE TABLE IF NOT EXISTS vendor_candidates (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                vendor_name     TEXT NOT NULL,
                vendor_category TEXT NOT NULL DEFAULT 'planner',
                platform        TEXT NOT NULL DEFAULT 'instagram',
                handle          TEXT NOT NULL,
                display_name    TEXT,
                bio             TEXT,
                followers       TEXT,
                profile_url     TEXT,
                confidence      REAL NOT NULL DEFAULT 0.5,
                status          TEXT NOT NULL DEFAULT 'pending',
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_candidates_vendor ON vendor_candidates(vendor_name);
            CREATE INDEX IF NOT EXISTS idx_candidates_status ON vendor_candidates(status);
        """)


def upsert_vendor(handle: str, platform: str, category: str, name: str,
                  active: bool = True, notes: str = "") -> int:
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO vendors (handle, platform, category, name, active, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(handle) DO UPDATE SET
                platform=excluded.platform,
                category=excluded.category,
                name=excluded.name,
                active=excluded.active,
                notes=excluded.notes
        """, (handle, platform, category, name, int(active), notes))
        row = conn.execute("SELECT id FROM vendors WHERE handle=?", (handle,)).fetchone()
        return row["id"]


def insert_post(vendor_id: int, shortcode: str, caption: str, posted_at: str,
                likes: int, tagged_handles: list, media_url: str, raw_path: str) -> int | None:
    try:
        with get_conn() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO posts
                    (vendor_id, post_shortcode, caption, posted_at, likes,
                     tagged_handles, media_url, raw_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (vendor_id, shortcode, caption, posted_at, likes,
                  json.dumps(tagged_handles), media_url, raw_path))
            row = conn.execute(
                "SELECT id FROM posts WHERE post_shortcode=?", (shortcode,)
            ).fetchone()
            return row["id"] if row else None
    except sqlite3.IntegrityError:
        return None


def get_unprocessed_posts(limit: int = 200) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT p.id, p.caption, p.tagged_handles, p.posted_at,
                   v.handle, v.category, v.name
            FROM posts p
            JOIN vendors v ON v.id = p.vendor_id
            WHERE p.processed = 0 AND p.caption IS NOT NULL AND p.caption != ''
            ORDER BY p.fetched_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def mark_post_processed(post_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE posts SET processed=1 WHERE id=?", (post_id,))


def insert_event(event_type: str | None, event_date: str | None,
                 venue_name: str | None, confidence: float = 1.0) -> int:
    with get_conn() as conn:
        cursor = conn.execute("""
            INSERT INTO events (event_type, event_date, venue_name, confidence)
            VALUES (?, ?, ?, ?)
        """, (event_type, event_date, venue_name, confidence))
        return cursor.lastrowid


def link_event_vendor(event_id: int, vendor_id: int, post_id: int):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO event_vendors (event_id, vendor_id, post_id)
            VALUES (?, ?, ?)
        """, (event_id, vendor_id, post_id))


def get_vendor_id_by_handle(handle: str) -> int | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM vendors WHERE handle=?", (handle,)
        ).fetchone()
        return row["id"] if row else None


def get_events_with_vendors(limit: int = 100, event_type: str | None = None,
                            category_filter: str | None = None) -> list[dict]:
    with get_conn() as conn:
        where_clauses = []
        params: list = []

        if event_type:
            where_clauses.append("e.event_type = ?")
            params.append(event_type)

        if category_filter:
            where_clauses.append("""
                e.id IN (
                    SELECT DISTINCT ev.event_id FROM event_vendors ev
                    JOIN vendors v ON v.id = ev.vendor_id
                    WHERE v.category = ?
                )
            """)
            params.append(category_filter)

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        events = conn.execute(f"""
            SELECT e.id, e.event_type, e.event_date, e.venue_name,
                   e.confidence, e.first_seen_at
            FROM events e
            {where_sql}
            ORDER BY e.event_date DESC NULLS LAST, e.first_seen_at DESC
            LIMIT ?
        """, params + [limit]).fetchall()

        result = []
        for ev in events:
            ev_dict = dict(ev)
            vendors = conn.execute("""
                SELECT DISTINCT v.handle, v.name, v.category
                FROM event_vendors ev
                JOIN vendors v ON v.id = ev.vendor_id
                WHERE ev.event_id = ?
                ORDER BY v.category, v.name
            """, (ev_dict["id"],)).fetchall()
            ev_dict["vendors"] = [dict(v) for v in vendors]
            result.append(ev_dict)

        return result


def get_vendor_network(min_cooccurrences: int = 2) -> list[dict]:
    """Return pairs of vendors that have appeared in events together."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT v1.handle AS handle_a, v1.name AS name_a, v1.category AS cat_a,
                   v2.handle AS handle_b, v2.name AS name_b, v2.category AS cat_b,
                   COUNT(*) AS cooccurrences
            FROM event_vendors ev1
            JOIN event_vendors ev2 ON ev1.event_id = ev2.event_id
                AND ev1.vendor_id < ev2.vendor_id
            JOIN vendors v1 ON v1.id = ev1.vendor_id
            JOIN vendors v2 ON v2.id = ev2.vendor_id
            GROUP BY ev1.vendor_id, ev2.vendor_id
            HAVING COUNT(*) >= ?
            ORDER BY cooccurrences DESC
        """, (min_cooccurrences,)).fetchall()
        return [dict(r) for r in rows]


def insert_candidate(vendor_name: str, vendor_category: str, platform: str,
                     handle: str, display_name: str, bio: str, followers: str,
                     profile_url: str, confidence: float):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO vendor_candidates
                (vendor_name, vendor_category, platform, handle, display_name,
                 bio, followers, profile_url, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (vendor_name, vendor_category, platform, handle, display_name,
              bio, followers, profile_url, confidence))


def has_confirmed_or_pending(vendor_name: str) -> bool:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) FROM vendor_candidates
            WHERE vendor_name = ? AND status IN ('pending', 'confirmed')
        """, (vendor_name,)).fetchone()
        return row[0] > 0


def get_pending_vendors() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT vendor_name, vendor_category,
                   COUNT(*) AS candidate_count
            FROM vendor_candidates
            WHERE status = 'pending'
            GROUP BY vendor_name
            ORDER BY vendor_name
        """).fetchall()
        return [dict(r) for r in rows]


def get_candidates_for_vendor(vendor_name: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, handle, display_name, bio, followers, profile_url,
                   confidence, status, platform
            FROM vendor_candidates
            WHERE vendor_name = ? AND status = 'pending'
            ORDER BY confidence DESC
        """, (vendor_name,)).fetchall()
        return [dict(r) for r in rows]


def confirm_candidate(vendor_name: str, handle: str) -> str:
    """Mark one candidate as confirmed, reject the others. Returns category."""
    with get_conn() as conn:
        conn.execute("""
            UPDATE vendor_candidates SET status = 'confirmed'
            WHERE vendor_name = ? AND handle = ?
        """, (vendor_name, handle))
        conn.execute("""
            UPDATE vendor_candidates SET status = 'rejected'
            WHERE vendor_name = ? AND handle != ?
        """, (vendor_name, handle))
        row = conn.execute(
            "SELECT vendor_category FROM vendor_candidates WHERE vendor_name = ?",
            (vendor_name,)
        ).fetchone()
        return row["vendor_category"] if row else "other"


def reject_all_candidates(vendor_name: str):
    with get_conn() as conn:
        conn.execute("""
            UPDATE vendor_candidates SET status = 'rejected'
            WHERE vendor_name = ?
        """, (vendor_name,))


def get_enrichment_stats() -> dict:
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(DISTINCT vendor_name) FROM vendor_candidates"
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(DISTINCT vendor_name) FROM vendor_candidates WHERE status='pending'"
        ).fetchone()[0]
        confirmed = conn.execute(
            "SELECT COUNT(DISTINCT vendor_name) FROM vendor_candidates WHERE status='confirmed'"
        ).fetchone()[0]
        return {"total": total, "pending": pending, "confirmed": confirmed}


def get_stats() -> dict:
    with get_conn() as conn:
        total_vendors = conn.execute(
            "SELECT COUNT(*) FROM vendors WHERE active=1"
        ).fetchone()[0]
        total_posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        total_events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        new_today = conn.execute(
            "SELECT COUNT(*) FROM events WHERE date(first_seen_at)=date('now')"
        ).fetchone()[0]
        by_type = conn.execute("""
            SELECT event_type, COUNT(*) as cnt
            FROM events GROUP BY event_type ORDER BY cnt DESC
        """).fetchall()
        return {
            "total_vendors": total_vendors,
            "total_posts": total_posts,
            "total_events": total_events,
            "new_today": new_today,
            "by_type": [dict(r) for r in by_type],
        }
