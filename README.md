# TikTok → Apify → Google Sheet automation

Weekly, hands-off TikTok analytics. Scrapes public data for a TikTok profile via
the Apify `clockworks/tiktok-scraper` actor and appends it to an existing Google
Sheet tracker. Runs on GitHub Actions — no laptop required.

> No personal data is committed: your Sheet ID and TikTok handle are supplied via
> environment variables / GitHub repo Variables, so this repo is safe to make public.

```
Run A (my posts)  ──► Post Log     (new posts, deduped)
                  ──► Weekly Log    (one aggregate row per week)
Run B (hashtags)  ──► Trend Watch   (top videos, refreshed each run)
```

Private data (audience demographics, hourly heatmap, watch time) **can't** be
scraped — keep refreshing the Audience tab manually from a TikTok Studio CSV
export every month or so.

---

## What it does each run

- **Post Log** — appends any new videos from the last 14 days. Dedup uses the
  `Link` column (the post URL) when present, otherwise `date + title` — the same
  key the old CSV importer used, so nothing gets double-logged. New rows get
  **Pillar / Hook Type dropdowns** attached automatically (suggestions, not
  strict — see `tagging` in `config.yaml`) so they stay quick to tag.
- **Weekly Log** — appends one aggregate row for the 7-day window ending on the
  run date: Week Start/End, Week #, New Followers, Total Followers, Posts This
  Week, Total Views/Likes/Comments/Shares, Avg Views/Post, and the week's Best
  Performing Post + its views. Columns are matched by header name, so the order
  and any extra columns don't matter.
- **Trend Watch** — clears and rewrites a tab with the top hashtag videos
  (sorted by views), surfacing trending sounds/formats to riff on.
- **Dashboard** — fully rebuilt from Post Log + Weekly Log into four blocks:
  Growth Momentum (followers + trend sparklines), Content Scoreboard (pillar
  performance + top/recent posts), Brand-Pitch Snapshot (reach, engagement rate,
  best week), and Posting Consistency (vs your 4–5/week target). Values are
  computed in Python (locale-proof) and refresh each run; pillar stats use your
  manual Pillar tags. The rebuild is best-effort — if it fails, the data sync
  above is unaffected. Disable with `--skip-dashboard`.

Manual/private columns (Pillar, Hook Type, FYP %, Profile Views, Notes) are left
blank for you to fill — the script never overwrites them.

---

## One-time setup

### 1. Apify token
Apify Console → **Settings → Integrations → API token**. Copy it.

### 2. Google service account (so the script can write to the Sheet)
1. [Google Cloud Console](https://console.cloud.google.com/) → create/select a project.
2. **APIs & Services → Library** → enable **Google Sheets API**.
3. **APIs & Services → Credentials → Create credentials → Service account**.
4. Open the new service account → **Keys → Add key → JSON** → download the file.
5. Open the JSON, copy the `client_email` value (looks like
   `…@….iam.gserviceaccount.com`).
6. Open your tracker sheet → **Share** → paste that email → give it **Editor** → Send.

### 3. Tell it your sheet + handle
- **Sheet ID** — the long string in your sheet's URL between `/d/` and `/edit`.
- **Handle** — your TikTok username (without the `@`).

Provide them as env vars / repo Variables: `SPREADSHEET_ID` and `TIKTOK_USERNAME`.
(For a private repo you can instead just fill `spreadsheet_id` / `profile.username`
straight into `config.yaml`.)

### 4a. Run locally (to test)
```bash
pip install -r requirements.txt
cp .env.example .env
# fill in APIFY_TOKEN, SPREADSHEET_ID, TIKTOK_USERNAME; put the JSON key next to
# it as service_account.json
python sync.py --dry-run     # scrape + report, writes nothing
python sync.py               # the real thing
```

### 4b. Run on GitHub Actions (the weekly automation)
1. Push this folder to a GitHub repo.
2. Repo **Settings → Secrets and variables → Actions**:
   - **Secrets:** `APIFY_TOKEN`, and `GOOGLE_SERVICE_ACCOUNT_JSON` (paste the
     **entire contents** of the downloaded JSON key file).
   - **Variables:** `SPREADSHEET_ID`, `TIKTOK_USERNAME`.
3. The workflow in `.github/workflows/weekly-sync.yml` runs every **Sunday
   15:00 UTC (18:00 Finnish)**. Trigger it manually anytime from the **Actions**
   tab (with an optional dry-run checkbox).

---

## Configuration

Everything tweakable lives in [`config.yaml`](config.yaml) — hashtags to track,
how many videos per run, the date window, tab names. Edit, commit, push. No code
changes needed.

```bash
python sync.py --dry-run         # scrape, write nothing
python sync.py --skip-trends     # Run A only (posts + weekly)
python sync.py --skip-profile    # Run B only (hashtag scan)
python sync.py --skip-dashboard  # don't rebuild the Dashboard tab
```

---

## Cost

Pay-per-result on Apify. ~30 profile videos + ~80 hashtag videos ≈ **$0.40–0.50
per week**, well inside Apify's free **$5/month** credit. Effective cost: **$0**.

---

## Layout

```
config.yaml                  # all settings
sync.py                      # entrypoint / orchestration
tiktok_apify/
  apify_runner.py            # triggers the actor, collects the dataset
  mapping.py                 # raw scraper fields -> tracker fields
  sheets.py                  # header-aware Google Sheet writer + dedup
.github/workflows/
  weekly-sync.yml            # the Sunday schedule
```

The sheet writer **detects the header row and maps columns by name at runtime**,
so it works whether the Post Log is the v3 layout (headers on row 2, with a
`Link` column) or the older flat layout — and survives minor tab edits.

## Notes / limitations

- Photo/carousel posts come back with an empty caption and `0s` duration. With
  the `Link` column present, dedup still works perfectly (URL-based). On a sheet
  with no `Link` column, two empty-caption posts on the *same day* would collide
  on the dedup key — vanishingly rare, but worth knowing.
- **Saves:** if the Post Log has no `Saves` column, the script adds one
  automatically at the end of the header row (so nothing else shifts) and fills
  it from then on. If you already have a `Saves` column anywhere, it's detected
  by name and used as-is.
