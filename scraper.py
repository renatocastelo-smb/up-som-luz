"""
Instagram scraper using instaloader.
Fetches recent posts from each vendor in vendors.json and saves to raw_posts/.
Run: python scraper.py [--limit N] [--handle @someone]
"""

import os
import sys
import json
import time
import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone

import instaloader
from dotenv import load_dotenv

import db

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

RAW_POSTS_DIR = Path(__file__).parent / "raw_posts"
VENDORS_FILE = Path(__file__).parent / "vendors.json"
POSTS_PER_VENDOR = int(os.getenv("POSTS_PER_VENDOR", "10"))
SCRAPE_DELAY = int(os.getenv("SCRAPE_DELAY_SECONDS", "45"))


def build_loader() -> instaloader.Instaloader:
    L = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        quiet=True,
    )
    session_id = os.getenv("IG_SESSION_ID")
    username = os.getenv("IG_USERNAME")
    if session_id and username:
        # Inject the session cookie directly — avoids storing credentials as files
        L.context._session.cookies.set("sessionid", session_id, domain=".instagram.com")
        L.context.username = username
        log.info("Instagram session loaded for @%s", username)
    else:
        log.warning(
            "No IG_SESSION_ID/IG_USERNAME set — running anonymous (stricter rate limits)"
        )
    return L


def extract_tagged_handles(post: instaloader.Post) -> list[str]:
    tagged = set()
    try:
        for tag in post.tagged_users:
            tagged.add(f"@{tag}")
    except Exception:
        pass
    # Also look for @mentions in the caption
    if post.caption:
        import re
        for m in re.finditer(r"@([\w.]+)", post.caption):
            tagged.add(f"@{m.group(1)}")
    return sorted(tagged)


def scrape_handle(L: instaloader.Instaloader, handle: str,
                  vendor_id: int, limit: int) -> int:
    clean_handle = handle.lstrip("@")
    out_dir = RAW_POSTS_DIR / clean_handle
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        profile = instaloader.Profile.from_username(L.context, clean_handle)
    except instaloader.exceptions.ProfileNotExistsException:
        log.warning("Profile not found: %s", handle)
        return 0
    except Exception as e:
        log.error("Error loading profile %s: %s", handle, e)
        return 0

    saved = 0
    try:
        for post in profile.get_posts():
            if saved >= limit:
                break

            shortcode = post.shortcode
            raw_file = out_dir / f"{shortcode}.json"

            tagged = extract_tagged_handles(post)
            posted_at = post.date_utc.replace(tzinfo=timezone.utc).isoformat()
            caption = post.caption or ""
            media_url = post.url if hasattr(post, "url") else ""

            raw_data = {
                "shortcode": shortcode,
                "caption": caption,
                "posted_at": posted_at,
                "likes": post.likes,
                "tagged_handles": tagged,
                "media_url": media_url,
                "handle": handle,
            }
            raw_file.write_text(json.dumps(raw_data, ensure_ascii=False, indent=2))

            post_id = db.insert_post(
                vendor_id=vendor_id,
                shortcode=shortcode,
                caption=caption,
                posted_at=posted_at,
                likes=post.likes,
                tagged_handles=tagged,
                media_url=media_url,
                raw_path=str(raw_file),
            )
            if post_id:
                saved += 1
                log.debug("  saved %s", shortcode)

    except instaloader.exceptions.QueryReturnedNotFoundException:
        log.warning("Posts not accessible for %s", handle)
    except Exception as e:
        log.error("Error scraping %s: %s", handle, e)

    return saved


def load_vendors(handle_filter: str | None = None) -> list[dict]:
    data = json.loads(VENDORS_FILE.read_text())
    vendors = [v for v in data if v.get("active", True) and v.get("platform") == "instagram"]
    if handle_filter:
        vendors = [v for v in vendors if v["handle"] == handle_filter]
    return vendors


def sync_vendors_to_db(vendors: list[dict]):
    for v in vendors:
        db.upsert_vendor(
            handle=v["handle"],
            platform=v.get("platform", "instagram"),
            category=v["category"],
            name=v["name"],
            active=v.get("active", True),
            notes=v.get("notes", ""),
        )


def main():
    parser = argparse.ArgumentParser(description="Scrape Instagram vendors")
    parser.add_argument("--limit", type=int, default=POSTS_PER_VENDOR,
                        help="Max posts per vendor (default: POSTS_PER_VENDOR env var)")
    parser.add_argument("--handle", type=str, default=None,
                        help="Scrape only this handle (e.g. @myvendor)")
    parser.add_argument("--delay", type=int, default=SCRAPE_DELAY,
                        help="Seconds between profiles (default: SCRAPE_DELAY_SECONDS env var)")
    args = parser.parse_args()

    db.init_db()
    vendors = load_vendors(args.handle)
    if not vendors:
        log.error("No vendors found. Edit vendors.json and set active=true.")
        sys.exit(1)

    sync_vendors_to_db(vendors)
    L = build_loader()

    log.info("Scraping %d vendors (limit=%d, delay=%ds)…",
             len(vendors), args.limit, args.delay)
    total_saved = 0

    for i, vendor in enumerate(vendors):
        handle = vendor["handle"]
        vendor_id = db.get_vendor_id_by_handle(handle)
        if vendor_id is None:
            log.warning("Vendor not in DB after sync: %s", handle)
            continue

        log.info("[%d/%d] %s (%s)", i + 1, len(vendors), handle, vendor["category"])
        saved = scrape_handle(L, handle, vendor_id, args.limit)
        log.info("  → %d new posts saved", saved)
        total_saved += saved

        if i < len(vendors) - 1:
            time.sleep(args.delay)

    log.info("Done. Total new posts saved: %d", total_saved)


if __name__ == "__main__":
    main()
