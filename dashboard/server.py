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
    return jsonify(db.get_all_vendors())


@app.route("/api/vendors/update", methods=["POST"])
def api_vendors_update():
    data = request.get_json()
    vendor_id = data.get("id")
    if not vendor_id:
        return jsonify({"error": "id required"}), 400
    db.update_vendor(
        vendor_id=int(vendor_id),
        name=data.get("name", ""),
        category=data.get("category", "other"),
        tier=data.get("tier", "A"),
        active=bool(data.get("active", True)),
        notes=data.get("notes", ""),
    )
    return jsonify({"ok": True})


@app.route("/api/vendors/add", methods=["POST"])
def api_vendors_add():
    data = request.get_json()
    handle = (data.get("handle") or "").strip()
    name = (data.get("name") or "").strip()
    if not handle or not name:
        return jsonify({"error": "handle and name required"}), 400
    vendor_id = db.upsert_vendor(
        handle=handle,
        platform=data.get("platform", "instagram"),
        category=data.get("category", "other"),
        name=name,
        tier=data.get("tier", "A"),
        active=True,
        notes=data.get("notes", ""),
    )
    return jsonify({"ok": True, "id": vendor_id})


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


@app.route("/api/review/action", methods=["POST"])
def api_review_action():
    """
    body: { vendor_name, handle, action: confirm_a | confirm_b | discard }
    confirm_a  → add as principal vendor (A tier), reject remaining candidates
    confirm_b  → add as secondary vendor (B tier), leave others pending
    discard    → remove this candidate only
    """
    data = request.get_json()
    vendor_name = data.get("vendor_name")
    handle = data.get("handle")
    action = data.get("action")

    if not vendor_name or not handle or action not in ("confirm_a", "confirm_b", "discard"):
        return jsonify({"error": "vendor_name, handle, and valid action required"}), 400

    category = db.action_candidate(vendor_name, handle, action)

    if action in ("confirm_a", "confirm_b"):
        tier = "A" if action == "confirm_a" else "B"
        db.upsert_vendor(
            handle=handle,
            platform="instagram",
            category=category,
            name=vendor_name,
            tier=tier,
            active=True,
        )

    return jsonify({"ok": True, "handle": handle, "action": action})


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
    return jsonify(db.get_calendar_data())


if __name__ == "__main__":
    db.init_db()
    port = int(os.getenv("PORT", 5050))
    print(f"Dashboard running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
