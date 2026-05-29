"""
Cross-reference logic: merges separate event records that likely describe
the same real-world event (same venue + date within ±3 days).
Run: python cross_reference.py
"""

from __future__ import annotations

import logging
from datetime import date

import db

log = logging.getLogger(__name__)


def _parse_date(s) -> date | None:
    if not s:
        return None
    try:
        if isinstance(s, date):
            return s
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _normalize(s) -> str:
    if not s:
        return ""
    return str(s).lower().strip().replace("  ", " ")


def merge_duplicate_events() -> int:
    """
    Find events with the same venue_name and dates within 3 days,
    then merge the smaller event_id into the larger (keep higher id as canonical).
    Returns number of merges performed.
    """
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, event_date, venue_name FROM events ORDER BY id")
            events = [dict(r) for r in cur.fetchall()]

    merged: dict[int, int] = {}

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

            _do_merge(can_a, can_b)
            merged[can_b] = can_a
            merge_count += 1
            log.info("Merged event %d into %d (venue: %s)", can_b, can_a, venue_a)

    log.info("Cross-reference complete. Merges: %d", merge_count)
    return merge_count


def _do_merge(keep_id: int, discard_id: int):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            # Re-point links from discarded event to kept, skipping PK conflicts
            cur.execute("""
                UPDATE event_vendors
                SET event_id = %s
                WHERE event_id = %s
                  AND (vendor_id, post_id) NOT IN (
                      SELECT vendor_id, post_id FROM event_vendors WHERE event_id = %s
                  )
            """, (keep_id, discard_id, keep_id))
            cur.execute("DELETE FROM event_vendors WHERE event_id = %s", (discard_id,))
            cur.execute("DELETE FROM events WHERE id = %s", (discard_id,))
        conn.commit()


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db.init_db()
    merge_duplicate_events()
