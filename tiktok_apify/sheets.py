"""Google Sheet writer for the TikTok tracker.

Robust to differing Post Log layouts: it finds the header row and
maps columns by header name at runtime rather than hardcoding positions.
Dedup uses the Link column (webVideoUrl) when present, else date+title — the
same key the original CSV importer used, so we never double-add a post that was
logged manually.
"""

from __future__ import annotations

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
    def _find_header(values: list[list[str]], must_have: str):
        """Return (0-based header row index, {field: 0-based col}) by scanning
        the first few rows for a row containing `must_have`."""
        for r in range(min(5, len(values))):
            cells = [c.strip().lower() for c in values[r]]
            if any(must_have in c for c in cells):
                colmap: dict[str, int] = {}
                for ci, cell in enumerate(cells):
                    for alias, field in POST_HEADER_ALIASES.items():
                        if alias in cell and field not in colmap:
                            colmap[field] = ci
                return r, colmap
        return None, {}

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
            log.info("[dry-run] Would append %d new post(s) to Post Log at row %d.", added, next_row)
            return added

        ws.update(range_name=f"A{next_row}", values=new_rows, value_input_option="USER_ENTERED")
        log.info("Post Log: appended %d new post(s).", added)
        return added

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

    # ── Run A: Follower Growth ──────────────────────────────────────────
    def append_follower(self, count: int | None, date: str) -> bool:
        if count is None:
            log.warning("No follower count in scrape — skipping follower row.")
            return False
        ws = self._worksheet(self.cfg["sheets"]["follower_growth"])
        if ws is None:
            log.warning("Follower Growth tab not found — skipping.")
            return False

        values = ws.get_all_values()
        hdr, _ = self._find_header(values, "follower")
        if hdr is None:
            log.warning("Could not locate Follower Growth headers — skipping.")
            return False

        data_rows = values[hdr + 1:]
        last_count, last_offset = None, -1
        for i, row in enumerate(data_rows):
            if row and row[0].strip():
                last_offset = i
                try:
                    last_count = int(str(row[1]).replace(",", "").strip())
                except (ValueError, IndexError):
                    pass
                if row[0].strip() == date:  # already logged today
                    log.info("Follower Growth: %s already has a row — skipping.", date)
                    return False

        diff = (count - last_count) if last_count is not None else 0
        next_row = hdr + 1 + last_offset + 2
        new_row = [[date, count, diff, ""]]
        if self.dry_run:
            log.info("[dry-run] Would append follower row: %s = %d (%+d).", date, count, diff)
            return True
        ws.update(range_name=f"A{next_row}", values=new_row, value_input_option="USER_ENTERED")
        log.info("Follower Growth: appended %s = %d (%+d).", date, count, diff)
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
