"""Normalise raw Apify scraper items into the fields the tracker needs.

The clockworks/tiktok-scraper returns flat keys with dots in them
(e.g. item["videoMeta.duration"], item["authorMeta.fans"]). The getter below
also tolerates genuinely nested dicts, so we survive a future output change.
"""

from __future__ import annotations


def g(item: dict, key: str, default=None):
    """Get a value by dotted key, trying a flat key first then nested access."""
    if key in item:
        return item[key]
    cur = item
    for part in key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def post_date(item: dict) -> str:
    """ISO timestamp -> 'YYYY-MM-DD'."""
    ts = g(item, "createTimeISO", "") or ""
    return ts[:10]


def hashtag_names(item: dict) -> list[str]:
    tags = g(item, "hashtags", []) or []
    out = []
    for h in tags:
        name = h.get("name") if isinstance(h, dict) else h
        if name:
            out.append(name)
    return out


def map_post(item: dict) -> dict:
    """One scraped video -> normalised dict used by the sheet writer."""
    return {
        "date": post_date(item),
        "title": (g(item, "text", "") or "").strip(),
        "views": g(item, "playCount", 0) or 0,
        "likes": g(item, "diggCount", 0) or 0,
        "comments": g(item, "commentCount", 0) or 0,
        "shares": g(item, "shareCount", 0) or 0,
        "saves": g(item, "collectCount", 0) or 0,
        "duration": g(item, "videoMeta.duration", "") or "",
        "sound": g(item, "musicMeta.musicName", "") or "",
        "hashtags": hashtag_names(item),
        "url": g(item, "webVideoUrl", "") or "",
    }


def follower_count(items: list[dict]) -> int | None:
    """Follower total comes free in every profile row (authorMeta.fans)."""
    for it in items:
        fans = g(it, "authorMeta.fans", None)
        if fans is not None:
            return int(fans)
    return None


def map_trend(item: dict) -> dict:
    """One scraped hashtag video -> a row for the Trend Watch tab."""
    return {
        "date": post_date(item),
        "author": g(item, "authorMeta.name", "") or "",
        "views": g(item, "playCount", 0) or 0,
        "likes": g(item, "diggCount", 0) or 0,
        "sound": g(item, "musicMeta.musicName", "") or "",
        "sound_original": bool(g(item, "musicMeta.musicOriginal", False)),
        "caption": (g(item, "text", "") or "").strip().replace("\n", " ")[:120],
        "hashtags": ", ".join("#" + t for t in hashtag_names(item)[:8]),
        "url": g(item, "webVideoUrl", "") or "",
    }
