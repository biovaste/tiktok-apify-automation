"""Build a decision-focused Dashboard grid from Post Log + Weekly Log data.

Pure layout/computation: given already-read rows, it returns a 2D value grid
(values + a few bare SPARKLINE formulas) and a list of Google Sheets batch
formatting requests. The caller clears the Dashboard tab and writes both.

Numbers are written as plain values (locale-proof); only single-argument
SPARKLINE() calls are used so the sheet's locale separator never matters.
"""

from __future__ import annotations

# Palette (hex -> rgb done at request build time)
BANNER = "1a1a2e"
BAND = "0f3460"
TABLE_HEAD = "16213e"
MID = "0f3460"
LIGHT = "f5f5f5"
WHITE = "ffffff"
WIDTH = 8  # columns A..H


def _num(x) -> int:
    try:
        return int(float(str(x).replace(",", "").strip()))
    except (ValueError, TypeError):
        return 0


def _hex(h: str) -> dict:
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}


def _last_nonempty(series: list):
    for v in reversed(series):
        if v not in ("", None):
            return v
    return None


def _title_or_date(p: dict) -> str:
    t = (p.get("title") or "").strip().replace("\n", " ")
    return (t or f"(untitled · {p.get('date', '')})")[:60]


def build_dashboard(sheet_id: int, username: str, post_rows: list[dict],
                    weekly_rows: list[dict], cfg: dict, weekly_ref: dict | None):
    rows: list[list] = []
    reqs: list[dict] = []

    def rng(r0, r1, c0, c1):
        return {"sheetId": sheet_id, "startRowIndex": r0, "endRowIndex": r1,
                "startColumnIndex": c0, "endColumnIndex": c1}

    def merge(r0, r1, c0, c1):
        reqs.append({"mergeCells": {"range": rng(r0, r1, c0, c1), "mergeType": "MERGE_ALL"}})

    def fmt(r0, r1, c0, c1, bg=None, fg=None, bold=False, size=None, align=None, numfmt=None):
        cell, fields, tf = {}, [], {}
        if bg:
            cell["backgroundColor"] = _hex(bg); fields.append("backgroundColor")
        if fg:
            tf["foregroundColor"] = _hex(fg)
        if bold:
            tf["bold"] = True
        if size:
            tf["fontSize"] = size
        if tf:
            cell["textFormat"] = tf; fields.append("textFormat")
        if align:
            cell["horizontalAlignment"] = align; fields.append("horizontalAlignment")
        if numfmt:
            cell["numberFormat"] = {"type": "NUMBER", "pattern": numfmt}; fields.append("numberFormat")
        reqs.append({"repeatCell": {"range": rng(r0, r1, c0, c1),
                                    "cell": {"userEnteredFormat": cell},
                                    "fields": "userEnteredFormat(" + ",".join(fields) + ")"}})

    def pad(row):
        return list(row) + [""] * (WIDTH - len(row))

    def banner(text):
        r = len(rows); rows.append(pad([text]))
        merge(r, r + 1, 0, WIDTH)
        fmt(r, r + 1, 0, WIDTH, bg=BANNER, fg=WHITE, bold=True, size=14, align="CENTER")

    def band(text):
        rows.append(pad([""]))  # spacer
        r = len(rows); rows.append(pad([text]))
        merge(r, r + 1, 0, WIDTH)
        fmt(r, r + 1, 0, WIDTH, bg=BAND, fg=WHITE, bold=True, size=11)

    def kpi_strip(items):
        labels, vals = [""] * WIDTH, [""] * WIDTH
        for i, (lab, val) in enumerate(items[:4]):
            labels[i * 2] = lab
            vals[i * 2] = val
        lr = len(rows); rows.append(labels)
        vr = len(rows); rows.append(vals)
        for i, (_, val) in enumerate(items[:4]):
            c = i * 2
            merge(lr, lr + 1, c, c + 2)
            merge(vr, vr + 1, c, c + 2)
            fmt(lr, lr + 1, c, c + 2, bg=MID, fg=WHITE, bold=True, size=9, align="CENTER")
            fmt(vr, vr + 1, c, c + 2, bg=LIGHT, bold=True, size=13, align="CENTER",
                numfmt="#,##0" if isinstance(val, (int, float)) else None)

    def table(headers, data, numeric_cols=()):
        hr = len(rows); rows.append(pad(headers))
        fmt(hr, hr + 1, 0, len(headers), bg=TABLE_HEAD, fg=WHITE, bold=True, size=10)
        for dr in data:
            rows.append(pad(dr))
        if data:
            for ci in numeric_cols:
                fmt(hr + 1, hr + 1 + len(data), ci, ci + 1, numfmt="#,##0")

    def sparkline(label, formula):
        r = len(rows); rows.append(pad([label, "", formula]))
        merge(r, r + 1, 0, 2)
        merge(r, r + 1, 2, WIDTH)
        fmt(r, r + 1, 0, 2, bold=True, size=9, align="RIGHT")

    # ── compute ─────────────────────────────────────────────────────────
    wk = sorted([w for w in weekly_rows if (w.get("week_end") or w.get("total_followers"))],
                key=lambda w: w.get("week_end", ""))
    followers_now = _last_nonempty([w.get("total_followers") for w in wk])
    followers_now = _num(followers_now) if followers_now is not None else None
    new_series = [_num(w.get("new_followers")) for w in wk if w.get("new_followers") not in ("", None)]
    delta_week = new_series[-1] if new_series else None
    delta_4wk = sum(new_series[-4:]) if new_series else None
    posts_series = [_num(w.get("posts")) for w in wk if w.get("posts") not in ("", None)]
    posts_week = posts_series[-1] if posts_series else None

    views = [_num(p.get("views")) for p in post_rows]
    total_posts = len(post_rows)
    avg_views = round(sum(views) / len(views)) if views else 0
    eng_den = sum(views)
    eng_num = sum(_num(p.get("likes")) + _num(p.get("comments")) + _num(p.get("shares")) for p in post_rows)
    eng_rate = round(eng_num / eng_den * 100, 1) if eng_den else 0
    best_week = max(wk, key=lambda w: _num(w.get("new_followers")), default=None)

    # ── BANNER ──────────────────────────────────────────────────────────
    banner(f"DASHBOARD — @{username}")

    # ── 1. GROWTH MOMENTUM ──────────────────────────────────────────────
    band("📈 GROWTH MOMENTUM")
    kpi_strip([
        ("Followers", followers_now if followers_now is not None else "—"),
        ("New this week", delta_week if delta_week is not None else "—"),
        ("New last 4 wks", delta_4wk if delta_4wk is not None else "—"),
        ("Posts this week", posts_week if posts_week is not None else "—"),
    ])
    if weekly_ref and wk:
        t, sr = weekly_ref["title"], weekly_ref["start_row"]
        c = weekly_ref["cols"]
        if c.get("total_followers"):
            sparkline("Follower trend", f"=SPARKLINE('{t}'!{c['total_followers']}{sr}:{c['total_followers']}1000)")
        if c.get("total_views"):
            sparkline("Weekly views trend", f"=SPARKLINE('{t}'!{c['total_views']}{sr}:{c['total_views']}1000)")

    # ── 2. CONTENT SCOREBOARD ───────────────────────────────────────────
    band("🎯 CONTENT SCOREBOARD")
    pillar_stats = []
    for name in (cfg.get("tagging", {}) or {}).get("pillar_options", []):
        ps = [p for p in post_rows if (p.get("pillar") or "").strip() == name]
        if not ps:
            continue
        pv = [_num(p.get("views")) for p in ps]
        pillar_stats.append([name, len(ps), sum(pv), round(sum(pv) / len(pv)), max(pv)])
    pillar_stats.sort(key=lambda r: r[3], reverse=True)  # by avg views
    table(["Content Pillar", "Posts", "Total Views", "Avg Views", "Best Post"],
          pillar_stats or [["— no pillar tags yet —", "", "", "", ""]],
          numeric_cols=(1, 2, 3, 4))

    rows.append(pad([""]))
    top5 = sorted(post_rows, key=lambda p: _num(p.get("views")), reverse=True)[:5]
    table(["Top posts (all-time)", "Views"],
          [[_title_or_date(p), _num(p.get("views"))] for p in top5] or [["—", ""]],
          numeric_cols=(1,))

    rows.append(pad([""]))
    recent5 = sorted(post_rows, key=lambda p: p.get("date", ""), reverse=True)[:5]
    table(["Most recent posts", "Date", "Views"],
          [[_title_or_date(p), p.get("date", ""), _num(p.get("views"))] for p in recent5] or [["—", "", ""]],
          numeric_cols=(2,))

    # ── 3. BRAND-PITCH SNAPSHOT ─────────────────────────────────────────
    band("🤝 BRAND-PITCH SNAPSHOT")
    best_week_txt = (f"+{_num(best_week.get('new_followers'))} ({best_week.get('week_end', '')})"
                     if best_week else "—")
    kpi_strip([
        ("Followers", followers_now if followers_now is not None else "—"),
        ("Avg views / post", avg_views),
        ("Engagement rate", f"{eng_rate}%"),
        ("Best follower week", best_week_txt),
    ])

    # ── 4. POSTING CONSISTENCY ──────────────────────────────────────────
    band("🗓️ POSTING CONSISTENCY")
    avg4 = round(sum(posts_series[-4:]) / len(posts_series[-4:]), 1) if posts_series else 0
    kpi_strip([
        ("Posts this week", posts_week if posts_week is not None else "—"),
        ("Target", "4–5 / wk"),
        ("Avg last 4 wks", avg4),
        ("Total posts tracked", total_posts),
    ])
    if weekly_ref and wk and weekly_ref["cols"].get("posts"):
        t, sr = weekly_ref["title"], weekly_ref["start_row"]
        sparkline("Weekly posts trend",
                  f"=SPARKLINE('{t}'!{weekly_ref['cols']['posts']}{sr}:{weekly_ref['cols']['posts']}1000)")

    rows.append(pad([""]))
    note = ("Auto-rebuilt weekly from Post Log + Weekly Log. Numbers update on each sync; "
            "pillar stats reflect your manual Pillar tags. Notes/qualitative columns live in the source tabs.")
    r = len(rows); rows.append(pad([note]))
    merge(r, r + 1, 0, WIDTH)
    fmt(r, r + 1, 0, WIDTH, bg=LIGHT, size=9)

    return [pad(r) for r in rows], reqs
