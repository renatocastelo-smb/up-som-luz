"""PostgreSQL database layer for market intelligence."""

import os
import json
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")


def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vendors (
                    id          SERIAL PRIMARY KEY,
                    handle      TEXT NOT NULL UNIQUE,
                    platform    TEXT NOT NULL DEFAULT 'instagram',
                    category    TEXT NOT NULL,
                    name        TEXT NOT NULL,
                    tier        TEXT NOT NULL DEFAULT 'A',
                    active      BOOLEAN NOT NULL DEFAULT TRUE,
                    notes       TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS posts (
                    id              SERIAL PRIMARY KEY,
                    vendor_id       INTEGER NOT NULL REFERENCES vendors(id),
                    post_shortcode  TEXT NOT NULL UNIQUE,
                    caption         TEXT,
                    posted_at       TIMESTAMPTZ,
                    likes           INTEGER,
                    tagged_handles  TEXT,
                    media_url       TEXT,
                    raw_path        TEXT,
                    processed       BOOLEAN NOT NULL DEFAULT FALSE,
                    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS events (
                    id              SERIAL PRIMARY KEY,
                    event_type      TEXT,
                    event_date      DATE,
                    venue_name      TEXT,
                    confidence      REAL NOT NULL DEFAULT 1.0,
                    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS event_vendors (
                    event_id    INTEGER NOT NULL REFERENCES events(id),
                    vendor_id   INTEGER NOT NULL REFERENCES vendors(id),
                    post_id     INTEGER NOT NULL REFERENCES posts(id),
                    PRIMARY KEY (event_id, vendor_id, post_id)
                );

                CREATE INDEX IF NOT EXISTS idx_posts_vendor    ON posts(vendor_id);
                CREATE INDEX IF NOT EXISTS idx_posts_posted_at ON posts(posted_at);
                CREATE INDEX IF NOT EXISTS idx_events_date     ON events(event_date);

                CREATE TABLE IF NOT EXISTS vendor_candidates (
                    id              SERIAL PRIMARY KEY,
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
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(vendor_name, handle)
                );

                CREATE INDEX IF NOT EXISTS idx_candidates_vendor ON vendor_candidates(vendor_name);
                CREATE INDEX IF NOT EXISTS idx_candidates_status  ON vendor_candidates(status);
            """)
        conn.commit()


def upsert_vendor(handle: str, platform: str, category: str, name: str,
                  tier: str = 'A', active: bool = True, notes: str = '') -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO vendors (handle, platform, category, name, tier, active, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(handle) DO UPDATE SET
                    platform  = EXCLUDED.platform,
                    category  = EXCLUDED.category,
                    name      = EXCLUDED.name,
                    tier      = EXCLUDED.tier,
                    active    = EXCLUDED.active,
                    notes     = EXCLUDED.notes
                RETURNING id
            """, (handle, platform, category, name, tier, active, notes))
            row = cur.fetchone()
        conn.commit()
        return row['id']


def insert_post(vendor_id: int, shortcode: str, caption: str, posted_at: str,
                likes: int, tagged_handles: list, media_url: str, raw_path: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO posts
                    (vendor_id, post_shortcode, caption, posted_at, likes,
                     tagged_handles, media_url, raw_path)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(post_shortcode) DO NOTHING
                RETURNING id
            """, (vendor_id, shortcode, caption, posted_at, likes,
                  json.dumps(tagged_handles), media_url, raw_path))
            row = cur.fetchone()
        conn.commit()
        return row['id'] if row else None


def get_unprocessed_posts(limit: int = 200) -> list:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.id, p.caption, p.tagged_handles, p.posted_at,
                       v.handle, v.category, v.name
                FROM posts p
                JOIN vendors v ON v.id = p.vendor_id
                WHERE p.processed = FALSE
                  AND p.caption IS NOT NULL AND p.caption != ''
                ORDER BY p.fetched_at DESC
                LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]


def mark_post_processed(post_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE posts SET processed=TRUE WHERE id=%s", (post_id,))
        conn.commit()


def insert_event(event_type, event_date, venue_name, confidence: float = 1.0) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO events (event_type, event_date, venue_name, confidence)
                VALUES (%s, %s, %s, %s)
                RETURNING id
            """, (event_type, event_date, venue_name, confidence))
            row = cur.fetchone()
        conn.commit()
        return row['id']


def link_event_vendor(event_id: int, vendor_id: int, post_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO event_vendors (event_id, vendor_id, post_id)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (event_id, vendor_id, post_id))
        conn.commit()


def get_vendor_id_by_handle(handle: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM vendors WHERE handle=%s", (handle,))
            row = cur.fetchone()
            return row['id'] if row else None


def get_events_with_vendors(limit: int = 100, event_type=None,
                            category_filter=None) -> list:
    with get_conn() as conn:
        with conn.cursor() as cur:
            where_clauses = []
            params = []

            if event_type:
                where_clauses.append("e.event_type = %s")
                params.append(event_type)

            if category_filter:
                where_clauses.append("""
                    e.id IN (
                        SELECT DISTINCT ev.event_id FROM event_vendors ev
                        JOIN vendors v ON v.id = ev.vendor_id
                        WHERE v.category = %s
                    )
                """)
                params.append(category_filter)

            where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

            cur.execute(f"""
                SELECT e.id, e.event_type, e.event_date::text, e.venue_name,
                       e.confidence, e.first_seen_at::text
                FROM events e
                {where_sql}
                ORDER BY e.event_date DESC NULLS LAST, e.first_seen_at DESC
                LIMIT %s
            """, params + [limit])
            events = cur.fetchall()

            result = []
            for ev in events:
                ev_dict = dict(ev)
                cur.execute("""
                    SELECT DISTINCT v.handle, v.name, v.category, v.tier
                    FROM event_vendors ev
                    JOIN vendors v ON v.id = ev.vendor_id
                    WHERE ev.event_id = %s
                    ORDER BY v.category, v.name
                """, (ev_dict['id'],))
                ev_dict['vendors'] = [dict(v) for v in cur.fetchall()]
                result.append(ev_dict)

            return result


def get_vendor_network(min_cooccurrences: int = 2) -> list:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT v1.handle AS handle_a, v1.name AS name_a, v1.category AS cat_a,
                       v2.handle AS handle_b, v2.name AS name_b, v2.category AS cat_b,
                       COUNT(*) AS cooccurrences
                FROM event_vendors ev1
                JOIN event_vendors ev2
                    ON ev1.event_id = ev2.event_id AND ev1.vendor_id < ev2.vendor_id
                JOIN vendors v1 ON v1.id = ev1.vendor_id
                JOIN vendors v2 ON v2.id = ev2.vendor_id
                GROUP BY ev1.vendor_id, ev2.vendor_id,
                         v1.handle, v1.name, v1.category,
                         v2.handle, v2.name, v2.category
                HAVING COUNT(*) >= %s
                ORDER BY cooccurrences DESC
            """, (min_cooccurrences,))
            return [dict(r) for r in cur.fetchall()]


def insert_candidate(vendor_name: str, vendor_category: str, platform: str,
                     handle: str, display_name: str, bio: str, followers: str,
                     profile_url: str, confidence: float):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO vendor_candidates
                    (vendor_name, vendor_category, platform, handle, display_name,
                     bio, followers, profile_url, confidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(vendor_name, handle) DO NOTHING
            """, (vendor_name, vendor_category, platform, handle, display_name,
                  bio, followers, profile_url, confidence))
        conn.commit()


def has_confirmed_or_pending(vendor_name: str) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM vendor_candidates
                WHERE vendor_name = %s
                  AND status IN ('pending', 'confirmed_a', 'confirmed_b')
            """, (vendor_name,))
            return cur.fetchone()['count'] > 0


def get_pending_vendors() -> list:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT vendor_name, vendor_category,
                       COUNT(*) AS candidate_count
                FROM vendor_candidates
                WHERE status = 'pending'
                GROUP BY vendor_name, vendor_category
                ORDER BY vendor_name
            """)
            return [dict(r) for r in cur.fetchall()]


def get_candidates_for_vendor(vendor_name: str) -> list:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, handle, display_name, bio, followers, profile_url,
                       confidence, status, platform
                FROM vendor_candidates
                WHERE vendor_name = %s AND status = 'pending'
                ORDER BY confidence DESC
            """, (vendor_name,))
            return [dict(r) for r in cur.fetchall()]


def action_candidate(vendor_name: str, handle: str, action: str) -> str:
    """
    action: confirm_a | confirm_b | discard
    confirm_a → mark confirmed_a, reject all other pending for this vendor
    confirm_b → mark confirmed_b, leave others pending
    discard   → mark this one rejected only
    Returns vendor_category.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            if action == 'confirm_a':
                cur.execute("""
                    UPDATE vendor_candidates SET status = 'confirmed_a'
                    WHERE vendor_name = %s AND handle = %s
                """, (vendor_name, handle))
                cur.execute("""
                    UPDATE vendor_candidates SET status = 'rejected'
                    WHERE vendor_name = %s AND handle != %s AND status = 'pending'
                """, (vendor_name, handle))
            elif action == 'confirm_b':
                cur.execute("""
                    UPDATE vendor_candidates SET status = 'confirmed_b'
                    WHERE vendor_name = %s AND handle = %s
                """, (vendor_name, handle))
            elif action == 'discard':
                cur.execute("""
                    UPDATE vendor_candidates SET status = 'rejected'
                    WHERE vendor_name = %s AND handle = %s
                """, (vendor_name, handle))

            cur.execute("""
                SELECT vendor_category FROM vendor_candidates
                WHERE vendor_name = %s LIMIT 1
            """, (vendor_name,))
            row = cur.fetchone()
        conn.commit()
        return row['vendor_category'] if row else 'other'


def reject_all_candidates(vendor_name: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE vendor_candidates SET status = 'rejected'
                WHERE vendor_name = %s AND status = 'pending'
            """, (vendor_name,))
        conn.commit()


def get_enrichment_stats() -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(DISTINCT vendor_name) FROM vendor_candidates")
            total = cur.fetchone()['count']
            cur.execute("""
                SELECT COUNT(DISTINCT vendor_name) FROM vendor_candidates
                WHERE status = 'pending'
            """)
            pending = cur.fetchone()['count']
            cur.execute("""
                SELECT COUNT(DISTINCT vendor_name) FROM vendor_candidates
                WHERE status IN ('confirmed_a', 'confirmed_b')
            """)
            confirmed = cur.fetchone()['count']
            return {"total": total, "pending": pending, "confirmed": confirmed}


def get_all_vendors() -> list:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, handle, name, category, platform, tier, active, notes
                FROM vendors ORDER BY tier, category, name
            """)
            return [dict(r) for r in cur.fetchall()]


def update_vendor(vendor_id: int, name: str, category: str,
                  tier: str, active: bool, notes: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE vendors
                SET name=%s, category=%s, tier=%s, active=%s, notes=%s
                WHERE id=%s
            """, (name, category, tier, active, notes, vendor_id))
        conn.commit()


def get_stats() -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM vendors WHERE active=TRUE")
            total_vendors = cur.fetchone()['count']
            cur.execute("SELECT COUNT(*) FROM posts")
            total_posts = cur.fetchone()['count']
            cur.execute("SELECT COUNT(*) FROM events")
            total_events = cur.fetchone()['count']
            cur.execute("""
                SELECT COUNT(*) FROM events
                WHERE event_date = CURRENT_DATE
            """)
            new_today = cur.fetchone()['count']
            cur.execute("""
                SELECT event_type, COUNT(*) AS cnt
                FROM events GROUP BY event_type ORDER BY cnt DESC
            """)
            by_type = [dict(r) for r in cur.fetchall()]
            return {
                "total_vendors": total_vendors,
                "total_posts": total_posts,
                "total_events": total_events,
                "new_today": new_today,
                "by_type": by_type,
            }


def get_calendar_data() -> list:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT TO_CHAR(event_date, 'IYYY-"W"IW') AS week,
                       event_type,
                       COUNT(*) AS cnt
                FROM events
                WHERE event_date IS NOT NULL
                GROUP BY week, event_type
                ORDER BY week DESC
                LIMIT 200
            """)
            return [dict(r) for r in cur.fetchall()]
