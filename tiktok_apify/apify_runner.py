"""Trigger the Apify TikTok scraper and collect its dataset items.

Runs the actor synchronously (the GitHub Actions job blocks on it), then
returns the dataset rows as plain dicts.
"""

from __future__ import annotations

import logging

from apify_client import ApifyClient

log = logging.getLogger(__name__)


def _collect(client: ApifyClient, run: dict) -> list[dict]:
    dataset_id = run["defaultDatasetId"]
    return list(client.dataset(dataset_id).iterate_items())


def run_profile(client: ApifyClient, cfg: dict) -> list[dict]:
    """Run A — my own recent posts (cheap, date-filtered)."""
    p = cfg["profile"]
    run_input = {
        "profiles": [p["username"]],
        "resultsPerPage": p["results_per_page"],
        "profileSorting": "latest",
        "profileScrapeSections": ["videos"],
        "excludePinnedPosts": False,
    }
    days = p.get("recent_days")
    if days:
        # "14" -> only videos from the last 14 days (small paid add-on, keeps cost low)
        run_input["oldestPostDateUnified"] = str(days)

    log.info("Running profile scrape for @%s (last %s days)...", p["username"], days)
    run = client.actor(cfg["actor_id"]).call(run_input=run_input)
    items = _collect(client, run)
    log.info("Profile scrape returned %d videos.", len(items))
    return items


def run_hashtags(client: ApifyClient, cfg: dict) -> list[dict]:
    """Run B — hashtag/trend scan."""
    h = cfg["hashtags"]
    if not h.get("tags"):
        return []
    run_input = {
        "hashtags": h["tags"],
        "resultsPerPage": h["results_per_tag"],
        "profileScrapeSections": ["videos"],
    }
    log.info("Running hashtag scan for %s...", h["tags"])
    run = client.actor(cfg["actor_id"]).call(run_input=run_input)
    items = _collect(client, run)
    log.info("Hashtag scan returned %d videos.", len(items))
    return items
