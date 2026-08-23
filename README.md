# Auto-market-tracker

Daily used-car market tracker. Once a day a GitHub Action pulls listings from the
[auto.dev](https://auto.dev) API for every target in `targets.json`, appends a snapshot
to `data/snapshots.csv`, writes a Markdown report, emails it, and publishes a dashboard
to GitHub Pages.

## What you get each day

- `REPORT.md` — the emailed report: price changes, vehicles gone since the last snapshot,
  the cheapest listings per market, and a best-value list (landed, mileage-adjusted).
- `docs/index.html` + `docs/data.json` — the dashboard. Lowest and median landed price
  over time, one row per vehicle with photo, history flags (CPO, owners, accidents,
  ex-lease), days on market, per-vehicle price sparkline, and a "gone from the market" list.
- `design-system/` — the SpicyChicken59 design system the dashboard is built on (`sc59.css`, the
  spec, a living style guide). `docs/sc59.css` is a copy of it; see `design-system/README.md`.
- `data/snapshots.csv` — every listing seen, every day. The file is rewritten on each run,
  so re-running on the same day replaces that day's rows instead of duplicating them.

**Landed price** = asking price + `ship_cost` (national listings only) +
`(miles − mileage_baseline) × cents_per_mile`. It is the number everything sorts by.

## Setup

1. Repository secrets (Settings → Secrets and variables → Actions):
   `AUTODEV_API_KEY` (required), `RESEND_API_KEY` and `EMAIL_TO` (optional — no email without them).
2. GitHub Pages: Settings → Pages → *Deploy from a branch* → `main` → `/docs`.
   The dashboard appears at `https://<user>.github.io/Auto-market-tracker/`.
3. The workflow in `.github/workflows/daily.yml` runs at 11:00 UTC (7am Eastern) and can be
   started by hand from the Actions tab (*Run workflow*).

Run locally with `AUTODEV_API_KEY=… python Tracking.py`. To preview the dashboard,
serve the folder (`python -m http.server` inside `docs/`) — it fetches `data.json`, which
browsers block on `file://`.

## Configuring targets

Everything lives in `targets.json`. `markets` maps a name to a zip and radius in miles
(`null` = no geographic filter, i.e. national). Each target:

| Key | Meaning |
|---|---|
| `active` | `false` pauses a target without deleting it |
| `label`, `note` | shown in the report and dashboard |
| `make`, `model`, `years` | passed to the API |
| `trim_query` | sent to the API as `vehicle.trim` so the page window isn't filled by other trims |
| `trim_match` | client-side safety net: the text must appear somewhere in the record |
| `markets` | which market names to query |
| `cents_per_mile`, `mileage_baseline` | mileage adjustment (default 0.25 / 20,000) |
| `ship_cost` | added to national listings' landed price |
| `min_price` | listings below this are ignored — they're monthly payments or typos, not cars (default 5,000) |

Add a second target as another key under `targets`; the dashboard grows a tab row.
