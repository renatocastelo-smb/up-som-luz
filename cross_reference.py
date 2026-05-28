"""
Cross-reference logic: merges separate event records that likely describe
the same real-world event (same venue + date within ±3 days).
Run: python cross_reference.py
"""

import sqlite3
import logging
from datetime import date, timedelta

import db

log = logging.getLogger(__name__)


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _normalize(s: str | None) -> str:
    if not s:
        return ""
    return s.lower().strip().replace("  ", " ")


def merge_duplicate_events():
    """
    Find events with the same (or very similar) venue_name and dates within 3 days
    and merge the smaller event_id into the larger one (keep higher id as canonical).
    Returns number of merges performed.
    """
    conn = db.get_conn()
    events = conn.execute(
        "SELECT id, event_date, venue_name FROM events ORDER BY id"
    ).fetchall()
    conn.close()

    events = [dict(e) for e in events]
    merged: dict[int, int] = {}  # old_id -> canonical_id

    def canonical(eid: int) -> int:
        while eid in merged:
            eid = merged[eid]
        return eid

    merge_count = 0

    for i, ev_a in enumerate(events):
        can_a = canonical(ev_a["id"])
        if can_a != ev_a["id"]:
            continue

        date_a = _parse_date(ev_a["event_date"])
        venue_a = _normalize(ev_a["venue_name"])
        if not venue_a:
            continue

        for ev_b in events[i + 1:]:
            can_b = canonical(ev_b["id"])
            if can_b == can_a:
                continue

            venue_b = _normalize(ev_b["venue_name"])
            if not venue_b or venue_b != venue_a:
                continue

            date_b = _parse_date(ev_b["event_date"])
            if date_a and date_b:
                if abs((date_a - date_b).days) > 3:
                    continue
            elif date_a or date_b:
                # One has a date and the other doesn't — still likely the same event
                # if venue matches exactly
                pass

            # Merge ev_b into ev_a
            _do_merge(can_a, can_b)
            merged[can_b] = can_a
            merge_count += 1
            log.info("Merged event %d into %d (venue: %s)", can_b, can_a, venue_a)

    log.info("Cross-reference complete. Merges: %d", merge_count)
    return merge_count


def _do_merge(keep_id: int, discard_id: int):
    with db.get_conn() as conn:
        # Re-point event_vendors links from discarded event to kept event
        conn.execute("""
            UPDATE OR IGNORE event_vendors
            SET event_id = ?
            WHERE event_id = ?
        """, (keep_id, discard_id))
        # Delete any duplicate rows that now have the same (event_id, vendor_id, post_id)
        conn.execute("""
            DELETE FROM event_vendors
            WHERE event_id = ? AND rowid NOT IN (
                SELECT MIN(rowid) FROM event_vendors
                WHERE event_id = ?
                GROUP BY vendor_id, post_id
            )
        """, (discard_id, discard_id))
        conn.execute("DELETE FROM events WHERE id = ?", (discard_id,))


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db.init_db()
    merge_duplicate_events()
