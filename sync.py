#!/usr/bin/env python3
"""Weekly TikTok sync.

Triggers the Apify scraper, then appends results to the Google Sheet tracker:
  Run A -> Post Log (new posts) + Follower Growth (one row)
  Run B -> Trend Watch (hashtag scan, refreshed each run)

Usage:
  python sync.py                 # full run
  python sync.py --dry-run       # scrape + report, write nothing
  python sync.py --skip-trends   # Run A only
  python sync.py --skip-profile  # Run B only
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys

import yaml
from apify_client import ApifyClient

try:  # optional: auto-load a local .env (no-op in CI, where secrets are env vars)
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from tiktok_apify import apify_runner, mapping
from tiktok_apify.sheets import Tracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("sync")


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # Personal identifiers prefer env vars (set as GitHub repo Variables) so the
    # committed config can stay generic and the repo can be public. Fall back to
    # whatever is in config.yaml for private/local use.
    cfg["spreadsheet_id"] = os.environ.get("SPREADSHEET_ID") or cfg.get("spreadsheet_id")
    cfg["profile"]["username"] = (
        os.environ.get("TIKTOK_USERNAME") or cfg["profile"].get("username")
    )

    if not cfg["spreadsheet_id"]:
        raise SystemExit("Set SPREADSHEET_ID (env/repo variable) or spreadsheet_id in config.yaml.")
    if not cfg["profile"]["username"]:
        raise SystemExit("Set TIKTOK_USERNAME (env/repo variable) or profile.username in config.yaml.")
    return cfg


def main() -> int:
    ap = argparse.ArgumentParser(description="Weekly TikTok -> Google Sheet sync")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--dry-run", action="store_true", help="scrape but write nothing")
    ap.add_argument("--skip-trends", action="store_true")
    ap.add_argument("--skip-profile", action="store_true")
    args = ap.parse_args()

    token = os.environ.get("APIFY_TOKEN")
    if not token:
        log.error("APIFY_TOKEN is not set. Add it to your environment or .env.")
        return 1

    cfg = load_config(args.config)
    client = ApifyClient(token)
    tracker = Tracker(cfg, dry_run=args.dry_run)
    today = dt.date.today().isoformat()

    if not args.skip_profile:
        items = apify_runner.run_profile(client, cfg)
        posts = [mapping.map_post(it) for it in items]
        tracker.append_posts(posts)
        tracker.append_follower(mapping.follower_count(items), today)

    if not args.skip_trends:
        items = apify_runner.run_hashtags(client, cfg)
        trends = [mapping.map_trend(it) for it in items]
        tracker.write_trends(trends, today)

    log.info("Done%s.", " (dry-run, nothing written)" if args.dry_run else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
