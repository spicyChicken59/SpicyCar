# Auto-market-tracker

Daily used-car market tracker. Once a day a GitHub Action pulls listings from the
[auto.dev](https://auto.dev) API for every brand → model → trim in `targets.json`, appends a
snapshot to `data/snapshots.csv`, writes a Markdown report, emails it, and publishes a dashboard
to GitHub Pages. Currently tracking the BMW i4 and i5, every trim.

## What you get each day

- `REPORT.md` — the emailed report, grouped by model then trim: price changes, vehicles gone since
  the last snapshot, every local listing, and the five best-value cars nationwide (landed,
  mileage-adjusted).
- `docs/index.html` + `docs/data.json` — the dashboard. Brand and model tabs, a trim filter,
  lowest and median landed price over time, one row per vehicle with photo, history flags (CPO,
  owners, accidents, ex-lease), distance from your nearest market, days on market, per-vehicle price
  sparkline, and a "gone from the market" list.
- `design-system/` — the SpicyChicken59 design system the dashboard is built on (`sc59.css`, the
  spec, a living style guide). `docs/sc59.css` is a copy of it; see `design-system/README.md`.
- `data/snapshots.csv` — every listing seen, every day, with coordinates. The file is rewritten on
  each run, so re-running on the same day replaces that day's rows instead of duplicating them.

**Landed price** = asking price + `ship_cost` (listings outside every local market) +
`(miles − mileage_baseline) × cents_per_mile`. It is the number everything sorts by.

## How it stays inside the free API plan

The auto.dev free plan allows **1,000 calls a month** and returns at most **20 listings per call**.
The tracker is built around that:

- Each trim is fetched **twice**: once for the **region** (one wide radius that covers all your local
  markets) and once **nationally**. Listings are then placed into local markets by distance from
  each market's centre, so adding a market costs nothing.
- A trim's `depth` decides how many calls it spends per source: `light` = the cheapest 20
  (1 call), `full` = every sort × every page (with the defaults, 4 calls). Give `full` to the one or
  two trims you are actually shopping; leave the rest `light`.
- `budget_per_day` is a hard guard: the script refuses to run if the planned call count exceeds it,
  and prints the plan (`20/day ≈ 600/month`) at the start of every run.
- Because each query only returns the cheapest N, a car can disappear from the data by being priced
  above the day's cut-off rather than by selling. Those are marked "out of window" in the dashboard
  and left out of the report's "gone" list.

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

Everything lives in `targets.json`.

| Key | Meaning |
|---|---|
| `budget_per_day` | Maximum API calls per run; the script refuses to exceed it. |
| `region` | The one geographic query: `zip`, `lat`, `lon`, `distance` (miles). Make it wide enough to cover every local market. |
| `markets` | Local markets: `zip`, `lat`, `lon`, `distance`. A listing belongs to every market whose radius contains it; its primary market is the nearest. |
| `defaults` | Fallbacks for every parameter below. |
| `legacy_ids` | Old target ids → new ids, so history carries over when the config is restructured. |
| `brands.<brand>` | `label`, `make` (as the API spells it), `active`, and `models`. |
| `brands.<brand>.models.<model>` | `label`, `model` (API spelling), `years`, `note`, `active`, parameter overrides, and `trims`. |
| `…trims.<trim>` | `label`, `trim_query` (sent to the API as `vehicle.trim`), `trim_match` (client-side safety net), `note`, `depth`, `years`, `min_price`, `active`. |

Parameters resolve trim ← model ← brand ← defaults:

| Parameter | Meaning |
|---|---|
| `cents_per_mile`, `mileage_baseline` | mileage adjustment (default 0.30 / 20,000) |
| `ship_cost` | added to the landed price of listings outside every local market |
| `min_price` | listings below this are ignored — monthly payments or typos, not cars |
| `depth` | `light` (1 call per source) or `full` (`sorts` × `pages` calls per source) |
| `sorts`, `pages` | what `full` depth fetches (defaults: `price.asc` + `miles.asc`, 2 pages) |
| `years` | model years; sent as a range and also filtered client-side |

A target's id is `brand-model-trim` (e.g. `bmw-i5-edrive40`). Add a brand as another key under
`brands`; the dashboard grows a brand tab row. Check the printed call plan after any change.
