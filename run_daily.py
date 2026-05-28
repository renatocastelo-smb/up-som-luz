"""
Daily orchestrator: scrape → extract → cross-reference → log summary.
Run: python run_daily.py
"""

import json
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import db
import cross_reference

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def run_step(label: str, module: str, extra_args: list[str] | None = None):
    cmd = [sys.executable, module] + (extra_args or [])
    log.info("=== %s ===", label)
    result = subprocess.run(cmd, cwd=Path(__file__).parent, capture_output=False)
    if result.returncode != 0:
        log.error("Step '%s' failed with code %d", label, result.returncode)
        sys.exit(result.returncode)


def main():
    start = datetime.now()
    log.info("Daily market intel run started: %s", start.isoformat())

    db.init_db()

    run_step("Scrape Instagram", "scraper.py")
    run_step("Extract events with Claude", "extractor.py")

    log.info("=== Cross-referencing events ===")
    merges = cross_reference.merge_duplicate_events()

    stats = db.get_stats()
    stats["merges_today"] = merges
    stats["run_at"] = start.isoformat()
    stats["duration_seconds"] = round((datetime.now() - start).total_seconds())

    summary_file = Path(__file__).parent / "last_run.json"
    summary_file.write_text(json.dumps(stats, indent=2))

    log.info("=== Summary ===")
    log.info("  Vendors monitored : %d", stats["total_vendors"])
    log.info("  Total posts in DB : %d", stats["total_posts"])
    log.info("  Total events      : %d", stats["total_events"])
    log.info("  New events today  : %d", stats["new_today"])
    log.info("  Merges performed  : %d", merges)
    log.info("  Duration          : %ds", stats["duration_seconds"])
    log.info("Run complete.")


if __name__ == "__main__":
    main()
