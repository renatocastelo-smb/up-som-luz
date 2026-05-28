"""
Flask API server for the market intelligence dashboard.
Run: python dashboard/server.py
Opens on http://localhost:5050
"""

import sys
import os
import json
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory

sys.path.insert(0, str(Path(__file__).parent.parent))
import db

VENDORS_FILE = Path(__file__).parent.parent / "vendors.json"

app = Flask(__name__, static_folder=str(Path(__file__).parent))


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/events")
def api_events():
    limit = min(int(request.args.get("limit", 100)), 500)
    event_type = request.args.get("type") or None
    category = request.args.get("category") or None
    events = db.get_events_with_vendors(
        limit=limit, event_type=event_type, category_filter=category
    )
    return jsonify(events)


@app.route("/api/vendors")
def api_vendors():
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, handle, name, category, platform, active, notes "
            "FROM vendors ORDER BY category, name"
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/network")
def api_network():
    min_co = int(request.args.get("min", 2))
    pairs = db.get_vendor_network(min_cooccurrences=min_co)
    return jsonify(pairs)


@app.route("/api/stats")
def api_stats():
    stats = db.get_stats()
    last_run_file = Path(__file__).parent.parent / "last_run.json"
    if last_run_file.exists():
        stats["last_run"] = json.loads(last_run_file.read_text())
    return jsonify(stats)


@app.route("/review")
def review_page():
    return send_from_directory(app.static_folder, "review.html")


@app.route("/api/review/vendors")
def api_review_vendors():
    return jsonify(db.get_pending_vendors())


@app.route("/api/review/candidates")
def api_review_candidates():
    vendor_name = request.args.get("vendor")
    if not vendor_name:
        return jsonify({"error": "vendor param required"}), 400
    return jsonify(db.get_candidates_for_vendor(vendor_name))


@app.route("/api/review/confirm", methods=["POST"])
def api_confirm():
    data = request.get_json()
    vendor_name = data.get("vendor_name")
    handle = data.get("handle")
    if not vendor_name or not handle:
        return jsonify({"error": "vendor_name and handle required"}), 400

    category = db.confirm_candidate(vendor_name, handle)

    # Write to vendors.json
    vendors = json.loads(VENDORS_FILE.read_text())
    if not any(v["handle"] == handle for v in vendors):
        vendors.append({
            "handle": handle,
            "platform": "instagram",
            "category": category,
            "name": vendor_name,
            "active": True,
            "notes": "",
        })
        VENDORS_FILE.write_text(json.dumps(vendors, ensure_ascii=False, indent=2))
        db.upsert_vendor(handle=handle, platform="instagram", category=category,
                         name=vendor_name, active=True)

    return jsonify({"ok": True, "handle": handle})


@app.route("/api/review/skip", methods=["POST"])
def api_skip():
    data = request.get_json()
    vendor_name = data.get("vendor_name")
    if not vendor_name:
        return jsonify({"error": "vendor_name required"}), 400
    db.reject_all_candidates(vendor_name)
    return jsonify({"ok": True})


@app.route("/api/review/stats")
def api_review_stats():
    return jsonify(db.get_enrichment_stats())


@app.route("/api/calendar")
def api_calendar():
    """Return event counts grouped by week for the heatmap."""
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT strftime('%Y-W%W', event_date) AS week,
                   event_type,
                   COUNT(*) AS cnt
            FROM events
            WHERE event_date IS NOT NULL
            GROUP BY week, event_type
            ORDER BY week DESC
            LIMIT 200
        """).fetchall()
    return jsonify([dict(r) for r in rows])


if __name__ == "__main__":
    db.init_db()
    port = int(os.getenv("PORT", 5050))
    print(f"Dashboard running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
