# SpicyCar

Used-car purchase analyzer. Once a day a GitHub Action pulls listings from the
[auto.dev](https://auto.dev) API for every brand → model → trim on the watchlist, prices each one
as **landed** — what it would cost a specific buyer to put it in their driveway — appends a snapshot
to `data/snapshots.csv`, writes a Markdown report, emails it, and publishes a dashboard to GitHub
Pages.

Two things are configured, separately, in `targets.json`:

- **buyer** — who is purchasing: home zip, the states they will drive to for a car, and how they
  value miles and shipping. One buyer today (the first real use: a buyer near Chicago); the
  shape is built for more.
- **watchlist** — what to track: brands, models, trims. Currently BMW i4 and i5, every trim.

## Landed price

```
landed = asking
       + shipping                       0 in-state · else max(ship_min, miles_from_home × ship_per_mile)
       + (miles − mileage_baseline) × cents_per_mile
```

"In-state" means the listing's own state field is one of the buyer's `states` — no coordinates
involved, so listings the API could not geocode still land in the right bucket. Distance from home
comes from the listing's coordinates, or from its zip (geocoded once and cached in
`data/zipcodes.json`). If neither is usable, the flat `ship_cost` applies. Everything sorts by
landed.

## What you get each day

- `REPORT.md` — the emailed report, grouped by model then trim: price changes, vehicles gone since
  the last snapshot, every in-state listing grouped by state, and the five best-value cars out of
  state.
- `docs/index.html` + `docs/data.json` — the dashboard. Brand and model tabs, trim and state
  filters, lowest and median landed price over time, one row per vehicle with photo, history flags
  (CPO, owners, accidents, ex-lease), distance from home, shipping estimate, days on market,
  per-vehicle price sparkline, and a "gone from the market" list.
- The dashboard's styles and marks load straight from the
  [SpicyChicken design system](https://github.com/spicyChicken59/design-system) via its GitHub
  Pages — no vendored copy.
- `data/snapshots.csv` — every listing seen, every day, with coordinates and distance from home.
  Rewritten on each run, so a same-day re-run replaces that day's rows instead of duplicating them.

## How it stays inside the free API plan

The auto.dev free plan allows **1,000 calls a month** and returns at most **20 listings per call**.

- Each trim is fetched **twice**: once filtered to the buyer's states (the API takes a comma list,
  so three states cost one call) and once **nationally**. Adding a state costs nothing.
- A trim's `depth` decides how many calls it spends per source: `light` = the cheapest 20
  (1 call), `full` = every sort × every page (with the defaults, 4 calls). Give `full` to the one or
  two trims you are actually shopping; leave the rest `light`.
- `budget_per_day` is a hard guard: the script refuses to run if the planned call count exceeds it,
  and prints the plan at the start of every run.
- Because each query only returns the cheapest N, a car can disappear from the data by being priced
  above the day's cut-off rather than by selling. Those are marked "out of window" in the dashboard
  and left out of the report's "gone" list.

## Setup

1. Repository secrets (Settings → Secrets and variables → Actions):
   `AUTODEV_API_KEY` (required), `RESEND_API_KEY` and `EMAIL_TO` (optional — no email without them).
2. GitHub Pages: Settings → Pages → *Deploy from a branch* → `main` → `/docs`.
3. The workflow in `.github/workflows/daily.yml` runs at 11:00 UTC (7am Eastern) and can be
   started by hand from the Actions tab (*Run workflow*).

Run locally with `AUTODEV_API_KEY=… python Tracking.py`. To preview the dashboard, serve the
folder (`python -m http.server` inside `docs/`) — it fetches `data.json`, which browsers block on
`file://`.

## Configuration

### buyer

| Key | Meaning |
|---|---|
| `home_zip` | Where the car ends up. Distances are measured from here. |
| `states` | Two-letter codes. Listings in these states are drivable: no shipping. |
| `ship_per_mile`, `ship_min` | Shipping estimate for everything else: `max(ship_min, distance × ship_per_mile)`. Set `ship_per_mile` to `null` for a flat rate. |
| `ship_cost` | Flat shipping, used when distance is unknown or `ship_per_mile` is off. |
| `cents_per_mile`, `mileage_baseline` | Mileage adjustment: each mile above the baseline costs this much; each mile below credits it. |

### watchlist

| Key | Meaning |
|---|---|
| `budget_per_day` | Maximum API calls per run; the script refuses to exceed it. |
| `defaults` | Fallbacks for the per-trim parameters below. |
| `legacy_ids` | Old target ids → new ids, so history carries over when the config is restructured. |
| `watchlist.<brand>` | `label`, `make` (as the API spells it), `active`, and `models`. |
| `…models.<model>` | `label`, `model` (API spelling), `years`, `note`, `active`, parameter overrides, and `trims`. |
| `…trims.<trim>` | `label`, `trim_query` (sent to the API as `vehicle.trim`), `trim_match` (client-side safety net), `note`, `depth`, `years`, `min_price`, `active`. |

Per-trim parameters resolve trim ← model ← brand ← defaults:

| Parameter | Meaning |
|---|---|
| `min_price` | listings below this are ignored — monthly payments or typos, not cars |
| `depth` | `light` (1 call per source) or `full` (`sorts` × `pages` calls per source) |
| `sorts`, `pages` | what `full` depth fetches (defaults: `price.asc` + `miles.asc`, 2 pages) |
| `years` | model years; sent as a range and also filtered client-side |

A target's id is `brand-model-trim` (e.g. `bmw-i5-edrive40`). Add a brand as another key under
`watchlist`; the dashboard grows a brand tab row. Check the printed call plan after any change.

## Roadmap

- Drill below state: county or metro.
- A second buyer profile — the config is already shaped for it.
- More brands and models on the watchlist.
