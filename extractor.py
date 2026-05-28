"""
Claude-powered extractor: reads unprocessed posts from the DB,
sends captions to claude-haiku-4-5, and saves structured event data.
Run: python extractor.py [--limit N]
"""

import os
import sys
import json
import logging
import argparse

import anthropic
from dotenv import load_dotenv

import db

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "is_event_post": {
            "type": "boolean",
            "description": "True if the post is about a real event (wedding, graduation, corporate, birthday, etc.)"
        },
        "event_type": {
            "type": ["string", "null"],
            "enum": ["wedding", "graduation", "corporate", "birthday", "other", None],
            "description": "Type of event described"
        },
        "event_date": {
            "type": ["string", "null"],
            "description": "Date of the event in YYYY-MM-DD format, or null if not mentioned"
        },
        "venue_name": {
            "type": ["string", "null"],
            "description": "Name of the venue or location where the event took place, or null"
        },
        "vendor_handles_mentioned": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of @handles mentioned in the caption or tagged (include the @ symbol)"
        }
    },
    "required": ["is_event_post", "event_type", "event_date",
                 "venue_name", "vendor_handles_mentioned"]
}

SYSTEM_PROMPT = """You are an assistant that extracts structured data from Brazilian event vendor social media posts.
Given a post caption (in Portuguese or English) and a list of tagged handles, extract the requested fields.
Be conservative with event_date: only extract if a specific date is clearly mentioned or strongly implied.
For venue_name, extract the name of the physical location/space where the event occurred (not the vendor's name).
Return valid JSON matching the provided schema exactly."""


def extract_post(post: dict) -> dict | None:
    tagged = json.loads(post.get("tagged_handles") or "[]")
    caption = post.get("caption", "").strip()

    user_content = f"""Vendor: {post['handle']} ({post['category']})
Post date: {post.get('posted_at', 'unknown')}
Tagged accounts: {', '.join(tagged) if tagged else 'none'}
Caption:
{caption}"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
            tools=[{
                "name": "extract_event",
                "description": "Extract event information from a social media post",
                "input_schema": EXTRACTION_SCHEMA,
            }],
            tool_choice={"type": "tool", "name": "extract_event"},
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == "extract_event":
                return block.input

    except anthropic.APIError as e:
        log.error("API error extracting post %s: %s", post.get("id"), e)

    return None


def process_posts(limit: int):
    posts = db.get_unprocessed_posts(limit=limit)
    log.info("Processing %d unprocessed posts…", len(posts))

    events_created = 0
    skipped = 0

    for post in posts:
        result = extract_post(post)
        if result is None:
            db.mark_post_processed(post["id"])
            skipped += 1
            continue

        if not result.get("is_event_post"):
            db.mark_post_processed(post["id"])
            skipped += 1
            continue

        event_id = db.insert_event(
            event_type=result.get("event_type"),
            event_date=result.get("event_date"),
            venue_name=result.get("venue_name"),
            confidence=1.0,
        )

        vendor_id = db.get_vendor_id_by_handle(post["handle"])
        if vendor_id:
            db.link_event_vendor(event_id, vendor_id, post["id"])

        # Link any mentioned handles that are in our vendor registry
        for mentioned_handle in result.get("vendor_handles_mentioned", []):
            handle_lower = mentioned_handle.lower()
            vid = db.get_vendor_id_by_handle(handle_lower)
            if vid and vid != vendor_id:
                db.link_event_vendor(event_id, vid, post["id"])

        db.mark_post_processed(post["id"])
        events_created += 1
        log.debug("Event %d created from post %s (%s)",
                  event_id, post["id"], post["handle"])

    log.info("Done. Events created: %d | Non-event posts skipped: %d",
             events_created, skipped)
    return events_created


def main():
    parser = argparse.ArgumentParser(description="Extract events from posts using Claude")
    parser.add_argument("--limit", type=int, default=200,
                        help="Max posts to process per run")
    args = parser.parse_args()

    db.init_db()
    process_posts(args.limit)


if __name__ == "__main__":
    main()
