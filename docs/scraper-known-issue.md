# Scraper broken — instaloader `doc_id` rejected by Instagram

**Status:** Blocked on upstream (instaloader). Not a bug in our code.
**Last investigated:** 2026-05-29
**Affected:** `scraper.py` (`python scraper.py`) — fetching posts from Instagram.

## Symptom

Every profile fetch fails. The session authenticates fine, then:

```
JSON Query to graphql/query: 403 Forbidden ... [retrying]
JSON Query to graphql/query: 403 Forbidden ... [retrying]
ERROR Error scraping @<handle>: 400 Bad Request - "fail" status,
      message "invalid request" ... graphql/query?...&doc_id=25980296051578533
→ 0 new posts saved
```

Result: **0 posts saved** for all 12 vendors.

## Root cause

Instagram changed its GraphQL contract and now rejects instaloader's hardcoded
profile-query `doc_id` (`25980296051578533`, in `instaloader/structures.py:994`)
as `"invalid request"`. This is a **known upstream issue** affecting everyone on
the current release:

- https://github.com/instaloader/instaloader/issues/2695
- https://github.com/instaloader/instaloader/issues/2682

## What was ruled out (2026-05-29)

| Hypothesis | Test | Result |
|---|---|---|
| Volume rate-limiting | Single fresh request (`--limit 1 --delay 0`) | ❌ Failed instantly — not rate-limiting |
| Outdated PyPI version | `pip index versions instaloader` | Already on latest (`4.15.1`); nothing newer published |
| Fix in git `master` | Installed `git+...@master` (commit `d3cd639`) and re-tested | ❌ Same error; `doc_id` unchanged in master — no fix yet |
| Our code / session | Session loads (`@u.s.l.renato`), vendors iterate, DB writes work | ✅ Our side is fine |

Environment was restored to the clean pinned `instaloader==4.15.1` afterward.

## Everything else works

The scraper is the only blocked piece. The PostgreSQL DB, dashboard, and the
3-action review UI (over the existing 86 candidates / 12 vendors) all work and
are independent of the scraper.

## How to retry in the future

1. Check whether issue #2695 is fixed and a **new instaloader release** is out:
   `pip index versions instaloader` (look for > 4.15.1), or watch the GitHub issue.
2. If a fix landed only in `master`/a PR (not yet released), install that commit:
   `pip install "git+https://github.com/instaloader/instaloader.git@master"`
3. Bump the pin in `requirements.txt` once a fixed version exists.
4. **Test on a single handle first** to avoid burning the rate limit:
   `python scraper.py --handle @formatoproducoes --limit 1 --delay 0`
5. If upstream stays broken long-term, consider pivoting to the official
   Instagram Graph API or a different scraping library.
