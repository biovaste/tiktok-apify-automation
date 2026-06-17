"""Google Sheet writer for the TikTok tracker.

Robust to differing Post Log layouts: it finds the header row and
maps columns by header name at runtime rather than hardcoding positions.
Dedup uses the Link column (webVideoUrl) when present, else date+title — the
same key the original CSV importer used, so we never double-add a post that was
logged manually.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os

import gspread
from google.oauth2.service_account import Credentials

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Header text -> the normalised field we map into it. Matched case-insensitively
# by substring, so "Post Date" and "Date" both resolve to "date", etc.
POST_HEADER_ALIASES = {
    "date": "date",
    "title": "title",
    "view": "views",
    "like": "likes",
    "comment": "comments",
    "share": "shares",
    "save": "saves",
    "link": "url",
    "url": "url",
    "pillar": "pillar",
    "hook": "hook",
}

# Weekly Log (v2 tracker) — one aggregate row per week. Aliases are matched by
# substring against the header text; more specific phrases first.
WEEKLY_HEADER_ALIASES = {
    "week start": "week_start",
    "week end": "week_end",
    "week #": "week_num",
    "new follower": "new_followers",
    "total follower": "total_followers",
    "posts this week": "posts",
    "total views": "total_views",
    "avg views": "avg_views",
    "best performing": "best_post",
    "best post views": "best_views",
    "total likes": "total_likes",
    "total comments": "total_comments",
    "total shares": "total_shares",
}


def _client() -> gspread.Client:
    """Authorise from a JSON file path or inline JSON (for CI secrets)."""
    inline = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if inline:
        info = json.loads(inline)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "service_account.json")
        creds = Credentials.from_service_account_file(path, scopes=SCOPES)
    return gspread.authorize(creds)


def _dedup_key(date: str, title: str) -> str:
    """Mirror of the original importer's key, for cross-compatibility."""
    t = " ".join(str(title).split())[:40].lower()
    return f"{date}::{t}"


class Tracker:
    def __init__(self, cfg: dict, dry_run: bool = False):
        self.cfg = cfg
        self.dry_run = dry_run
        self.ss = _client().open_by_key(cfg["spreadsheet_id"])

    # ── worksheet / header helpers ──────────────────────────────────────
    def _worksheet(self, name_substr: str):
        target = name_substr.lower()
        for ws in self.ss.worksheets():
            if target in ws.title.lower():
                return ws
        return None

    @staticmethod
    def _locate_header(values: list[list[str]], must_have: str, aliases: dict,
                       max_rows: int = 8):
        """Return (0-based header row index, {field: 0-based col}) by scanning the
        first `max_rows` rows for a row containing `must_have`, mapping columns by
        the given alias table."""
        for r in range(min(max_rows, len(values))):
            cells = [c.strip().lower() for c in values[r]]
            if any(must_have in c for c in cells):
                colmap: dict[str, int] = {}
                for ci, cell in enumerate(cells):
                    for alias, field in aliases.items():
                        if alias in cell and field not in colmap:
                            colmap[field] = ci
                return r, colmap
        return None, {}

    @classmethod
    def _find_header(cls, values: list[list[str]], must_have: str):
        return cls._locate_header(values, must_have, POST_HEADER_ALIASES)

    # ── Run A: Post Log ─────────────────────────────────────────────────
    def append_posts(self, posts: list[dict]) -> int:
        ws = self._worksheet(self.cfg["sheets"]["post_log"])
        if ws is None:
            log.warning("Post Log tab not found — skipping post append.")
            return 0

        values = ws.get_all_values()
        hdr, colmap = self._find_header(values, "view")
        if hdr is None or "date" not in colmap:
            log.warning("Could not locate Post Log headers — skipping.")
            return 0

        # Ensure a Saves column exists (v3 ships without one). Added at the end of
        # the header row so existing columns, formulas and dashboard ranges don't shift.
        header_cells = values[hdr]
        colmap = self._ensure_field(ws, hdr, header_cells, colmap, "saves", "Saves")

        data_rows = values[hdr + 1:]
        has_link = "url" in colmap

        # Build dedup set + find the last row that actually holds data.
        existing: set[str] = set()
        last_data_offset = -1
        for i, row in enumerate(data_rows):
            date = row[colmap["date"]] if colmap["date"] < len(row) else ""
            title = row[colmap["title"]] if colmap.get("title", 99) < len(row) else ""
            link = row[colmap["url"]] if has_link and colmap["url"] < len(row) else ""
            if not (date or title or link):
                continue
            last_data_offset = i
            existing.add(link.strip() if (has_link and link.strip()) else _dedup_key(date, title))

        next_row = hdr + 1 + last_data_offset + 2  # 1-based sheet row for the first new entry
        width = max(colmap.values()) + 1

        new_rows, added = [], 0
        for p in posts:
            key = p["url"].strip() if (has_link and p["url"].strip()) else _dedup_key(p["date"], p["title"])
            if key in existing:
                continue
            existing.add(key)
            row_num = next_row + len(new_rows)
            row = [""] * width
            self._place(row, colmap, "date", p["date"])
            self._place(row, colmap, "title", p["title"])
            self._place(row, colmap, "views", p["views"])
            self._place(row, colmap, "likes", p["likes"])
            self._place(row, colmap, "comments", p["comments"])
            self._place(row, colmap, "shares", p["shares"])
            self._place(row, colmap, "saves", p["saves"])
            self._place(row, colmap, "url", p["url"])
            # Eng Rate is a v3 formula column; only fill if present and views > 0.
            self._place_eng_rate(row, values[hdr], colmap, row_num, p)
            new_rows.append(row)
            added += 1

        if not new_rows:
            log.info("Post Log: no new posts (all %d already logged).", len(posts))
            return 0
        if self.dry_run:
            log.info("[dry-run] Would append %d new post(s) to Post Log at row %d "
                     "(with Pillar/Hook dropdowns).", added, next_row)
            return added

        ws.update(range_name=f"A{next_row}", values=new_rows, value_input_option="USER_ENTERED")
        self._apply_dropdowns(ws, colmap, next_row, len(new_rows))
        log.info("Post Log: appended %d new post(s).", added)
        return added

    def _apply_dropdowns(self, ws, colmap: dict, start_row: int, n_rows: int) -> None:
        """Attach Pillar / Hook Type dropdowns (suggestions, not strict) to the
        just-appended rows, so new auto-added posts stay quick to tag."""
        tagging = self.cfg.get("tagging") or {}
        specs = [("pillar", tagging.get("pillar_options")),
                 ("hook", tagging.get("hook_options"))]
        requests = []
        for field, options in specs:
            if field not in colmap or not options:
                continue
            col = colmap[field]
            requests.append({
                "setDataValidation": {
                    "range": {
                        "sheetId": ws.id,
                        "startRowIndex": start_row - 1,
                        "endRowIndex": start_row - 1 + n_rows,
                        "startColumnIndex": col,
                        "endColumnIndex": col + 1,
                    },
                    "rule": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [{"userEnteredValue": o} for o in options],
                        },
                        "showCustomUi": True,
                        "strict": False,
                    },
                }
            })
        if not requests:
            return
        ws.spreadsheet.batch_update({"requests": requests})
        log.info("Applied Pillar/Hook dropdowns to %d new Post Log row(s).", n_rows)

    def _ensure_field(self, ws, hdr_idx: int, header_cells: list, colmap: dict,
                      field: str, label: str) -> dict:
        """If `field` has no column, append one labelled `label` at the end of the
        header row and register it in colmap. Returns the (possibly updated) colmap."""
        if field in colmap:
            return colmap
        new_idx = max(len(header_cells), max(colmap.values()) + 1)
        colmap[field] = new_idx
        a1 = gspread.utils.rowcol_to_a1(hdr_idx + 1, new_idx + 1)
        if self.dry_run:
            log.info("[dry-run] Would add '%s' column to Post Log at %s.", label, a1)
        else:
            ws.update(range_name=a1, values=[[label]], value_input_option="USER_ENTERED")
            log.info("Added '%s' column to Post Log at %s.", label, a1)
        return colmap

    @staticmethod
    def _place(row: list, colmap: dict, field: str, value):
        if field in colmap:
            row[colmap[field]] = value

    @staticmethod
    def _place_eng_rate(row, header_cells, colmap, row_num, p):
        """Fill an 'Eng Rate' column with the same formula the v3 builder uses."""
        for ci, cell in enumerate(header_cells):
            if "eng" in cell.strip().lower():
                if p["views"]:
                    v = gspread.utils.rowcol_to_a1
                    views = v(row_num, colmap["views"] + 1)
                    likes = v(row_num, colmap["likes"] + 1)
                    comments = v(row_num, colmap["comments"] + 1)
                    shares = v(row_num, colmap["shares"] + 1)
                    row[ci] = f"=ROUND(({likes}+{comments}+{shares})/{views}*100,2)"
                return

    # ── Run A: Weekly Log ───────────────────────────────────────────────
    @staticmethod
    def _cell(row: list, colmap: dict, field: str) -> str:
        ci = colmap.get(field)
        return row[ci] if ci is not None and ci < len(row) else ""

    @staticmethod
    def _as_int(value) -> int | None:
        try:
            return int(str(value).replace(",", "").strip())
        except (ValueError, TypeError):
            return None

    def append_weekly(self, posts: list[dict], followers: int | None, run_date: str) -> bool:
        """Append one weekly aggregate row to the Weekly Log tab.

        The 7-day window ends on run_date. Private columns (FYP %, Profile Views)
        and judgement columns (Best Post Pillar, Notes) are left blank to fill in.
        """
        ws = self._worksheet(self.cfg["sheets"]["weekly_log"])
        if ws is None:
            log.warning("Weekly Log tab not found — skipping weekly row.")
            return False

        values = ws.get_all_values()
        hdr, colmap = self._locate_header(values, "week start", WEEKLY_HEADER_ALIASES)
        if hdr is None or "total_followers" not in colmap:
            log.warning("Could not locate Weekly Log headers — skipping.")
            return False

        # Walk existing rows for the previous follower total, last week #, and to
        # avoid double-logging the same week.
        last_total, last_week_num, last_offset = None, 0, -1
        for i, row in enumerate(values[hdr + 1:]):
            if not (self._cell(row, colmap, "week_start") or self._cell(row, colmap, "total_followers")):
                continue
            last_offset = i
            last_total = self._as_int(self._cell(row, colmap, "total_followers")) or last_total
            last_week_num = self._as_int(self._cell(row, colmap, "week_num")) or last_week_num
            if self._cell(row, colmap, "week_end") == run_date:
                log.info("Weekly Log already has a row ending %s — skipping.", run_date)
                return False

        end_d = dt.date.fromisoformat(run_date)
        start_d = end_d - dt.timedelta(days=6)
        week_start, week_end = start_d.isoformat(), end_d.isoformat()

        wk = [p for p in posts if p["date"] and p["date"] >= week_start]
        total_views = sum(int(p["views"]) for p in wk)
        best = max(wk, key=lambda p: int(p["views"]), default=None)

        fields = {
            "week_start": week_start,
            "week_end": week_end,
            "week_num": (last_week_num + 1) if last_week_num else end_d.isocalendar()[1],
            "new_followers": (followers - last_total) if (followers is not None and last_total is not None) else "",
            "total_followers": followers if followers is not None else "",
            "posts": len(wk),
            "total_views": total_views,
            "avg_views": round(total_views / len(wk)) if wk else "",
            "best_post": (best["title"] or f"(untitled · {best['date']})")[:80] if best else "",
            "best_views": int(best["views"]) if best else "",
            "total_likes": sum(int(p["likes"]) for p in wk),
            "total_comments": sum(int(p["comments"]) for p in wk),
            "total_shares": sum(int(p["shares"]) for p in wk),
        }

        width = max(colmap.values()) + 1
        new_row = [""] * width
        for field, value in fields.items():
            if field in colmap:
                new_row[colmap[field]] = value

        if self.dry_run:
            log.info("[dry-run] Would append Weekly Log %s..%s: followers=%s (%+s), %d posts, %d views.",
                     week_start, week_end, followers, fields["new_followers"], len(wk), total_views)
            return True

        next_row = hdr + 1 + last_offset + 2
        ws.update(range_name=f"A{next_row}", values=[new_row], value_input_option="USER_ENTERED")
        log.info("Weekly Log: appended %s..%s (followers=%s, %d posts, %d views).",
                 week_start, week_end, followers, len(wk), total_views)
        return True

    # ── Run B: Trend Watch ──────────────────────────────────────────────
    def write_trends(self, trends: list[dict], run_date: str) -> int:
        if not trends:
            return 0
        name = self.cfg["sheets"]["trend_watch"]
        ws = self._worksheet(name)
        header = ["Scanned", "Posted", "Author", "Views", "Likes",
                  "Sound", "Original?", "Caption", "Hashtags", "Link"]
        trends = sorted(trends, key=lambda t: t["views"], reverse=True)
        rows = [[
            run_date, t["date"], t["author"], t["views"], t["likes"],
            t["sound"], "✓" if t["sound_original"] else "",
            t["caption"], t["hashtags"], t["url"],
        ] for t in trends]

        if self.dry_run:
            log.info("[dry-run] Would write %d trend rows to '%s'.", len(rows), name)
            return len(rows)

        if ws is None:
            ws = self.ss.add_worksheet(title="🔥 Trend Watch", rows=200, cols=10)
            log.info("Created Trend Watch tab.")
        # Overwrite each week with the latest scan (header + fresh rows).
        ws.clear()
        ws.update(range_name="A1", values=[header] + rows, value_input_option="USER_ENTERED")
        ws.format("A1:J1", {"textFormat": {"bold": True}})
        log.info("Trend Watch: wrote %d rows.", len(rows))
        return len(rows)
