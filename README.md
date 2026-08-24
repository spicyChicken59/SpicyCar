<p align="center">
  <a href="https://spicychicken59.github.io/SpicyCar/"><img src="docs/screenshot.png" alt="SpicyCar dashboard — BMW i5, lowest and median landed price, listings with photos and history flags" width="920"></a>
</p>

# SpicyCar

[![SpicyCar daily](https://github.com/spicyChicken59/SpicyCar/actions/workflows/daily.yml/badge.svg)](https://github.com/spicyChicken59/SpicyCar/actions/workflows/daily.yml)

**A used-car purchase analyzer.** Every day it snapshots every BMW i4 and i5 on the market, prices
each one as what it would actually cost to land in a specific buyer's driveway, and publishes the
result as a report, an email, and a dashboard.

It runs on the free tier of one API, GitHub Actions, and GitHub Pages. No servers, no database — a
CSV in the repository is the ledger.

**[Dashboard](https://spicychicken59.github.io/SpicyCar/) ·
[How it works](https://spicychicken59.github.io/SpicyCar/how.html) ·
[Today's report](REPORT.md)**

## The idea

Asking price is the least useful number on a listing. A $38,000 car in San Diego and a $45,000 car
in Indianapolis are not $7,000 apart if one has to ride a truck for 1,800 miles and the other has
20,000 more miles on it. SpicyCar makes that comparison honest, every day:

```
landed = asking
       + shipping                  0 in-state · else max($350, miles_from_home × $0.65)
       + (miles − 20,000) × $0.30
```

Everything sorts by landed. Which cars are sitting, which are being cut, which just disappeared —
all relative to what they would really cost *this* buyer.

Two things are configured, separately:

- **buyer** — who is purchasing: home zip, the states they will drive to for a car, and how they
  value miles and shipping. One buyer today; the first real use is a buyer near Chicago shopping
  for an i5 eDrive40. The shape is built for more.
- **watchlist** — what to track: brands → models → trims. Every trim of the i4 and i5, so the one
  they want can be judged against its siblings.

## Design decisions

**It runs on 20 API calls a day.** The free plan allows 1,000 calls a month at 20 listings each.
So each trim is fetched twice — once filtered to the buyer's states (the API takes a comma list, so
three states cost one call) and once nationally — and each trim has a *depth*: the one being shopped
gets every sort and page, the rest get the cheapest 20. A hard `budget_per_day` makes the script
refuse to run over budget, and it prints its plan before it starts.

**It is honest about what it cannot see.** Because each query returns only the cheapest N, a car
can vanish from the data by being priced *above* the day's cut-off rather than by selling. Those are
labelled "priced above today's cut-off" on the dashboard and left out of the report's "gone" list.

**Scope by state, not by radius.** The first version placed listings into city radii by their
coordinates and returned one or two local cars a day while the same cars appeared nationally with
Midwest addresses. The cause: for listings it cannot geocode, the API returns exactly `(0, 0)` —
null island, 5,900 miles from Indianapolis — which passes every null check and fails every radius.
Now a listing is in-state when its own `state` field says so; coordinates only price shipping, with
a cached zip-code fallback. *Use the field that means the thing.*

**Buyer and watchlist are separate.** Cost model and geography belong to a person; brands and trims
belong to the market. Keeping them apart is what makes the second buyer an addition, not a rewrite.

**Nothing to operate.** A scheduled Action, a CSV rewritten in place each run (so a same-day re-run
replaces rather than duplicates), a static dashboard that reads one JSON file, and a design system
loaded live from its own repo's Pages so the app restyles itself when the system updates.

## Architecture

```mermaid
flowchart LR
  cron[GitHub Actions<br>daily 11:00 UTC] --> py[Tracking.py]
  cfg[(targets.json<br>buyer + watchlist)] --> py
  py -->|2 calls per trim| api[auto.dev listings API]
  py -->|zip fallback, cached| geo[zippopotam.us]
  py --> csv[(data/snapshots.csv)]
  py --> rep[REPORT.md]
  py --> json[docs/data.json]
  rep --> mail[Email via Resend]
  json --> dash[Dashboard<br>GitHub Pages]
  ds[SpicyChicken design system<br>its own Pages] -. styles + marks .-> dash
```

**Stack:** Python 3.12 + `requests` · GitHub Actions · GitHub Pages · vanilla JavaScript, no build
step · [SpicyChicken design system](https://github.com/spicyChicken59/design-system).

## What you get each day

- `REPORT.md` — grouped by model then trim: price changes, vehicles gone since the last snapshot,
  every in-state listing grouped by state, and the five best-value cars out of state.
- The dashboard — brand and model tabs; trim, state, year and mileage filters; lowest and median
  landed price over time; one row per vehicle with photo, history flags (CPO, owners, accidents,
  ex-lease), distance from home, shipping estimate, days on market and a price sparkline; and the
  "gone" list.
- `data/snapshots.csv` — every listing seen, every day, with coordinates and distance from home.

## Run it yourself

1. Fork. Add repository secrets: `AUTODEV_API_KEY` (required), `BUYER_HOME_ZIP` (the buyer's zip —
   it stays out of the repo and out of every output), and `RESEND_API_KEY` + `EMAIL_TO` if you want
   the email.
2. Settings → Pages → *Deploy from a branch* → `main` → `/docs`.
3. Edit `targets.json`: your `buyer`, your `watchlist`. The Action runs at 11:00 UTC and can be
   started by hand from the Actions tab.

Locally: `AUTODEV_API_KEY=… python Tracking.py`. To preview the dashboard, serve the folder
(`python -m http.server` inside `docs/`) — it fetches `data.json`, which browsers block on `file://`.

## Configuration

### buyer

| Key | Meaning |
|---|---|
| `home_zip` | Where the car ends up; distances are measured from here. Leave it `null` and set the `BUYER_HOME_ZIP` repository secret instead — the tracker reads it from the environment, never caches it, and never writes it to any output. |
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
- Distance-based "drivable" instead of state lines.
- A second buyer profile — the config is already shaped for it.
- More brands on the watchlist.

## Author

Mohammed Tahir Madni — [github.com/spicyChicken59](https://github.com/spicyChicken59)
