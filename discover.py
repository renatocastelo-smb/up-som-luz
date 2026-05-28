"""
Enrichment agent: finds Instagram handles for vendors in vendors_to_enrich.json.
Uses DuckDuckGo to find candidate profiles, fetches IG metadata, ranks with
Claude Haiku, and stores top 5 candidates per vendor for human review.

Run: python discover.py [--limit N] [--name "Vendor Name"]
"""

import os
import re
import json
import time
import logging
import argparse
from pathlib import Path
from urllib.parse import urlparse

import requests
import anthropic
from duckduckgo_search import DDGS
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

ENRICH_FILE = Path(__file__).parent / "vendors_to_enrich.json"
VENDORS_FILE = Path(__file__).parent / "vendors.json"
CITY = "Belo Horizonte"

IG_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def extract_ig_handles_from_results(results: list[dict]) -> list[str]:
    handles = []
    seen = set()
    for r in results:
        url = r.get("href", "")
        body = r.get("body", "")
        for text in [url, body]:
            for m in re.finditer(r'instagram\.com/([A-Za-z0-9_.]{2,30})(?:/|$|\s|")', text):
                candidate = m.group(1)
                # Skip known non-profile paths
                if candidate in ("p", "reel", "explore", "stories", "accounts",
                                 "tv", "reels", "share", "s", "direct"):
                    continue
                handle = "@" + candidate
                if handle not in seen:
                    seen.add(handle)
                    handles.append(handle)
    return handles


def search_ddg(vendor_name: str) -> list[str]:
    queries = [
        f'"{vendor_name}" instagram {CITY}',
        f'{vendor_name} instagram site:instagram.com',
    ]
    all_results = []
    with DDGS() as ddgs:
        for q in queries:
            try:
                results = list(ddgs.text(q, max_results=8))
                all_results.extend(results)
                time.sleep(1.5)
            except Exception as e:
                log.warning("DDG search error for '%s': %s", q, e)
    return extract_ig_handles_from_results(all_results)


def fetch_ig_metadata(handle: str) -> dict:
    username = handle.lstrip("@")
    url = f"https://www.instagram.com/{username}/"
    base = {"handle": handle, "display_name": "", "bio": "", "followers": "", "profile_url": url}
    try:
        resp = requests.get(url, headers=IG_HEADERS, timeout=12)
        if resp.status_code != 200:
            return base
        html = resp.text

        def og(prop: str) -> str:
            m = re.search(rf'<meta\s+property="{re.escape(prop)}"\s+content="([^"]*)"', html)
            if not m:
                m = re.search(rf'<meta\s+content="([^"]*)"\s+property="{re.escape(prop)}"', html)
            return m.group(1) if m else ""

        title = og("og:title")
        desc = og("og:description")

        # title: "Display Name (@user) • Instagram photos and videos"
        display_name = ""
        if title:
            display_name = re.sub(r'\s*\(@[^)]+\).*', '', title).strip()

        # desc: "1,234 Followers, 56 Following, 78 Posts - See Instagram…" or bio text
        followers = ""
        bio = ""
        if desc:
            m_fol = re.match(r'([\d,.KkMm]+)\s+Followers', desc)
            if m_fol:
                followers = m_fol.group(1)
            if " - " in desc:
                bio_part = desc.split(" - ", 1)[1]
                bio = re.sub(r'See Instagram photos.*', '', bio_part).strip()[:160]
            else:
                bio = desc[:160]

        return {**base, "display_name": display_name, "bio": bio, "followers": followers}
    except Exception as e:
        log.debug("Could not fetch %s: %s", handle, e)
        return base


def rank_with_claude(vendor_name: str, category: str, candidates: list[dict]) -> list[dict]:
    if not candidates:
        return []
    prompt = f"""Você está identificando o Instagram correto de um fornecedor de eventos em Belo Horizonte, MG, Brasil.

Fornecedor: "{vendor_name}"
Categoria: {category}

Candidatos:
{json.dumps(candidates, ensure_ascii=False, indent=2)}

Ordene os handles do mais para o menos provável de ser o perfil oficial.
Considere: similaridade do nome, bio compatível, menção a BH/Belo Horizonte/MG.

Retorne SOMENTE um JSON array dos handles, ex: ["@handle1", "@handle2"]"""

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        m = re.search(r'\[.*?\]', text, re.DOTALL)
        if m:
            ranked_handles = json.loads(m.group())
            handle_map = {c["handle"]: c for c in candidates}
            ranked = [handle_map[h] for h in ranked_handles if h in handle_map]
            ranked += [c for c in candidates if c["handle"] not in ranked_handles]
            for i, c in enumerate(ranked):
                c["confidence"] = round(max(0.95 - i * 0.18, 0.1), 2)
            return ranked
    except Exception as e:
        log.warning("Claude ranking failed: %s", e)

    for i, c in enumerate(candidates):
        c["confidence"] = 0.5
    return candidates


def discover_one(vendor_name: str, category: str) -> int:
    log.info("  Searching: %s", vendor_name)

    handles = search_ddg(vendor_name)
    if not handles:
        log.info("    No Instagram handles found in search results.")
        return 0

    # Deduplicate and cap
    seen: set[str] = set()
    unique = []
    for h in handles:
        if h not in seen:
            seen.add(h)
            unique.append(h)

    # Fetch metadata for top candidates
    candidates = []
    for handle in unique[:6]:
        meta = fetch_ig_metadata(handle)
        candidates.append(meta)
        time.sleep(1.0)

    ranked = rank_with_claude(vendor_name, category, candidates)

    stored = 0
    for c in ranked[:5]:
        db.insert_candidate(
            vendor_name=vendor_name,
            vendor_category=category,
            platform="instagram",
            handle=c["handle"],
            display_name=c.get("display_name", ""),
            bio=c.get("bio", ""),
            followers=c.get("followers", ""),
            profile_url=c.get("profile_url", ""),
            confidence=c.get("confidence", 0.5),
        )
        stored += 1

    log.info("    Stored %d candidate(s)", stored)
    return stored


def main():
    parser = argparse.ArgumentParser(description="Discover Instagram handles for vendors")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--name", type=str, default=None,
                        help='Process only this vendor name')
    args = parser.parse_args()

    db.init_db()

    vendors: list[dict] = json.loads(ENRICH_FILE.read_text())

    if args.name:
        vendors = [v for v in vendors if args.name.lower() in v["name"].lower()]
        if not vendors:
            log.error("No vendor found matching '%s'", args.name)
            return

    if args.limit:
        vendors = vendors[:args.limit]

    total_candidates = 0
    for i, v in enumerate(vendors):
        name = v["name"]
        category = v.get("category", "planner")

        if db.has_confirmed_or_pending(name):
            log.info("[%d/%d] Skipping %s (already discovered)", i + 1, len(vendors), name)
            continue

        log.info("[%d/%d] %s (%s)", i + 1, len(vendors), name, category)
        found = discover_one(name, category)
        total_candidates += found

        if i < len(vendors) - 1:
            time.sleep(2)

    stats = db.get_enrichment_stats()
    log.info("Done. Candidates found: %d | Pending review: %d",
             total_candidates, stats["pending"])
    log.info("Open the dashboard (/review) to confirm handles.")


if __name__ == "__main__":
    main()
