#!/usr/bin/env python3
"""SpicyCar — used-car purchase analyzer. All config lives in targets.json.

Two things are configured, separately:

  buyer      who is purchasing: a PUBLIC anchor point distances measure from
             (never a home address — published distances can be trilaterated),
             the states they will drive to for a car (no shipping), and how
             they value miles, shipping, tax and finance.
  watchlist  what to track: brand -> model -> trim. Each trim is a target
             (id "brand-model-trim").

Most targets are fetched twice on their day: once filtered to the buyer's
states plus search_states (one call, the API takes a comma list) and once
nationally. A `national_only` target — the nationwide certified watches —
asks the country once and skips the States half, which is what makes a
coast-to-coast watch affordable. A target's cadence (1 = daily, 2 = every other day, ...)
spreads the comparison brands across days so the whole watchlist fits the
API plan; buyer.shopping names the targets that lead the report in full,
while the rest get one line each. A listing is
"drivable" — no shipping — when its own state field is one of the buyer's
states, and nothing else: no coordinates involved, so listings the API
could not geocode still land in the right bucket, and the buyer decides
scope by naming states rather than by a radius. search_states widens the
query net to neighbouring states worth watching from beyond. Coordinates
price shipping, from the distance to the public anchor.

What each query actually did is written to data/fetch_log.json, because the
snapshot CSV records what was KEPT and says nothing about what was ASKED —
and delisted() has to know the difference between a car a query looked for
and missed, and one no query ever looked for. See fetch_log_row().
"""

import csv
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median

import requests

API_KEY = os.environ.get("AUTODEV_API_KEY")
if not API_KEY:
    sys.exit("Missing AUTODEV_API_KEY secret")

BASE = "https://api.auto.dev/listings"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}
TODAY = datetime.now(timezone.utc).date().isoformat()

APP = "SpicyCar"
CFG = json.loads(Path("targets.json").read_text())
BUYER = CFG.get("buyer", {})
STATES = [str(s).strip().upper() for s in BUYER.get("states", [])]
# The query net: the buyer's states plus neighbours partially within the
# watching from beyond (search_states). One comma list, so the extras cost nothing.
SEARCH_STATES = STATES + [s for s in
                          (str(x).strip().upper() for x in BUYER.get("search_states", []))
                          if s and s not in STATES]
SHOPPING = [str(s) for s in BUYER.get("shopping", [])]   # target ids being shopped


def _parse_shortlist(raw):
    """buyer.shortlist: the specific cars being decided on, by VIN. Entries
    are "VIN" or {"vin": ..., "note": ...}; order is the buyer's own."""
    out = {}
    for entry in raw or []:
        if isinstance(entry, dict):
            vin, note = str(entry.get("vin") or ""), str(entry.get("note") or "")
        else:
            vin, note = str(entry or ""), ""
        vin = vin.strip().upper()
        if vin:
            out[vin] = note
    return out


SHORTLIST = _parse_shortlist(BUYER.get("shortlist"))
PICKS = BUYER.get("picks", {})                            # how "our picks" are chosen
TODAY_ORD = date.fromisoformat(TODAY).toordinal()
WATCHLIST = CFG["watchlist"]
DEFAULTS = CFG.get("defaults", {})
LEGACY_IDS = CFG.get("legacy_ids", {})
BUDGET = CFG.get("budget_per_day", 40)          # cap on any single day
MONTHLY = CFG.get("budget_per_month", 1000)     # the API plan; checked on the average
PER_PAGE = 20                       # the free plan clamps limit to 20
PARAM_KEYS = ["min_price", "depth", "cadence", "sorts", "pages", "years",
              "newest", "max_miles", "cpo_only", "national_only"]
# The price/miles sorts sample the settled bottom of the market; a fresh,
# well-priced car can list and sell before it ever ranks there. Targets with
# newest > 0 also fetch that many newest-first pages per source, so a new
# listing is seen the day it appears. Overridable in case the API renames it.
NEWEST_SORT = str(CFG.get("newest_sort") or "createdAt.desc")

DATA = Path("data")
DATA.mkdir(exist_ok=True)
DOCS = Path("docs")
DOCS.mkdir(exist_ok=True)
SNAPSHOTS = DATA / "snapshots.csv"
# Exit code for "today was already fetched, nothing was spent". Distinct from 1
# so the workflow can tell a saving apart from a failure and act on it.
ALREADY_FETCHED = 3
SAMPLE = DATA / "sample_record.json"
ZIPCODES = DATA / "zipcodes.json"

FIELDS = ["snapshot_date", "target", "vin", "year", "trim", "miles",
          "price", "dealer", "city", "state", "listed_since", "url",
          "msrp", "color", "cpo", "owners", "accidents", "usage", "image",
          "carfax", "lat", "lon", "distance",
          # WHICH QUERIES RETURNED THIS ROW, pipe-joined "Source:sort" tokens
          # (e.g. "National:miles.asc|States:price.asc"). A target fetching
          # both price.asc and miles.asc has TWO windows, and without this
          # column there is no way to tell which one a car was inside — so a
          # car pushed out of the lowest-by-miles window by newer arrivals
          # cannot be told from one that actually left the market. That
          # ambiguity is why exit prices are currently withheld for every
          # multi-sort target (see departures_are_separable). Empty on every
          # row written before this column existed, which is honest: their
          # provenance is genuinely unknown and cannot be reconstructed.
          "via"]


# --------------------------------------------------------------------------
# Config resolution: defaults <- brand <- model <- trim
# --------------------------------------------------------------------------
def build_targets():
    targets = {}
    seen = Counter()      # per cadence, to spread targets evenly across the cycle
    for bkey, b in WATCHLIST.items():
        if not b.get("active", True):
            continue
        for mkey, m in b["models"].items():
            if not m.get("active", True):
                continue
            # A model with no trims is one target that covers every trim.
            trims = m.get("trims") or {None: {}}
            # A model's trims run on the same days — one page, one fetch day.
            # Per CADENCE, though: a trim that runs every third day cannot share
            # the slot of one that runs every second, and taking the offset from
            # whichever trim happened to be listed first also left the slower
            # trim never claiming a place in its own rotation. The i7 made that
            # visible — its two cadence-3 trims inherited the CPO watch's
            # offset, landed on the Ioniq 5's and Lucid's day, and pushed the
            # worst day from 34 to 36 of 40 while the month went DOWN.
            m_offset = {}
            for tkey, tr in trims.items():
                if not tr.get("active", True):
                    continue
                t = {}
                for layer in (DEFAULTS, b, m, tr):
                    for k in PARAM_KEYS:
                        if k in layer:
                            t[k] = layer[k]
                t.update({
                    "id": f"{bkey}-{mkey}" + (f"-{tkey}" if tkey else ""),
                    "brand": bkey, "brand_label": b.get("label", bkey),
                    "make": b["make"],
                    "model_key": mkey, "model_label": m.get("label", mkey),
                    "model": m.get("model", mkey),
                    "model_note": m.get("note", ""),
                    "model_notes": m.get("notes", {}),
                    "trim_key": tkey or "all",
                    "label": tr.get("label", tkey or "all trims"),
                    "note": tr.get("note", ""),
                    "trim_query": tr.get("trim_query", ""),
                    "trim_match": tr.get("trim_match", ""),
                    "trim_exclude": tr.get("trim_exclude", ""),
                })
                t.setdefault("years", [])
                t.setdefault("sorts", ["price.asc"])
                t.setdefault("pages", 1)
                t.setdefault("depth", "light")
                try:
                    t["newest"] = max(0, int(t.get("newest") or 0))
                except (TypeError, ValueError):
                    t["newest"] = 0
                try:
                    t["cadence"] = max(1, int(t.get("cadence") or 1))
                except (TypeError, ValueError):
                    t["cadence"] = 1
                cad = t["cadence"]
                if cad not in m_offset:
                    m_offset[cad] = seen[cad] % cad
                    seen[cad] += 1
                t["offset"] = m_offset[cad]
                t["shopping"] = t["id"] in SHOPPING
                targets[t["id"]] = t
    return targets


TARGETS = build_targets()
# Each source is a dict of extra query params. The States source asks the
# API for the buyer's states and search_states directly (comma = OR), one
# call per sort/page.
SOURCES = ([("States", {"retailListing.state": ",".join(SEARCH_STATES)})]
           if SEARCH_STATES else []) + [("National", None)]


def sorts_pages(t):
    """Which sorts, and how many pages each, a target fetches per source.

    Reads with .get() so a partial or unknown target answers "no sorts" rather
    than raising: this is called from honesty gates that run over rows whose
    trim id may name a target the current watchlist no longer carries, and a
    KeyError there would take down a rebuild over a car that left in July.
    build_targets() fills depth, sorts and pages for every real target, so this
    is byte-identical for them.
    """
    sorts = list(t.get("sorts") or [])
    if t.get("depth") == "full":
        return sorts, int(t.get("pages") or 1)
    return sorts[:1], 1


def sources_for(t):
    """A national_only target asks the country one question; the States
    query would only re-fetch a subset of the same national answer, so it
    is skipped — which is what makes the nationwide CPO watches affordable."""
    if t.get("national_only"):
        return [("National", None)]
    return SOURCES


def calls_for(t):
    sorts, pages = sorts_pages(t)
    return len(sources_for(t)) * (len(sorts) * pages + t["newest"])


def due_on(t, ordinal):
    """Cadence 1 runs every day. Cadence N runs every Nth day; models with
    the same cadence take successive offsets in watchlist order, so the load
    is spread evenly and each model keeps the same days of the cycle."""
    c = t["cadence"]
    return c <= 1 or (ordinal + t["offset"]) % c == 0


def next_due(t):
    for k in range(t["cadence"]):
        if due_on(t, TODAY_ORD + k):
            return date.fromordinal(TODAY_ORD + k).isoformat()
    return TODAY


def planned_calls():
    """(calls today, worst day in the next two weeks, daily average)."""
    days = [sum(calls_for(t) for t in TARGETS.values()
                if due_on(t, TODAY_ORD + k)) for k in range(14)]
    return days[0], max(days), sum(days) / len(days)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def dig(obj, path):
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def first(obj, paths, default=""):
    for p in paths:
        v = dig(obj, p)
        if v not in (None, "", [], {}):
            return v
    return default


def to_int(v):
    try:
        return int(float(str(v).replace(",", "").replace("$", "")))
    except (TypeError, ValueError):
        return None


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def int_or_blank(v):
    n = to_int(v)
    return n if n is not None else ""


def money(n):
    if not isinstance(n, int):
        return "—"
    return f"-${-n:,}" if n < 0 else f"${n:,}"


def place(x):
    """City, ST — or an honest 'location n/a' instead of an empty '(, )'."""
    bits = [str(x.get(k) or "").strip() for k in ("city", "state")]
    return ", ".join(b for b in bits if b) or "location n/a"


def haversine(lat1, lon1, lat2, lon2):
    """Miles between two points."""
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def coords_ok(lat, lon):
    """True for a usable point. (0, 0) is null island — the API's stand-in for
    a listing it could not geocode, and it is 5,900 miles from the Midwest, so
    it silently fails every radius check unless it is caught here."""
    return (lat is not None and lon is not None
            and not (abs(lat) < 0.01 and abs(lon) < 0.01))


def load_zip_cache():
    if ZIPCODES.exists():
        try:
            return json.loads(ZIPCODES.read_text())
        except ValueError:
            print("  ! zipcodes.json unreadable — starting a fresh cache")
    return {}


ZIP_CACHE = load_zip_cache()
ZIP_LOOKUPS = 0     # zip API calls made this run (cache misses only)
GEOCODED = 0        # listings rescued from a bad location by their zip
UNPLACED = 0        # listings with neither usable coordinates nor a usable zip


def zip_coords(z, cache=True):
    """lat/lon for a 5-digit US zip, cached in data/zipcodes.json so a zip is
    only ever looked up once. Misses are cached too; network errors are not.
    cache=False neither reads nor writes the cache (used for the home zip)."""
    global ZIP_LOOKUPS
    z = str(z or "").strip()[:5]
    if not (len(z) == 5 and z.isdigit()):
        return None, None
    if cache and z in ZIP_CACHE:
        hit = ZIP_CACHE[z]
        return (hit[0], hit[1]) if hit else (None, None)
    try:
        r = requests.get(f"https://api.zippopotam.us/us/{z}", timeout=15)
        ZIP_LOOKUPS += 1
        if r.status_code == 200:
            hit = ((r.json() or {}).get("places") or [{}])[0]
            lat = to_float(hit.get("latitude"))
            lon = to_float(hit.get("longitude"))
            if coords_ok(lat, lon):
                if cache:
                    ZIP_CACHE[z] = [round(lat, 5), round(lon, 5)]
                return lat, lon
        # only a definitive miss is cached — a throttle or server error must
        # not poison the committed cache and stop the zip ever being retried
        if r.status_code in (200, 404):
            if cache:
                ZIP_CACHE[z] = None
        else:
            print(f"  ! zip {z}: HTTP {r.status_code} — will retry next run")
    except requests.RequestException as e:
        print(f"  ! zip {z}: {e}" if cache else f"  ! home zip lookup failed: {e}")
    return None, None


def save_zip_cache():
    ZIPCODES.write_text(json.dumps(ZIP_CACHE, indent=0, sort_keys=True))


# --------------------------------------------------------------------------
# Buyer geography: scope by state, shipping by distance from home
# --------------------------------------------------------------------------
STATE_NAMES = dict(zip(
    ("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN "
     "MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA "
     "WV WI WY").split(),
    ("Alabama Alaska Arizona Arkansas California Colorado Connecticut Delaware "
     "District_of_Columbia Florida Georgia Hawaii Idaho Illinois Indiana Iowa "
     "Kansas Kentucky Louisiana Maine Maryland Massachusetts Michigan Minnesota "
     "Mississippi Missouri Montana Nebraska Nevada New_Hampshire New_Jersey "
     "New_Mexico New_York North_Carolina North_Dakota Ohio Oklahoma Oregon "
     "Pennsylvania Rhode_Island South_Carolina South_Dakota Tennessee Texas "
     "Utah Vermont Virginia Washington West_Virginia Wisconsin Wyoming").split()))
STATE_NAMES = {k: v.replace("_", " ") for k, v in STATE_NAMES.items()}

# The distance anchor is PUBLIC — a city-centre [lat, lon] in targets.json —
# so no published number derives from a private location. Exact per-listing
# distances measured from a private home zip can be trilaterated back to the
# house (hundreds of dealer coordinates plus a distance each overdetermine
# it, and ship/rate leaks the same signal), so the anchor is the only shape
# of this feature that keeps a home private. BUYER_HOME_ZIP remains as a
# legacy fallback for anyone who accepts that trade.
ANCHOR = BUYER.get("anchor") or []
HOME = ((to_float(ANCHOR[0]), to_float(ANCHOR[1]))
        if isinstance(ANCHOR, (list, tuple)) and len(ANCHOR) == 2
        else (None, None))
if not coords_ok(*HOME):
    HOME_ZIP = os.environ.get("BUYER_HOME_ZIP") or BUYER.get("home_zip")
    HOME = zip_coords(HOME_ZIP, cache=False) if HOME_ZIP else (None, None)
    if coords_ok(*HOME):
        print("  ! distances anchored to the private home zip — published "
              "distances can be traced to it; set buyer.anchor to a public "
              "point (your city centre) instead")
    else:
        print("  ! no buyer.anchor and no home zip — distances unavailable, "
              "flat ship_cost applies")
HOME_NAME = str(BUYER.get("label") or "home")

def dist_home(lat, lon):
    """Miles from the buyer's anchor, or None. Rounded to 25: distances are
    estimates for judging a drive, and in legacy home-zip mode coarseness at
    least blunts casual reading (it does NOT stop trilateration — only a
    public anchor does)."""
    if coords_ok(lat, lon) and coords_ok(*HOME):
        return max(25, int(round(haversine(lat, lon, HOME[0], HOME[1]) / 25) * 25))
    return None


def row_distance(r):
    d = to_int(r.get("distance"))
    if d is not None:
        return d
    return dist_home(to_float(r.get("lat")), to_float(r.get("lon")))


def in_scope(r):
    """Drivable, so no shipping: the listing's own state is one of the
    buyer's states. Nothing else — no radius, no coordinates — so scope is
    exactly what the buyer configured, and listings the API could not
    geocode still land in the right bucket."""
    return str(r.get("state", "")).strip().upper() in STATES


def scope_label():
    """The one phrase that says what "drivable" means for this buyer."""
    return "/".join(STATES) or "your states"


def fees_export():
    """What the state and the dealer add on top of the asking price.

    Illinois charges the BUYER's home rate on a vehicle wherever it was bought,
    which is why this belongs on every car on the page rather than only the
    drivable ones — the Phoenix car and the Naperville car are taxed the same.

    Tax is the largest single number the dashboard has never shown: at the
    configured rate it is roughly $4,600 on a $50,000 car, about six times the
    median shipping estimate the page has always displayed prominently. It also
    behaves differently from shipping, which is the reason it can change a
    ranking rather than merely raise every total: tax SCALES with price while
    shipping does not, so it widens the gap between a cheap far car and an
    expensive near one instead of shifting them together.

    `finance_shipping` is a modelling choice, not a fact, and it is here to be
    argued with. The default says no: an auto loan is written against the
    dealer's invoice — price, tax, doc, title, registration — while a transport
    broker is a separate cash transaction weeks later. Roll shipping into the
    principal and every payment on the page is quietly a little too high.

    Nothing here is verified: `checked` is null and the rates are the ones a
    reader would guess from public sources. A tax rate is exactly the kind of
    number that is locally specific and changes, so the page shows a total that
    says "estimate" until this block is dated.
    """
    f = BUYER.get("fees") or {}
    if not f:
        return None
    return {
        "tax_rate": to_float(f.get("tax_rate")) or 0,
        "tax_note": f.get("tax_note") or "",
        "doc_fee": to_int(f.get("doc_fee")) or 0,
        "title": to_int(f.get("title")) or 0,
        "registration": to_int(f.get("registration")) or 0,
        "ev_surcharge": to_int(f.get("ev_surcharge")) or 0,
        # Default False, and read explicitly so a config that omits it gets the
        # documented default rather than a falsy accident.
        "finance_shipping": bool(f.get("finance_shipping", False)),
        "checked": f.get("checked"),
    }


def finance_export():
    """The buyer's rate table, with each promo's expiry already decided here.

    A promo is a real offer with an end date, and an expired one is worth more
    than nothing to a reader — "that 2.99% ran out on the 31st" explains a page
    that suddenly ranks differently. So every promo ships, each carrying an
    `active` the page trusts for arithmetic and an `expires` it can count down
    from. Deciding it here rather than in the browser means one clock (the run's
    TODAY) settles it, instead of whatever the reader's device believes.

    fallback_apr is the rate every non-promo car finances at, and it is the
    number the whole payment ranking pivots on: it drifts with the market and
    nothing fetches it. `fallback_checked` is when a human last verified it, and
    `stale_days` lets the page say so rather than quietly ranking on a rate from
    last spring.
    """
    fin = BUYER.get("finance") or {}
    if not fin:
        return None
    today = date.fromordinal(TODAY_ORD)
    promos = []
    for p in fin.get("promos") or []:
        exp = str(p.get("expires") or "")
        try:
            ends = date.fromisoformat(exp) if exp else None
        except ValueError:
            ends = None
        promos.append({
            "model": p.get("model"),
            "cpo_only": bool(p.get("cpo_only", True)),
            "apr": to_float(p.get("apr")),
            "max_term": to_int(p.get("max_term")),
            "expires": exp or None,
            "label": p.get("label") or "",
            # No end date is a standing offer, not an expired one.
            "active": (ends is None or ends >= today),
            "days_left": ((ends - today).days if ends else None),
        })
    checked = str(fin.get("fallback_checked") or "")
    try:
        stale = (today - date.fromisoformat(checked)).days if checked else None
    except ValueError:
        stale = None
    terms = [t for t in (to_int(x) for x in (fin.get("terms") or [])) if t]
    default_term = to_int(fin.get("default_term")) or 60
    return {
        "fallback_apr": to_float(fin.get("fallback_apr")),
        "fallback_checked": checked or None,
        "stale_days": stale,
        "default_term": default_term,
        "terms": terms or [default_term],
        "down": to_int(fin.get("down")) or 0,
        "promos": promos,
    }


def _ship_bands(raw):
    """buyer.ship_bands as (edge, rate) pairs, widest edge last, open band last.

    Three things this does NOT do the obvious way, each because the obvious way
    fails silently on a hand-edited config:

    A rate of ZERO is kept. Filtering on truthiness dropped it, and "the first
    hundred miles are free" is the one config where a zero rate is meaningful —
    dropping it silently OVERCHARGES, billing those miles at the next band up.

    A missing or unparseable `to` is REJECTED, not read as the open band.
    `"to": null` is a deliberate declaration that a band is the open tail;
    a missing key or `"to": "1,000"` (to_float gives None on the comma) is a
    typo. Conflating them promotes the typo'd band to the catch-all, which
    swallows the whole distance and makes every band after it dead code.

    And the bands are SORTED by edge. band_cost bills the remainder past the
    last edge at the widest band's rate; unsorted, "widest" and "last in the
    list" are different bands, and a descending config bills long hauls at the
    SHORT-haul rate — which reverses the whole point of banding.
    """
    out, seen_open = [], False
    for b in (raw or []):
        rate = to_float(b.get("per_mile"))
        if rate is None:
            print(f"  ! ship_bands: dropping a band with no usable per_mile: {b}")
            continue
        if "to" in b and b.get("to") is None:
            edge = None
        else:
            edge = to_float(b.get("to"))
            if edge is None:
                print(f"  ! ship_bands: dropping a band whose 'to' is missing or "
                      f"unparseable — write null for the open band: {b}")
                continue
        if edge is None:
            if seen_open:
                print(f"  ! ship_bands: a second open band is unreachable, dropping: {b}")
                continue
            seen_open = True
        out.append((edge, rate))
    out.sort(key=lambda t: (t[0] is None, t[0]))
    return out


SHIP_BANDS = _ship_bands(BUYER.get("ship_bands"))
SHIP_ROAD_FACTOR = to_float(BUYER.get("ship_road_factor")) or 1.0


def band_cost(miles):
    """What a haul of this length costs, banded MARGINALLY, or None if unbanded.

    Transport is not linear in distance and never was. A carrier's fixed costs
    — dispatch, loading, the deadhead to reach the car — are the same for 200
    miles as for 2,000, so the short haul carries them alone and the long one
    spreads them. One flat rate therefore has to be wrong at both ends.

    The bands accumulate like tax brackets rather than one replacing another,
    and that is a correctness requirement, not a preference. The first version
    of this picked a single rate by distance, which made the estimate NON-
    MONOTONE: at 423 straight-line miles it charged $574 and at 424 it charged
    $425, so a car one mile further away was $149 cheaper to bring home. Every
    mile is now billed at its own band's rate, so the total can only rise with
    distance while the EFFECTIVE per-mile rate is NON-INCREASING across bands —
    which was the whole point of banding.

    Non-increasing ACROSS BANDS, not strictly falling everywhere: inside a
    single band the effective rate is exactly flat (every mile costs the same),
    and int(round()) in ship_for then jitters it by fractions of a cent, so
    two distances a hundred miles apart in the same band can differ in the
    fourth decimal place in either direction. The property that holds — and
    that the tests check — is between bands.
    """
    if not SHIP_BANDS:
        return None
    total, lo = 0.0, 0.0
    for edge, rate in SHIP_BANDS:
        hi = miles if edge is None else min(miles, edge)
        if hi > lo:
            total += (hi - lo) * rate
        lo = max(lo, hi)
        if edge is not None and miles <= edge:
            break
    if lo < miles:
        # Past the last edge with no open band. SHIP_BANDS is sorted, so the
        # last entry IS the widest — which is the whole reason it is sorted.
        total += (miles - lo) * SHIP_BANDS[-1][1]
    return total


def ship_for(r):
    """Shipping for a listing: nothing in-state; otherwise by distance from
    home when it is known, else the flat ship_cost.

    Two corrections over the flat great-circle estimate this replaces, both
    of which pushed the same way — understating what a distant car costs:

      road factor  A straight line is not a route. Trucks follow interstates
                   around lakes and terrain, and the detour is systematic, not
                   noise: real road miles run above the great-circle figure on
                   essentially every corridor out of Chicago.
      bands        See band_cost. A flat per-mile rate misprices both ends.

    What this buys the RANKING is very little, which is worth knowing before
    anyone spends effort here expecting the shortlist to move. Measured against
    the flat model on the 2026-09-01 snapshot (348 shipped cars of 495):

      median shipped car     +$351   (mean +$288, sd $120)
      landed top 25          3 of 25 positions change, all at 22-24;
                             one car enters, one leaves
      drivable in the top 25 23

    Twenty-three of the top twenty-five are DRIVABLE, so shipping is zero for
    them and no correction to it can touch them at all. And a correction only
    reorders through its DIFFERENTIAL part: this one is mostly a uniform lift
    ($120 of spread against a $300 median gap between adjacent cars), so it
    lifts the shipped cars together rather than past each other. The three
    positions that do change are the tail, where the gaps are smallest.

    Those figures are a SNAPSHOT MEASUREMENT, not a property of the model.
    They move with the market and they move with the bands; re-cut the bands
    and they are stale until re-measured. Two earlier versions of this
    paragraph were wrong for exactly that reason:

      - the first reasoned from the error's MAGNITUDE ($340-790 against those
        gaps) to "the ordering is being decided by the error". Wrong in
        principle: a systematic bias cannot reorder anything, only the spread
        around it can.
      - the second was measured correctly against the ORIGINAL bands
        (1.15/0.85/0.68/0.58), then carried forward verbatim when those bands
        were re-cut to 1.20/0.70/0.45/0.30 a few hours later. Every figure in
        it was false by the time it was committed, including the headline
        claim that the correction "reorders the landed-cost top 25 by zero".
        It reorders three of them.

    What it does buy is an absolute number worth quoting. "Bring it from
    Phoenix for about $1,240" is a sentence this model can now say and the flat
    one could not, and it is the number the fly-and-drive comparison has to be
    right about to mean anything.

    The bands below ship UNCALIBRATED — published typical open-carrier ranges,
    not quotes anyone obtained. That is why buyer.ship_calibrated is null and
    why the page says "estimate" rather than a number: the shape of this model
    is defensible today, its constants are not yet, and pretending otherwise
    would trade a wrong number for a wrong number that looks researched. Put
    three real quotes on real corridors into buyer.ship_quotes, set the date,
    and calibrate() will tell you how far off the bands are.
    """
    if in_scope(r):
        return 0
    d = row_distance(r)
    if d is None:
        return to_int(BUYER.get("ship_cost")) or 0
    floor = to_float(BUYER.get("ship_min")) or 0
    road = d * SHIP_ROAD_FACTOR
    banded = band_cost(road)
    if banded is not None:
        return int(round(max(floor, banded)))
    # No bands configured: the flat rate this replaced, unchanged, so a config
    # without ship_bands keeps behaving exactly as it did. Straight-line miles
    # here, not road miles: SHIP_ROAD_FACTOR is part of the banded model.
    rate = to_float(BUYER.get("ship_per_mile"))
    if not rate:
        return to_int(BUYER.get("ship_cost")) or 0
    return int(round(max(floor, d * rate)))


def ship_calibration():
    """How far the bands are from the quotes the buyer actually collected.

    Returns None until buyer.ship_quotes has something in it. Each quote is
    {miles, price, route} — the miles the BROKER quoted, not the great-circle
    figure, because that is the number the model is trying to predict.
    """
    raw = BUYER.get("ship_quotes") or []
    if not isinstance(raw, list):
        print("  ! ship_quotes is not a list; ignoring it")
        return None
    quotes, dropped = [], 0
    for q in raw:
        if not isinstance(q, dict):
            dropped += 1
            continue
        miles, price = to_float(q.get("miles")), to_float(q.get("price"))
        if miles is None or miles <= 0 or price is None or price <= 0:
            dropped += 1
            continue
        quotes.append((miles, price))
    # A quote written with the wrong key is indistinguishable from no quote at
    # all, and this is the ONE documented path out of the uncalibrated state.
    # Silence here means a buyer who collected three real quotes sees exactly
    # what they saw before collecting them.
    if dropped:
        print(f"  ! ship_quotes: {dropped} quote(s) ignored — each needs a positive "
              f"`miles` and `price`")
    if not quotes:
        return None
    errs = []
    for miles, price in quotes:
        est_raw = band_cost(miles)
        if est_raw is None:
            continue
        est = max(to_float(BUYER.get("ship_min")) or 0, est_raw)
        errs.append(est - price)
    if not errs:
        return None
    return {"n": len(errs),
            "mean_error": round(sum(errs) / len(errs)),
            "worst": round(max(errs, key=abs)),
            "calibrated": BUYER.get("ship_calibrated")}


def adjusted(price, miles, ship=0):
    """Asking plus shipping. The optional mileage adjustment (cents_per_mile,
    off by default) is the only thing that could ever take it below asking."""
    if price is None:
        return None
    base = to_int(BUYER.get("mileage_baseline")) or 20000
    cpm = to_float(BUYER.get("cents_per_mile")) or 0
    mileage = (miles - base) * cpm if (miles is not None and cpm) else 0
    return int(price + ship + mileage)


def landed(r):
    ship = ship_for(r)
    return adjusted(to_int(r["price"]), to_int(r["miles"]), ship), ship


# --------------------------------------------------------------------------
# Our picks: best value, not lowest price. Eligible cars are ranked by how far
# under the typical (median) value of their own model they sit, where value is
# asking + shipping + a mileage allowance. The reader only ever sees asking.
# --------------------------------------------------------------------------
NON_PERSONAL = ("rental", "fleet", "corporate", "commercial", "taxi", "livery",
                "government", "multiple")


def is_rental(x):
    u = str(x.get("usage", "")).lower()
    return any(k in u for k in NON_PERSONAL)


def pick_value(x):
    price, miles = to_int(x.get("price")), to_int(x.get("miles"))
    if price is None or miles is None:
        return None
    base = to_int(PICKS.get("mileage_baseline")) or 20000
    cpm = to_float(PICKS.get("cents_per_mile"))
    if cpm is None:
        cpm = 0.30
    ship = 0 if x.get("local") else (to_int(x.get("ship")) or 0)
    return price + ship + (miles - base) * cpm


def pick_eligible(x):
    price, miles = to_int(x.get("price")), to_int(x.get("miles"))
    if price is None or miles is None:
        return False
    if miles > (to_int(PICKS.get("max_miles")) or 50000):
        return False
    if PICKS.get("exclude_accidents", True) and (to_int(x.get("accidents")) or 0) > 0:
        return False
    if PICKS.get("exclude_rental", True) and is_rental(x):
        return False
    return True


def trim_disp(model_label, trim):
    """The trim, minus any words the model label already carries, so a
    cohort reads '2023 BMW i7 xDrive60' and never 'BMW i7 i7 xDrive60'."""
    words = {w.lower() for w in str(model_label).split()}
    return " ".join(w for w in str(trim or "").split() if w.lower() not in words)


# Six, not three, and for the reason exit_stats gives: five is the largest n
# at which NO distribution-free 95% interval for a median exists, so a cohort
# of three, four or five cars has a median with no honest error bar — and "21%
# under typical" is a claim about that median. Every surface that prints the
# percentage gates it on the interval score_picks ships, so a cohort that
# cannot carry an interval cannot carry a percentage either: the car falls
# through to the next cohort up, or to silence. docs/index.html carries the
# same six under the same name; the two must agree or the report and the
# page score different cars.
COHORT_FLOOR = 6


def cohort_stats(values):
    """(median, low edge, high edge, n) of one cohort's values. Only called
    at COHORT_FLOOR and above, where the interval always exists."""
    lo, hi = median_ci(values)
    return median(values), lo, hi, len(values)


def score_picks(listings, model_label):
    """Every eligible car of one model, scored against the tightest cohort
    with COHORT_FLOOR or more eligible cars: its own trim and model year
    first, then the model year, then the whole model. Without the cohorts a
    2023 eDrive50 i7 reads as half price simply because the blended median
    carries six-figure M70s and 2026 cars.

    Each score carries its cohort's 95% interval for the median (pick_lo,
    pick_hi) and where the car STANDS against it: "under" only when its
    value sits below the low edge, "over" only above the high edge,
    "typical" anywhere inside. A car 5% under a median of nine cars whose
    interval spans 14% is not under typical — it is indistinguishable from
    it — and no surface may say otherwise, however positive pick_pct is.
    The percentage stays the best estimate of the margin; the stand is
    whether the data can defend the word. The interval is two-sided, as
    exit_stats ships it, so each edge is a 97.5% one-sided bound — a shade
    conservative for a one-directional word, and the same interval the
    exit note already suppresses on. At six, seven and eight cars the
    interval is the whole sample, so no car in such a cohort is ever called
    under or over: that is the arithmetic, not a policy.

    A stand also asks for a margin that rounds to at least 1%. The interval
    makes the WORD defensible; the rounded percentage is the NUMBER every
    surface prints beside it, and "0% under typical" is a claim with no
    content — on a cohort tight enough, a car $100 below the low edge would
    print exactly that. Half a percent is the smallest margin that rounds
    to a digit, on both sides of the sheet.
    """
    pool = [x for x in listings if pick_eligible(x)]
    if len(pool) < COHORT_FLOOR:
        return []
    values = {id(x): pick_value(x) for x in pool}
    by_year, by_ty = defaultdict(list), defaultdict(list)
    for x in pool:
        y = str(x.get("year") or "")
        tr = str(x.get("trim") or "").strip().lower()
        by_year[y].append(values[id(x)])
        if tr:
            by_ty[(tr, y)].append(values[id(x)])
    # the trims behind each cohort, for the comparability guard below
    trim_set_all, trim_set_year, trim_set = set(), defaultdict(set), defaultdict(set)
    for x in pool:
        tr = str(x.get("trim") or "").strip().lower()
        y = str(x.get("year") or "")
        trim_set_all.add(tr)
        trim_set_year[y].add(tr)
        trim_set[(tr, y)].add(tr)
    st_all = cohort_stats(list(values.values()))
    year_st = {y: cohort_stats(vs) for y, vs in by_year.items() if len(vs) >= COHORT_FLOOR}
    ty_st = {k: cohort_stats(vs) for k, vs in by_ty.items() if len(vs) >= COHORT_FLOOR}
    out = []
    for x in pool:
        y = str(x.get("year") or "")
        tr = str(x.get("trim") or "").strip().lower()
        # WHICH cohort, and HOW MANY cars are in it. "21% under typical" is not
        # a claim until both are said: the same percentage means one thing
        # against 23 cars of the same trim and model year and quite another
        # against a whole model's blended median, which on the iX is six M60s
        # and one xDrive50. The level and the count now ride on the pick, so
        # every surface that prints the percentage can print what it is under.
        if (tr, y) in ty_st:
            st, p_year, p_trim, basis = ty_st[(tr, y)], y, trim_disp(model_label, x.get("trim")), "trim"
        elif y in year_st:
            st, p_year, p_trim, basis = year_st[y], y, "", "year"
        else:
            st, p_year, p_trim, basis = st_all, "", "", "model"
        med, lo, hi, n = st
        v = values[id(x)]
        # …and the fallback cohorts must still be COMPARABLE. The interval
        # asks whether the median is stable; it cannot ask whether the cars
        # behind it are the same kind of car. Four 2022 iX xDrive50s were
        # printed "26–40% under typical for a BMW iX, n=21" against a pool of
        # ten M60s and eleven xDrive50s — the M60 is a different car at a
        # different price, so the percentage measured the pool's trim mix, not
        # the car. A 2024 Ioniq 5 SE read "18% under" against SEs and SELs,
        # and a Kia EV9 Light Long Range "15% under" a 2024 pool of Light,
        # Land, GT-Line and Light Long Range — one model year, four cars.
        #
        # So a cohort speaks only when every car in it wears the scored car's
        # trim. That is automatic on the trim basis and an accident on the
        # other two, and it is the guard the compare card has always applied
        # to its winner (`comparableCohorts`, docs/index.html) — one shape,
        # now on every surface. A mixed cohort is not "about typical", which
        # is an affirmative claim the interval earns and this does not: it is
        # `mixed`, and the page says too few comparable cars to say.
        cohort_trims = (trim_set[(tr, y)] if basis == "trim"
                        else trim_set_year[y] if basis == "year" else trim_set_all)
        mixed = bool(cohort_trims - {tr})
        out.append({**x, "model_label": model_label,
                    "pick_year": p_year, "pick_trim": p_trim,
                    "pick_basis": basis, "pick_n": n,
                    "pick_lo": lo, "pick_hi": hi,
                    "pick_stand": ("mixed" if mixed
                                   else "under" if v < lo and (med - v) / med >= 0.005
                                   else "over" if v > hi and (v - med) / med >= 0.005
                                   else "typical"),
                    # floor(x + .5), not round(): Python rounds half to even and
                    # JavaScript rounds half up, and the page recomputes this
                    # figure — two picks read "$1,936 less" here and "$1,937
                    # less" there over an exact .5 residual. Same for negatives:
                    # Math.round(-1485.5) is -1485, which is floor(-1485.0).
                    "pick_under": int(math.floor(med - v + 0.5)),
                    "pick_pct": (med - v) / med if med else 0.0})
    out.sort(key=lambda p: -p["pick_pct"])
    return out


def choose_picks(scored, n, per_model=None, seed=None):
    out, per = [], Counter(seed or {})
    for p in sorted(scored, key=lambda p: -p["pick_pct"]):
        # Under the low edge of its cohort's interval, not merely under the
        # median. The list is sorted by margin, and a car inside a wide
        # interval can carry a bigger margin than one below a narrow edge,
        # so this skips rather than breaks.
        if p.get("pick_stand") != "under":
            continue
        if per_model and per[p["model_label"]] >= per_model:
            continue
        out.append(p)
        per[p["model_label"]] += 1
        if len(out) >= n:
            break
    return out


def choose_picks_reserving(scored, n, per_model=None, reserve=0):
    """choose_picks, with the first `reserve` drivable seats held for the models
    actually being shopped.

    A comparison car can sit further under its own typical price than any i5
    ever will — an Ioniq 5 at 21% under a typical Ioniq 5 outranks every BMW on
    the sheet — so ranking the drivable list by margin alone fills the front
    page with cars this buyer is not shopping for. On today's data all four
    drivable picks were an Ioniq 5, an Ioniq 5, an iX and an EV9, with neither
    car being decided on among them.

    The dashboard has done this since the multi-select release; this side never
    did, and nothing in targets.json or the export said the rule existed — the
    page carried a hard-coded 2. So the two surfaces disagreed about half the
    front page while both claimed to apply the same rule. `buyer.picks
    .reserve_shopping` is now that rule, in one place, read by both; set it to 0
    and both rank purely by margin again.
    """
    if not reserve:
        return choose_picks(scored, n, per_model)
    shop = choose_picks([p for p in scored if p.get("shopping")],
                        min(reserve, n), per_model)
    if not shop:
        return choose_picks(scored, n, per_model)
    per, taken = Counter(), {p["vin"] for p in shop}
    for p in shop:
        per[p["model_label"]] += 1
    rest = choose_picks([p for p in scored if p["vin"] not in taken],
                        n - len(shop), per_model, per)
    return shop + rest


def split_picks(scored, n, per_model=None, reserve=0):
    """Two lists under the same rule: drivable picks, and worth-the-ship
    picks from everywhere else. Scoring stays within-model across the whole
    market, so a drivable pick means the same thing as a national one.

    Only the drivable list reserves seats: it is the short list a buyer can act
    on this weekend, and it is the one the shopped cars keep falling out of.
    """
    return (choose_picks_reserving([p for p in scored if p.get("local")],
                                   n, per_model, reserve),
            choose_picks([p for p in scored if not p.get("local")], n, per_model))


def fmt_pick(p):
    bits = [f"**{money(to_int(p['price']))}**", p["model_label"], str(p.get("year", ""))]
    if to_int(p.get("miles")) is not None:
        bits.append(f"{to_int(p['miles']):,} mi")
    bits.append("drivable · no shipping" if p.get("local") else
                (f"+ {money(to_int(p['ship']))} shipping" if to_int(p.get("ship")) else "shipping n/a"))
    bits.append(place(p))
    line = "- " + " · ".join(b for b in bits if b)
    cohort = " ".join(b for b in (p.get("pick_year"), p["model_label"],
                                  p.get("pick_trim")) if b)
    # the cohort's SIZE, because a percentage under a median of three cars and
    # one under a median of twenty-three are different claims wearing one word
    n = p.get("pick_n")
    line += (f"\n  _spicy pick: {p['pick_pct']:.0%} under typical for a {cohort} "
             f"({money(p['pick_under'])} less"
             + (f", from {n} such car{'s' if n != 1 else ''}" if n else "") + ")_")
    if p.get("flags"):
        line += f" · _{' · '.join(p['flags'])}_"
    if p.get("url"):
        line += f"\n  [listing]({p['url']})"
    line += f" `{p.get('vin', '')}`"
    return line


def two_prices(series):
    """[low, high] when a car's record is two prices held in turn, else None.

    The storefront sawtooth: a VIN that surfaces through a group's storefronts
    at two fixed prices on alternate days reads as a series of cuts and
    restorations — WBY33FK05RCP99465 shipped as 4 cuts on a record that was
    54,999 / 55,849 / 54,999 / 55,849 for thirteen days — and every downward
    step of it was counted as a cut a dealer took. Two distinct prices, each
    seen at least twice, with a step in each direction: that shape is the
    car being SEEN at two prices, and the page says exactly that and no
    more. A single blip up and back, or a cut that was later put back once,
    has one of its prices seen only once and stays what it is: a cut that
    did not stick, which the "cut and put back" count already names. The
    dealer string flipping with the price is what would make this "two
    storefronts", and the sheet does not carry the dealer by day, so the
    page does not say it.
    """
    prices = [p for _, p in (series or []) if p]
    distinct = sorted(set(prices))
    if len(distinct) != 2 or len(prices) < 4:
        return None
    if any(prices.count(p) < 2 for p in distinct):
        return None
    steps = [b - a for a, b in zip(prices, prices[1:]) if b != a]
    if not any(d < 0 for d in steps) or not any(d > 0 for d in steps):
        return None
    return distinct


# Under a hundred miles a car is dealer stock rather than a used car — a demo,
# a loaner, a press car — and it reaches this API on the same used-listings
# endpoint at a different price. docs/index.html carries the same number as
# NEW_STOCK_MILES; the two must not drift, because the page and the record
# print the same split from it.
NEW_STOCK_MILES = 100


def market_stats(listings):
    """Per-model market context — the numbers a negotiation opens with: how
    long cars typically sit, what share have been cut while tracked, and the
    typical size of a cut. Also stamps each listing with stale_pct: the share
    of the model's cars it has outlasted on the market."""
    dl = sorted(x["days_listed"] for x in listings
                if x.get("days_listed") is not None)
    for x in listings:
        d = x.get("days_listed")
        x["stale_pct"] = (round(sum(1 for v in dl if v < d) / len(dl), 2)
                          if dl and d is not None else None)
        # …and what that share is a share OF: the cars carrying a listing
        # date, 85 of the i5's 134. "Longer than 75% of the model" claimed
        # the whole model and meant the dated part of it.
        #
        # The pool is the MODEL's, always — market_stats runs once per model,
        # over every listing on it. The page's market sentence is recomputed
        # for the rows in view, so on a trim view it says "75 of 92 dated"
        # one line above a row saying "of the 113 dated cars". Both are true
        # and they read as a contradiction, so the row says whose 113 it is.
        x["stale_of"] = len(dl) if dl else None
    # Cars with no mileage are in neither market, exactly as on the page: the
    # split is a claim about two populations and a car that cannot be assigned
    # to one is not evidence about either.
    d_stock = [x["days_listed"] for x in listings
               if x.get("days_listed") is not None
               and to_int(x.get("miles")) is not None and to_int(x["miles"]) < NEW_STOCK_MILES]
    d_used = [x["days_listed"] for x in listings
              if x.get("days_listed") is not None
              and to_int(x.get("miles")) is not None and to_int(x["miles"]) >= NEW_STOCK_MILES]
    tracked = [x for x in listings if x.get("days_tracked", 0) >= 2]
    # Seen at two prices is not cut: those cars leave every cut figure and
    # are counted apart, with the sentence saying so — see two_prices().
    two_priced = [x for x in tracked if two_prices(x.get("series"))]
    swings = {id(x) for x in two_priced}
    cut_cars = [x for x in tracked if x.get("cuts") and id(x) not in swings]
    drops = []
    for x in listings:
        if id(x) in swings:
            continue
        s = x.get("series") or []
        drops += [a - b for (_, a), (_, b) in zip(s, s[1:]) if b < a]
    # A cut that stuck, told apart from a step that bounced back. cut_share
    # counts any car with a downward step in its series, and a step reversed
    # on the next fetch counts the same as one that held: on this record a
    # third of the i5's downward steps were later reversed, and 17 of its 86
    # "cut" cars ask today what they asked when first seen — the storefront
    # sawtooth of a group listing one VIN at two prices on alternate days.
    # "66% cut while tracked" is true and is not the number a buyer can act
    # on; "69 of 131 ask less than when first seen, median $900 less" is.
    # Counts with their denominator, never a rate, so the two never get
    # compared across trims by eye.
    net_down = [x for x in tracked if (x.get("delta") or 0) < 0 and id(x) not in swings]
    restored = [x for x in tracked if x.get("cuts") and (x.get("delta") or 0) >= 0 and id(x) not in swings]
    counted = len(tracked) - len(two_priced)
    return {
        "median_days_listed": int(median(dl)) if dl else None,
        # …and each market's own, when both are big enough to have one. Under
        # a hundred miles a car is dealer stock — demos, loaners, press cars,
        # priced near sticker — and the used-listings API serves them beside
        # the used cars. The i7's "typical car 29d" is 49 stock cars at a
        # median 78 days blended with 61 used at 21, and 29 is a number no car
        # on either side sits at. The page has printed the split since the days
        # clause was built; this is the same rule and the same floor, so the
        # record stops describing a market that is not there.
        "days_split": ({"stock": {"n": len(d_stock), "days": int(median(d_stock))},
                        "used": {"n": len(d_used), "days": int(median(d_used))}}
                       if len(d_stock) >= 12 and len(d_used) >= 12 else None),
        # …over how many of how many. The dashboard has printed "(85 of 134
        # dated)" since the days split was built; the committed record printed
        # a bare median of the 85 as if it were the model's.
        "dated": len(dl), "n": len(listings),
        "tracked_2d": len(tracked),
        "cut_share": round(len(cut_cars) / counted, 2) if counted else None,
        "median_cut": int(median(drops)) if drops else None,
        # …and how many steps that median is over. The share beside it counts
        # CARS (117 on the i5) and the median counts downward STEPS (139), a
        # mismatch one sentence hid by giving the median no denominator at
        # all. It is also the honest answer to "could a $1 move be propping
        # this up": a median of $600 over 139 steps cannot be.
        "n_cuts": len(drops),
        "net_down": len(net_down),
        "restored": len(restored),
        "median_net_drop": int(median(-x["delta"] for x in net_down)) if net_down else None,
        "two_priced": len(two_priced),
    }


def departures_are_separable(t):
    """Can this target's departures be told apart from window churn?

    Only on a SINGLE-SORT target, and the reason is that the snapshot CSV does
    not record which query returned each row. A target fetching both price.asc
    and miles.asc has TWO windows, and delisted() reconstructs a vanish day's
    cut-off as the extreme kept value on ONE axis (window_dim), pooled across
    both. That pooling is not conservative, it is backwards: the miles.asc rows
    are the expensive delivery-mileage cars, so they push the reconstructed
    PRICE ceiling up above every car in the set, and nothing can then be judged
    out of window.

    What that produced, live, on bmw-i7-edrive50: nine "delistings" of which
    eight are 1-to-4-mile 2026 cars asking $115k-$125k. They did not leave the
    market — newer delivery-mileage cars pushed them out of the lowest-by-miles
    window. Their median became a published `exit_price` of $118,834, and the
    dashboard subtracted live cars from it, telling the reader a $54,000 i7 was
    "$64,834 below where this trim's listings ended".

    A value-based per-axis cut-off does not rescue this either: at 40 cars
    inside 9 miles, rank and value come apart, so "3 miles" is inside the
    window by value and outside it by rank on the same day. The distinction
    needs provenance the CSV has never carried.

    So the EXIT PRICE — the one claim that asserts these were comparable market
    exits, in dollars, next to a car being decided on — is withheld here. The
    gone list itself still shows every departure with its own `likely` label,
    because "this stopped being listed" is true whatever the cause.

    WHICH TARGETS THOSE ARE was the part this got wrong, and it read the config
    instead of the fetch. `sorts` is the CONFIGURED list; `sorts_pages()` is
    what a run actually asks for, and a `light` target takes only the first of
    them. Eleven of the fourteen targets are light, so this function called
    them two-window targets while they have only ever opened one — and withheld
    an exit price from every one of them for a reason that does not apply.
    window_reconstructable() is the same question asked of the fetch, and it
    also excludes the newest-first probe, which returns cars at any price and
    would pollute the axis exactly as a second sort does.

    That leaves the withholding where it belongs: bmw-i5-edrive40 and
    bmw-i7-edrive50, the two shopped trims that really do open two windows plus
    a newest probe, still publish nothing.
    """
    return window_reconstructable(t)


def window_reconstructable(t):
    """Can a fetch day's window be rebuilt from the snapshot rows alone?

    Only when every row the target kept that day arrived through ONE query
    shape on the window's own axis. Two things break that, and both put rows
    into the CSV that sit ABOVE the cut-off being reconstructed:

      a second sort  a price-dim target that also fetches miles.asc keeps the
                     expensive delivery-mileage cars, and the widest kept price
                     is then one of those, not the price.asc cut-off. This is
                     the pooling departures_are_separable() describes.
      newest         a createdAt.desc probe returns whatever listed today at
                     any price, so one fresh six-figure car raises the widest
                     kept value above every real window.

    It asks sorts_pages(), not t["sorts"], because those are different lists:
    a `light` target carries the two-sort default in its config and fetches
    only the first of them. Eleven of the fourteen targets are light, so the
    configured list says "two windows" about targets that only ever open one.
    """
    sorts, _ = sorts_pages(t)
    return len(sorts) <= 1 and not t.get("newest")


def median_ci(values, conf=0.95):
    """A distribution-free confidence interval for a median, from the order
    statistics. Returns (lo, hi) or None when the sample cannot support one.

    No assumption about the shape of the price distribution, which is the point
    — exit prices are skewed and small-n, and a normal-theory interval on eight
    of them would be a worse lie than no interval.

    The rank k is the largest with P(k <= position of median <= n+1-k) >= conf
    under Binomial(n, 0.5). At n=5 no such k exists: even min..max covers only
    93.75%, which is why five is not a floor anyone should publish a median at.
    """
    xs = sorted(v for v in values if v is not None)
    n = len(xs)
    if n < 6:
        return None
    # P(k <= B <= n-k) for B ~ Bin(n, 1/2), walking k up while it still covers.
    # Compared EXACTLY, as a ratio of integers against 19/20, because the page
    # draws the same interval in JavaScript with integer arithmetic and the two
    # must agree on every edge at every n. No n up to 2000 has a coverage
    # within float rounding of 19/20 (the nearest sits 4.9e-6 away, at n=417),
    # so this is parity by construction rather than a fix for a case that was
    # seen; the concrete change is that 2 ** n no longer overflows where
    # 2.0 ** n did, from n = 1024. Fraction(0.95) is the float's own value,
    # not 19/20; limit_denominator returns it to the fraction that was meant.
    from fractions import Fraction
    from math import comb
    total = 2 ** n
    want = Fraction(conf).limit_denominator(10 ** 6)
    best = None
    for k in range(1, n // 2 + 1):
        covered = Fraction(sum(comb(n, i) for i in range(k, n - k + 1)), total)
        if covered >= want:
            best = k
        else:
            break
    if best is None:
        return None
    return (xs[best - 1], xs[n - best])


def exit_stats(gone, trim_id, floor=6):
    """One trim's exit prices, or an empty dict when too few cars have left.

    `floor` is not decoration. Two departures make a median that swings by
    thousands on the next one, and a number that unstable printed beside a live
    car reads as authority it has not earned. Below the floor the page shows
    nothing rather than something shaky — the same rule the cut-share line
    already follows.

    Six, not five, and the reason is arithmetic rather than taste: five is the
    largest n at which NO distribution-free interval for a median exists at
    all. Even min-to-max covers only 93.75% of the time at n=5, so a median of
    five is a number with no honest error bar to put beside it. Six is the
    first n where the whole sample is a 95% interval.

    The interval itself ships as exit_lo/exit_hi so the page can compare a
    car's price against the median's OWN uncertainty instead of a flat
    threshold. It used to use $500, which on these cohorts is between 10 and 42
    times narrower than the real interval — 42% of the notes it drew named a
    gap smaller than the sampling error of the number they were drawn against.
    """
    t = TARGETS.get(trim_id) or {}
    if not departures_are_separable(t):
        return {}
    rows = [g for g in gone if g.get("trim_id") == trim_id]
    st = sale_stats(rows)
    if (st.get("n_exits") or 0) < floor:
        return {}
    exits = [to_int(g.get("last_price")) for g in rows
             if g.get("likely") == "delisted" and not g.get("still_listed")
             and to_int(g.get("last_price")) is not None]
    ci = median_ci(exits)
    return {"exit_n": st["n_exits"],
            "exit_price": st["median_exit_price"],
            "exit_lo": ci[0] if ci else None,
            "exit_hi": ci[1] if ci else None,
            "exit_watched": st.get("exit_watched", 0),
            "exit_cut_seen": st.get("exit_cut_while_watched", 0),
            "exit_days": st.get("median_days_to_sale")}


def cut_tag(cuts, delta):
    """What a price history did, in words that survive it going back up.

    `cuts` counts the downward steps and `delta` is last minus first, so a
    listing cut and then restored has cuts >= 1 and delta >= 0 — and the report
    printed that as "down 1x ($0)", which reads as a discount of nothing rather
    than as a car that is no cheaper than it started. Five lines of one day's
    report said exactly that, one of them for a car back at its exact opening
    price, and the same shape can print a POSITIVE delta as if it were a cut.

    The aggregate definition of a cut is deliberately left alone: "was cut at
    some point" is a true and useful thing to count, and a second real drop
    that lands above an earlier low is still a price change the buyer wants to
    see today. Only the sentence changes.
    """
    n = f"down {cuts}x"
    if delta < 0:
        return f"{n} ({money(delta)})"
    if delta > 0:
        return f"cut {cuts}x, now {money(delta)} above first seen"
    return f"cut {cuts}x, then back up"


def departure_is_evidence(g):
    """Can this departure be told apart from fetch-window churn?

    The same question exit_stats() and one_cohort() already ask before they
    publish a price, asked here so the COUNT is held to the standard the price
    is. A two-window target's "delisted" row may be a car that sold or a car
    that a newer, cheaper arrival pushed out of the window this run — that is
    the whole reason its exit price is withheld — so counting it as a car that
    left the market, and pricing the market's velocity from it, publishes on
    evidence the same file refuses to price.

    window_reconstructable(), not departures_are_separable()'s configured sorts
    list: eleven of fourteen targets are `light` depth and only ever open the
    first of the two sorts their config names, and gating on the config would
    withhold the phrase from every model on the sheet for a reason that does
    not apply to any of them.
    """
    # A car still listed under another of the model's targets did not leave
    # the market, whatever the query that lost it can prove — see delisted().
    if g.get("still_listed"):
        return False
    if "exact" in g:
        return bool(g["exact"])
    # A sheet written before delisted() carried its own provenance: fall back
    # to the shape of the target, which is what the reconstruction could prove
    # before the fetch log existed. An UNKNOWN target is not a defence — this
    # gate exists to keep a number off the page, and "nothing on record says
    # which query found this car" is not a reason to publish it.
    t = TARGETS.get(g.get("trim_id"))
    return bool(t) and window_reconstructable(t)


def sale_stats(gone):
    """How fast this model's cars actually leave, from the ones that really
    left: days from the listing date (or first sighting, when the dealer
    never said) to the last day the car was seen. Out-of-window and
    unchecked departures are not sales and are left out."""
    spans = []
    for g in gone:
        if g.get("likely") != "delisted":
            continue
        # …and a departure this target cannot separate from window churn is
        # not evidence that a car left. See departure_is_evidence().
        if not departure_is_evidence(g):
            continue
        # The listing date, and ONLY the listing date. first_seen used to stand
        # in for it, which measured how long the TRACKER had been watching: on a
        # ten-day-old record no span could exceed ten days, so "listings ran at
        # least ~5d" would have been a fact about this repo's start date. Cars
        # whose listed_since is missing — or was withheld as an index load, see
        # find_index_dates() — have no measurable span and are left out rather
        # than given the tracker's own.
        start = g.get("listed_since")
        if not start:
            continue
        try:
            spans.append(max(0, (date.fromisoformat(str(g.get("last_seen"))[:10])
                                 - date.fromisoformat(str(start)[:10])).days))
        except (TypeError, ValueError):
            continue
    # What they were asking when they went. The closest thing a tool with no
    # transaction feed will ever have to a sale price — and emphatically not a
    # sale price, which is why nothing here is called one. A delisted car may
    # have sold, gone to auction, moved to a sister lot, or simply had its ad
    # expire; all four look identical from outside. What IS true is that the
    # last ask is the last number the market saw and did not beat, so a live
    # car asking well above its trim's exit prices is asking above where
    # comparable cars stopped being advertised. That is a weaker claim than
    # "overpriced" and it is the one the data supports.
    exits, cuts = [], []
    for g in gone:
        if g.get("likely") != "delisted":
            continue
        # Same gate as the spans above, and for the same reason: an exit price
        # is a claim about where cars stopped being advertised, and a car that
        # merely fell out of this run's window did not stop being advertised.
        if not departure_is_evidence(g):
            continue
        last = to_int(g.get("last_price"))
        if last is None:
            continue
        exits.append(last)
        # NOT a median cut. Half these cars are observed on two days or fewer
        # of a listing life whose median is over three weeks, and a quarter are
        # seen exactly once, where a cut is arithmetically impossible. The
        # median of that is $0 for every trim on the sheet — a number that
        # describes the fetch cadence, not the market, and would read as "these
        # cars never discount" when it means "we mostly were not looking".
        # What IS honest is the count that cut while we watched, over the
        # number we could have seen cut at all.
        series = g.get("series") or []
        if len(series) >= 2:
            first = to_int(series[0][1]) if len(series[0]) > 1 else None
            cuts.append(1 if (first and first > last) else 0)
    return {"n_sold": len(spans),
            "median_days_to_sale": int(median(spans)) if spans else None,
            # Deliberately not "sold_price": see above.
            "n_exits": len(exits),
            "median_exit_price": int(median(exits)) if exits else None,
            "exit_watched": len(cuts),
            "exit_cut_while_watched": sum(cuts)}


def one_cohort(gone):
    """Do the departed cars behind a pooled exit median describe ONE trim?

    This asks the DATA, not the watchlist, and the difference is the whole
    point. The first version was `len(trims) == 1`, counting watchlist targets
    under a model — which inverts the test it was written for. A target like
    `kia-ev9` is one entry covering the entire model, so it scored one_trim
    TRUE while pooling a Light with a GT-Line; `audi-a6-etron` scored TRUE over
    a cohort spanning $28,077. The gate passed on exactly the catch-all
    targets it existed to stop, and blocked only the models whose trims are
    tracked properly enough to be separable.

    So: count the distinct trim labels actually carried by the cars whose
    prices form the median. One label (or none to distinguish) is a cohort.
    Anything else is an average of cars that do not exist.
    """
    labels = set()
    for g in gone:
        if (g.get("likely") != "delisted" or g.get("still_listed")
                or to_int(g.get("last_price")) is None):
            continue
        # A departure this target cannot separate from window churn is not
        # evidence about anything, so it cannot make a cohort either.
        if not departures_are_separable(TARGETS.get(g.get("trim_id")) or {}):
            return False
        labels.add((g.get("trim") or g.get("trim_id") or "").strip().lower())
    return len(labels) <= 1


def market_line(stats):
    """The market context as one report phrase, or ''. """
    bits = []
    if (stats.get("median_days_listed") is not None
            and (stats.get("dated") is None or stats["dated"] >= 12)):
        # Twelve dated cars or nothing, and the count beside it — the page's
        # rule and the page's words, which the record did not carry.
        sp = stats.get("days_split")
        bits.append(f"typical car {stats['median_days_listed']}d on market"
                    + (f" ({stats['dated']} of {stats['n']} dated)" if stats.get("n") else "")
                    + (f" — {sp['stock']['n']} dealer stock at {sp['stock']['days']}d, "
                       f"{sp['used']['n']} used at {sp['used']['days']}d" if sp else ""))
    if stats.get("cut_share") is not None and stats.get("tracked_2d", 0) >= 5:
        # The cuts that stuck lead, with their denominator — see market_stats.
        # The share of cars with any downward step follows, since it is the
        # figure the record has carried since the start and the two together
        # say how much of the cutting was theatre.
        if stats.get("net_down") is not None:
            held = f"{stats['net_down']} of {stats['tracked_2d']} ask less than when first seen"
            if stats.get("median_net_drop"):
                held += f", median {money(stats['median_net_drop'])} less"
            if stats.get("restored"):
                held += f" · {stats['restored']} cut and put back"
            bits.append(held)
        # …with the denominator the page has always printed. The share counts
        # CARS and the median counts downward STEPS: two different figures in
        # one clause, and until now only one of them said what it was over.
        counted = stats.get("tracked_2d", 0) - stats.get("two_priced", 0)
        cut = f"{stats['cut_share']:.0%} of {counted} cut while tracked"
        if stats.get("median_cut"):
            cut += f", median {money(stats['median_cut'])}"
            if stats.get("n_cuts"):
                cut += f" of {stats['n_cuts']} cut{'s' if stats['n_cuts'] != 1 else ''}"
        if stats.get("two_priced"):
            cut += f" · {stats['two_priced']} seen at two prices, not counted"
        bits.append(cut)
    # "Delisted" is not "sold" — a car whose ad ends may have sold, gone to
    # auction, moved lots, or simply expired, and none of those are
    # distinguishable from outside. The field names stay as they are because
    # they ship in data.json and the report, but the sentence a reader actually
    # sees should claim only what happened: the listing ended.
    # Twelve, not five, on a BARE median — one printed with no interval beside
    # it, so the floor is the only thing doing the work. At five no
    # distribution-free interval exists at all; at eight, this model's own
    # "~19d" spans 3 to 57 days, which is not a fact about the market. Twelve
    # is where the interval narrows to the middle two thirds of the sample.
    #
    # "at least", because the span is RIGHT-CENSORED. It runs from the list
    # date to the last day the car was SEEN, and the listing actually ended
    # somewhere between that day and the next fetch — a mean of 2.0 days later
    # on this data, against a median span of 15. Saying "ended after ~15d"
    # understates listing life by around 17% and makes the market look faster
    # than it is; "ran at least" is the same number without the overclaim.
    if stats.get("median_days_to_sale") is not None and stats.get("n_sold", 0) >= 12:
        bits.append(f"listings ran at least ~{stats['median_days_to_sale']}d "
                    f"({stats['n_sold']} gone)")
    # Model level only. A median mixing an eDrive50 with an M70 describes no car
    # that exists — exit_stats() says so and refuses to compute one per model —
    # so the report shows this ONLY where a model has a single trim. Everywhere
    # else the per-trim figures on the dashboard are the honest ones.
    if (stats.get("median_exit_price") and stats.get("n_exits", 0) >= 12
            and stats.get("one_trim")):
        # The cut count needs a real denominator before it is worth a sentence.
        # At five, "3 of 7" carries a confidence interval from roughly 10% to
        # 80% — a number that invites a comparison it cannot support, which is
        # exactly the mistake the $0 median made in a different costume. Twelve
        # is still small; it is the point where the count stops pretending to
        # be a rate.
        watched, cut = stats.get("exit_watched", 0), stats.get("exit_cut_while_watched", 0)
        bits.append(f"last ask before leaving {money(stats['median_exit_price'])}"
                    + (f" · {cut} of {watched} cut in the days we saw them" if watched >= 12 else ""))
    return " · ".join(bits)


def build_today(events):
    """The day's changes, once: '## Today' lines for the report, and the
    fragments for an email subject that says what happened. Priority:
    shortlist alerts, then cuts (shopping and drivable first), then new
    cars, then departures."""
    sec, bits = [], []
    for e in events["gone"]:
        if str(e["vin"]).upper() in SHORTLIST:
            sec.append(f"- **Shortlist: GONE** — {e['label']} last seen "
                       f"{e['last_seen']} at {money(e['last_price'])} `{e['vin']}`")
            bits.append("shortlist car GONE")
    for e in events["cuts"]:
        x = e["x"]
        if str(x["vin"]).upper() in SHORTLIST:
            sec.append(f"- **Shortlist: ▼ {money(e['amount'])} cut** — {e['label']} "
                       f"now {money(x['price'])} ({place(x)}) `{x['vin']}`")
            bits.append(f"▼{money(e['amount'])} on your shortlist")
    cuts = sorted(events["cuts"],
                  key=lambda e: (-e["shopping"], -bool(e["x"]["local"]), -e["amount"]))
    for e in cuts[:3]:
        x = e["x"]
        sec.append(f"- ▼ {money(e['amount'])} cut · {e['label']} · now "
                   f"{money(x['price'])} · {place(x)}"
                   f"{' · drivable' if x['local'] else ''} `{x['vin']}`")
    if len(cuts) > 3:
        # Cuts, not "price changes": events["cuts"] takes only the downward
        # steps, so a line calling itself price changes counted none of the
        # increases the sections below go on to list — 53 cuts headlined here
        # against 64 price-change rows down the page, 28 of them upward, on
        # the day this was written. Either the word or the list had to give,
        # and the word was the one that was wrong: the three bullets above
        # this line are cuts, so the overflow from them is cuts.
        #
        # …and only the SHOPPED models get "### trim" sections. The watchlist's
        # other five models are one line each under "## Comparison", which
        # lists no cuts at all, so "listed in the sections below" was false for
        # 23 of the 50 it counted. The line now says how many of the overflow
        # the reader can actually go and find.
        rest = len(cuts) - 3
        below = sum(1 for e in cuts[3:] if e["shopping"])
        sec.append(f"- …and {rest} more cut{'s' if rest != 1 else ''} today"
                   + (", listed in the sections below" if below == rest
                      else f", {below} of them listed in the sections below" if below
                      else " on models the sections below do not cover"))
    if cuts and not bits:
        e = cuts[0]
        bits.append(f"▼{money(e['amount'])} cut on "
                    f"{'drivable ' if e['x']['local'] else ''}{e['label']}")
    news = [e for e in events["new"] if e["shopping"]]
    if news:
        best = max(news, key=lambda e: e["pct"] if e["pct"] is not None else -1)
        reach = sum(1 for e in news if reach_not_arrival(e["x"]))
        line = f"- {len(news)} new on the shopped models"
        if reach:
            line += (f" ({reach} listed {REACH_DAYS}+ days before the tracker saw "
                     f"{'it' if reach == 1 else 'them'} — reach, not arrival)")
        if best["pct"] and best["pct"] > 0:
            line += (f" · best {best['pct']:.0%} under typical "
                     f"({money(best['x']['price'])}, {place(best['x'])})")
        sec.append(line)
        bits.append(f"{len(news)} new")
    gones = [e for e in events["gone"] if e["shopping"]]
    if gones:
        sec.append(f"- {len(gones)} gone since the last fetch on the shopped models")
        bits.append(f"{len(gones)} gone")
    subject = (f"{APP} — " + " · ".join(bits[:3])) if bits else f"{APP} — quiet day · {TODAY}"
    if not sec:
        return [], subject
    return ["## Today", ""] + sec + [""], subject


def shortlist_section(live_by_vin, gone_by_vin, scored_by_vin):
    """The cars actually being decided on, first in the report. A live one
    shows its price and movement; a vanished one says so loudly, with the
    honest read on whether it sold or just fell out of the fetch window."""
    if not SHORTLIST:
        return []
    sec = ["## Shortlist", "",
           f"_The {len(SHORTLIST)} car{'s' if len(SHORTLIST) != 1 else ''} "
           "being decided on, watched by VIN._", ""]
    for vin, note in SHORTLIST.items():
        if vin in live_by_vin:
            x, label = live_by_vin[vin]
            obj = x
            bits = [f"**{money(x['price'])}**", label, str(x.get("year") or "")]
            if x.get("miles") is not None:
                bits.append(f"{x['miles']:,} mi")
            bits.append("drivable · no shipping" if x.get("local") else
                        (f"+ {money(x['ship'])} shipping" if x.get("ship")
                         else "shipping n/a"))
            bits.append(place(x))
            line = "- " + " · ".join(b for b in bits if b)
            tags = []
            series = x.get("series") or []
            swing = two_prices(series)
            if swing:
                tags.append(f"seen at {money(swing[0])} and {money(swing[1])}")
            elif (len(series) >= 2 and series[-1][0] == TODAY
                    and series[-1][1] < series[-2][1]):
                tags.append(f"▼ CUT {money(series[-2][1] - series[-1][1])} today")
            elif x.get("cuts"):
                tags.append(cut_tag(x["cuts"], x.get("delta") or 0))
            if x.get("days_listed") is not None:
                tags.append(f"on market {x['days_listed']}d")
            p = scored_by_vin.get(vin)
            if p and p.get("pick_stand") == "under":
                tags.append(f"{p['pick_pct']:.0%} under typical")
            tags += x.get("flags") or []
            if tags:
                line += f"\n  _{' · '.join(tags)}_"
        elif vin in gone_by_vin:
            g = gone_by_vin[vin]
            obj = g
            still = g.get("still_listed")
            verdict = {
                # not "sold or pulled": that names two of the four ways a
                # listing ends, and the other two look identical from outside
                "delisted": "**GONE — the listing ended**",
                "out of window": "missing today — beyond the day's fetch "
                                 "cut-off, probably still for sale",
                "not checked": "missing — not checked since it was last "
                               "seen, so nothing is known yet",
            }.get(g["likely"], "missing today")
            if still:
                verdict = (f"left the {g.get('trim_label') or 'watch'} — the same VIN is "
                           f"listed as {still['trim'] or 'another trim'}"
                           + (f" at {money(still['price'])}" if still["price"] else "")
                           + (", still certified" if still.get("cpo") else ", not certified"))
            line = (f"- {verdict} · last seen {g['last_seen']} at "
                    f"{money(g['last_price'])} · {g.get('trim_label') or ''} · "
                    f"{place(g)}")
        else:
            sec.append(f"- not seen yet by the tracker `{vin}`"
                       + (f" — {note}" if note else ""))
            continue
        if note:
            line += f"\n  note: {note}"
        if obj.get("url"):
            line += f"\n  [listing]({obj['url']})"
        line += f" `{vin}`"
        sec.append(line)
    sec.append("")
    return sec


def fmt_new(x, p=None):
    """One line for a car first seen this run — the time-sensitive block."""
    bits = [f"**{money(x['price'])}**", str(x.get("year") or "")]
    if x.get("miles") is not None:
        bits.append(f"{x['miles']:,} mi")
    bits.append("drivable · no shipping" if x.get("local") else
                (f"+ {money(x['ship'])} shipping" if x.get("ship") else "shipping n/a"))
    bits.append(place(x))
    dl = x.get("days_listed")
    if dl is not None and dl <= 7:
        bits.append(f"listed {dl}d ago")
    elif dl is not None:
        bits.append(f"on market {dl}d, new to the tracker"
                    + (" — reach, not arrival" if reach_not_arrival(x) else ""))
    else:
        bits.append("new to the tracker")
    line = "- " + " · ".join(b for b in bits if b)
    if p and p.get("pick_stand") == "under":
        line += f"\n  _{p['pick_pct']:.0%} under typical ({money(p['pick_under'])} less)_"
    if x.get("url"):
        line += f"\n  [listing]({x['url']})"
    line += f" `{x.get('vin', '')}`"
    return line


def picks_rule():
    bits = [f"under {(to_int(PICKS.get('max_miles')) or 50000):,} miles"]
    if PICKS.get("exclude_accidents", True):
        bits.append("no reported accidents")
    if PICKS.get("exclude_rental", True):
        bits.append("no rental or fleet history")
    cpm = to_float(PICKS.get("cents_per_mile"))
    if cpm is None:
        cpm = 0.30
    # The same sentence the page's picksRule() prints, interval clause
    # included: two surfaces stating two rules for one list is the drift the
    # cross-surface smoke check exists to catch.
    return (", ".join(bits) + "; ranked by how far under the typical price "
            "for its model — its own trim and model year when there are "
            f"enough of them — a car sits, allowing ${cpm:.2f} a mile. "
            "A car is under typical only when its value sits below the 95% "
            f"interval of its cohort's median: {COHORT_FLOOR} cars make a "
            "cohort, and nine are the fewest that can put a car outside it")


# --------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------
CALLS = 0
SPENT = {}             # target id -> calls this target actually spent today. The plan is
                       # an upper bound, not a bill: a query that comes back short stops
                       # its own pagination and skips its newest probe, so a thin market
                       # silently costs less than it was budgeted. Nobody has ever known
                       # by how much, which makes every "can we afford one more model?"
                       # a guess against a number the run already had and discarded.
PRICE_WINDOW = {}      # (target id, source) -> highest price its price.asc query returned today
MILES_WINDOW = {}      # (target id, source) -> highest mileage its miles.asc query returned today


def window_dim(t):
    """Which axis a target's fetch window lives on. A cheapest-N fetch is
    bounded in dollars; the CPO watches sort by miles.asc only, so their
    window is bounded in miles — judging their departures by a price
    cut-off would compare against a number that never gated anything."""
    return "price" if "price.asc" in (t.get("sorts") or []) else "miles"
EXHAUSTED = set()      # (target id, source): a query came back short, so it returned
                       # that scope's ENTIRE result set — no cheapest-N cut-off applies
FAILED_FETCHES = 0     # requests that still failed after the retry
FAILED_SCOPES = set()  # (target id, source): a query that still failed after its retry.
                       # fetch() returning None means "unknown", and delisted() has to be
                       # told so: without this, a scope that never answered is judged by
                       # whatever the OTHER scope returned, and every car only that query
                       # could see is published as a departure. A dead National query on
                       # bmw-i7-edrive50 turned 9 real departures into 93.
RAW_N = Counter()      # (target id, source) -> RAW records the API returned today, before
                       # normalize() dropped any. EXHAUSTED is set from this count, and the
                       # offline reconstruction used to re-derive it from KEPT rows instead
                       # — which is a different number for every filtered target (bmw-i5-cpo
                       # keeps 6 of 40) and made every one of its days read as exhaustive.
TOTALS = {}            # (target id, source) -> the API's own total result count,
                       # when the response envelope carries one — the honest
                       # denominator behind "N tracked"
ENVELOPE_WARNED = False
OVERLAP = {}           # target id -> today's States-vs-National audit, persisted so the
                       # decision it informs can be made on a week of runs instead of
                       # one: Actions logs expire, and a single day where the States
                       # query happened to add nothing is not evidence that it never does.
SOURCE_VINS = {}       # (target id, source) -> the VINs that source returned today.
                       # `rows` is keyed (target, vin) and shared across sources, so a
                       # car both queries return collapses into one row and the overlap
                       # becomes invisible the moment the fetch ends. It is measured
                       # here because it is the only place it still exists — and it is
                       # the number that decides whether the States query is worth its
                       # half of the call budget, or is re-buying cars National already
                       # brought back.


def source_overlap(rows):
    """What the States query bought today that National did not already bring.

    Half this run's calls go to asking the buyer's own eight states the same
    question the national query just asked. Whether that is worth paying for is
    an empirical question with one number behind it — how many cars only the
    States query saw — and nobody has ever measured it, because the two answers
    are merged into one VIN-keyed table the moment they arrive.

    So: per target, the size of each source's own catch, the overlap, and the
    part that matters — `states_only`, which is exactly what would be LOST by
    making a target national_only. A target whose National query came back
    short is marked exhausted: that query returned the entire national market,
    so its States half cannot be buying anything new by definition, and no
    amount of local coverage argument survives it.
    """
    out = {}
    for t in TARGETS.values():
        tid = t["id"]
        st = SOURCE_VINS.get((tid, "States"))
        nat = SOURCE_VINS.get((tid, "National"))
        if st is None or nat is None:
            continue          # national_only, or not due today: nothing to compare
        only_st = st - nat
        out[tid] = {
            "states": len(st), "national": len(nat),
            "both": len(st & nat), "states_only": len(only_st),
            "national_exhausted": (tid, "National") in EXHAUSTED,
            # The states that would go dark. A benchmark model losing Ohio
            # visibility is a different decision from one losing nothing.
            "states_only_in": sorted({r.get("state") for k, r in rows.items()
                                      if k[0] == tid and k[1] in only_st and r.get("state")}),
            "shopping": bool(t.get("shopping")),
            "calls_saved_per_due_day": len(sorts_pages(t)[0]) * sorts_pages(t)[1] + t["newest"],
        }
    return out


def report_source_overlap(overlap):
    """Print the audit as a table, and say what it implies rather than leaving
    the reader to divide the columns themselves."""
    if not overlap:
        return
    print("\nStates-vs-National overlap (what the second source actually bought):")
    print(f"  {'target':<24}{'States':>7}{'Natl':>6}{'both':>6}{'only States':>12}  note")
    free = 0.0
    for tid, o in sorted(overlap.items(), key=lambda kv: kv[1]["states_only"]):
        t = TARGETS[tid]
        note = ""
        if o["national_exhausted"]:
            note = "National saw the whole market — States is redundant"
        elif o["states_only"] == 0:
            note = "States added nothing today"
        elif o["states_only_in"]:
            note = "would lose " + "/".join(o["states_only_in"])
        if o["states_only"] == 0 and not o["shopping"]:
            free += o["calls_saved_per_due_day"] / t["cadence"]
        print(f"  {tid:<24}{o['states']:>7}{o['national']:>6}{o['both']:>6}{o['states_only']:>12}  {note}")
    if free:
        print(f"  -> {free:.1f} calls/day (~{free * 30.4:.0f}/month) sit behind benchmark targets whose "
              f"States query added nothing today. One day is not proof; watch it before flipping national_only.")


SPEND_LOG = Path("data/spend.json")


def spend_report(planned_today):
    """Planned against actual, and what the difference is worth.

    `planned_calls()` returns an upper bound: every due target billed for every
    page it is allowed. The fetch loop then spends less whenever a query comes
    back short — it stops paginating and skips that scope's newest probe — so
    the thin certified markets in particular cost a fraction of their budget.

    The gap is the real headroom, and it is the number every "can we afford one
    more model?" needs. It has never been recorded: the run prints CALLS and
    exits. So this returns today's row, and the log below keeps it.
    """
    actual = sum(SPENT.values())
    due = [t for t in TARGETS.values() if due_on(t, TODAY_ORD)]
    by_target = {}
    for t in due:
        tid = t["id"]
        want = calls_for(t)
        got = SPENT.get(tid, 0)
        if got != want:
            by_target[tid] = [want, got]
    # A target that was due and spent NOTHING did not save money — it did not
    # run. Counting that as headroom is how a budget gets spent twice: once on
    # the new model it appears to afford, and again when the broken target
    # starts working. So it is named separately and kept out of `banked`.
    silent = sorted(t["id"] for t in due if not SPENT.get(t["id"]))
    lost = sum(calls_for(t) for t in due if not SPENT.get(t["id"]))
    return {
        "planned": planned_today,
        "actual": actual,
        # Headroom is what the run declined to spend while working, not what it
        # failed to spend at all.
        "banked": planned_today - actual - lost,
        "unrun": lost,
        "silent_targets": silent,
        "targets_due": len(due),
        "exhausted": len(EXHAUSTED),
        "failed": FAILED_FETCHES,
        # Only the targets whose bill differed from their budget — a full table
        # would be mostly rows saying "as planned" every day forever.
        "off_plan": by_target,
    }


def report_spend(row, hist):
    """Say what was spent, and what the month looks like at this rate."""
    banked = row["banked"]
    print(f"\nSpend: {row['actual']} of {row['planned']} planned"
          + (f" · {banked} banked by {row['exhausted']} exhausted quer"
             f"{'y' if row['exhausted'] == 1 else 'ies'}" if banked > 0 else "")
          + (f" · {row['failed']} wasted on retries" if row["failed"] else ""))
    if row.get("silent_targets"):
        print(f"  ! {row['unrun']} calls' worth of targets were due and never ran — "
              f"NOT headroom: {', '.join(row['silent_targets'])}")
    for tid, (want, got) in sorted(row["off_plan"].items()):
        print(f"    {tid:<24} planned {want}, spent {got}")
    # Month to date, from the log itself rather than an average: a cadence cycle
    # is not a month and the two disagree by more than the headroom being hunted.
    month = TODAY[:7]
    days = {d: r for d, r in hist.items() if d.startswith(month)}
    if len(days) >= 2:
        spent = sum(r["actual"] for r in days.values())
        pace = spent / len(days)
        projected = pace * 30.4
        print(f"    month to date: {spent} over {len(days)} days ({pace:.1f}/day) "
              f"→ ~{projected:.0f}/month against a {MONTHLY:,} plan")
        # The first version of this printed max(0, MONTHLY - projected), which
        # reads "~0 unspent at this rate" whether the run is exactly on plan or
        # four hundred calls over it. A headroom meter that floors at zero is
        # silent in the only case worth printing, so overspend is now its own
        # sentence and says how far over. The plan is deliberately tight — 915
        # of 1,000 — and a retry bills twice, so this is a live number, not a
        # defensive one.
        if projected > MONTHLY:
            print(f"    ! on pace to OVERSPEND by ~{projected - MONTHLY:.0f} calls"
                  f" — {pace:.1f}/day sustains {MONTHLY / 30.4:.1f}/day")
        else:
            print(f"    ~{MONTHLY - projected:.0f} unspent at this rate")


def save_spend_history(row, path=SPEND_LOG, keep=400):
    """One row per day, newest kept. Small on purpose: this file exists to be
    read by a decision, not to be a second ledger.

    Today's row ACCUMULATES rather than replaces. SPENT is a per-process
    global, so a second run of the day starts from zero and the first version
    of this wrote `hist[TODAY] = row` straight over the first run's total —
    which made this log structurally blind to the one thing it was built to
    catch. On 2026-09-01 it would have recorded 24 calls on a day that spent
    72 across three runs. `runs` is what makes that visible at a glance.
    """
    try:
        hist = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        hist = {}
    if not isinstance(hist, dict):
        hist = {}
    prior = hist.get(TODAY) if isinstance(hist.get(TODAY), dict) else None
    if prior:
        row = dict(row)
        row["runs"] = (to_int(prior.get("runs")) or 1) + 1
        # actual, unrun and failed are per-run costs and add. `planned` is not
        # a cost — it is the DAY's plan, the same number every run of that day
        # computes — so adding it makes banked (planned - actual - unrun)
        # overstate headroom by (runs - 1) times the whole plan. Two runs of a
        # 32-call day reported planned 64, actual 30, banked 34: more headroom
        # than the day ever had, on the day it was overspent.
        for k in ("actual", "unrun", "failed"):
            row[k] = (to_int(prior.get(k)) or 0) + (to_int(row.get(k)) or 0)
        row["planned"] = max(to_int(prior.get("planned")) or 0,
                             to_int(row.get("planned")) or 0)
        # Headroom is a derived quantity; recompute it from the day's totals
        # rather than adding two runs' independently-computed versions.
        row["banked"] = row["planned"] - row["actual"] - row["unrun"]
    else:
        row = {**row, "runs": 1}
    hist[TODAY] = row
    for day in sorted(hist)[:-keep]:
        del hist[day]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(hist, indent=1, sort_keys=True) + "\n")
    except OSError as e:
        print(f"  ! could not write {path}: {e}")
    return hist


FETCH_LOG = Path("data/fetch_log.json")


def fetch_log_row():
    """What each query actually did today, per (target, source).

    delisted() judges a vanished car against the fetch window of the day it
    vanished. On the LIVE run that window is exact — PRICE_WINDOW / MILES_WINDOW
    / EXHAUSTED are the run's own bookkeeping. On any later day, and on every
    offline rebuild, it was re-derived from the snapshot rows instead, and the
    re-derivation is wrong in three separate ways that all push the same way,
    towards calling a car GONE:

      pooled sources  win_max was keyed (target, day) over the rows of BOTH
                      queries, so a car in a state the States query never asks
                      about was judged against the States cut-off — routinely
                      $5-8k higher than National's. Rebuilding the 2026-09-01
                      outputs flipped 24 departures from "out of window" to
                      "delisted" and took the report's headline from "9 gone
                      since the last fetch" to "31".
      kept vs raw     EXHAUSTED is set from the RAW page length, but the
                      reconstruction re-derived it as "fewer than 20 rows kept".
                      Every target filters after the fetch (cpo_only, max_miles,
                      trim_match, years, min_price), so bmw-i5-cpo keeping 6 of
                      40 raw records read as "this query saw the whole market"
                      and made every absence a confirmed departure — on a
                      single-sort target, which then publishes an exit price.
      failed scopes   a query that failed after its retry left no trace at all,
                      so the absence was judged against whatever the other
                      query returned.

    None of the three can be recovered from snapshots.csv, because the CSV keeps
    what was KEPT and says nothing about what was ASKED. So the run writes down
    what it did. One row per day, per target, per source: the window on the
    target's own axis, whether the query was exhaustive, whether it failed, and
    the raw record count behind those two. From the first day this exists, an
    offline rebuild reproduces the live labels exactly instead of approximating
    them; before it, delisted() falls back to what the rows can prove and says
    "not checked" for the rest.
    """
    row = {}
    for t in TARGETS.values():
        tid = t["id"]
        dim = window_dim(t)
        live = PRICE_WINDOW if dim == "price" else MILES_WINDOW
        for source_name, _ in sources_for(t):
            key = (tid, source_name)
            if key not in RAW_N and key not in FAILED_SCOPES:
                continue          # not due today, or never asked
            row.setdefault(tid, {})[source_name] = {
                "window": live.get(key),
                "dim": dim,
                "exhausted": key in EXHAUSTED,
                "failed": key in FAILED_SCOPES,
                "raw": RAW_N.get(key, 0),
            }
    return row


def save_fetch_log(row, path=None, keep=400):
    """Today's fetch facts, merged into the log. A second run of the same day
    MERGES rather than replaces: the two runs asked different questions and the
    union is what the day actually saw."""
    if not row:
        return {}
    # Resolved at CALL time, not bound as a default: a default argument freezes
    # the module global at import, so anything that repoints FETCH_LOG (a test,
    # a rebuild against another tree) would still read the committed file.
    path = Path(path) if path else FETCH_LOG
    try:
        hist = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        hist = {}
    if not isinstance(hist, dict):
        hist = {}
    day = hist.get(TODAY) if isinstance(hist.get(TODAY), dict) else {}
    for tid, sources in row.items():
        for src, fact in sources.items():
            prior = (day.get(tid) or {}).get(src)
            if isinstance(prior, dict):
                # the widest window either run reached, and exhaustion/failure
                # ORed: a scope that failed once and answered once did answer
                w = [v for v in (prior.get("window"), fact.get("window")) if v is not None]
                fact = {**fact,
                        "window": max(w) if w else None,
                        "exhausted": bool(prior.get("exhausted")) or fact["exhausted"],
                        "failed": bool(prior.get("failed")) and fact["failed"],
                        "raw": (to_int(prior.get("raw")) or 0) + fact["raw"]}
            day.setdefault(tid, {})[src] = fact
    hist[TODAY] = day
    for d in sorted(hist)[:-keep]:
        del hist[d]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(hist, indent=1, sort_keys=True) + "\n")
    except OSError as e:
        print(f"  ! could not write {path}: {e}")
    return hist


def load_fetch_log(path=None):
    path = Path(path) if path else FETCH_LOG
    try:
        log = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        return {}
    return log if isinstance(log, dict) else {}


OVERLAP_LOG = Path("data/source_overlap.json")


def save_overlap_history(overlap, path=OVERLAP_LOG, keep=120):
    """Append today's audit to a small dated log, newest days kept.

    Only the four numbers that answer the question are stored — a full record
    per target per day would grow faster than the ledger it sits beside for no
    extra answer. A rerun on the same day overwrites its own entry rather than
    doubling it, because the run is not idempotent about calls but this file
    must be about days.
    """
    if not overlap:
        return
    try:
        hist = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        hist = {}
    if not isinstance(hist, dict):
        hist = {}
    hist[TODAY] = {tid: [o["states"], o["national"], o["both"], o["states_only"]]
                   for tid, o in sorted(overlap.items())}
    for day in sorted(hist)[:-keep]:
        del hist[day]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(hist, indent=1, sort_keys=True) + "\n")
    except OSError as e:
        print(f"  ! could not write {path}: {e}")


def envelope_total(payload):
    """The total-result count from a listings response envelope, or None.
    The key is probed, not assumed — API envelopes rename these freely."""
    if not isinstance(payload, dict):
        return None
    for k in ("total", "totalCount", "count", "hitsCount", "totalResults"):
        n = to_int(payload.get(k))
        if n is not None:
            return n
    for parent in ("meta", "pagination"):
        sub = payload.get(parent)
        if isinstance(sub, dict):
            for k in ("total", "totalCount", "totalItems", "count", "totalResults"):
                n = to_int(sub.get(k))
                if n is not None:
                    return n
    return None


def year_param(years):
    ys = sorted(str(y) for y in years)
    if not ys:
        return None
    return ys[0] if len(ys) == 1 else f"{ys[0]}-{ys[-1]}"


def fetch(source_name, source, sort, page, t):
    """One API page, retried once on a transient failure. Returns the list of
    records, or None when the request failed even after the retry — callers
    must treat None as "unknown", never as "the market is empty"."""
    global CALLS, FAILED_FETCHES
    params = {
        "vehicle.make": t["make"],
        "vehicle.model": t["model"],
        "sort": sort,
        "limit": PER_PAGE,
        "page": page,
    }
    yp = year_param(t["years"])
    if yp:
        params["vehicle.year"] = yp
    if t.get("trim_query"):
        params["vehicle.trim"] = t["trim_query"]
    if source:
        params.update(source)
    for attempt in (1, 2):
        CALLS += 1
        # Counted per target as well as globally, and counted HERE so a retry
        # counts twice — because it costs twice. A ledger that recorded intent
        # rather than requests would understate a bad network day and hand back
        # headroom that was never there.
        SPENT[t["id"]] = SPENT.get(t["id"], 0) + 1
        err = None
        try:
            r = requests.get(BASE, headers=HEADERS, params=params, timeout=45)
        except requests.RequestException as e:
            err = str(e)
        else:
            if r.status_code == 200:
                global ENVELOPE_WARNED
                try:
                    payload = r.json() or {}
                except ValueError:
                    payload = None
                # An empty `data` list is a real answer: that query found
                # nothing. A response with NO data list is not an answer at all
                # — a maintenance page, an HTML error body, a renamed envelope —
                # and it used to become one: `[]`, which the loop reads as a
                # short page, which marks the scope EXHAUSTED, which tells
                # delisted() the query saw the whole market and every car it
                # did not return is gone. One bad deploy upstream would have
                # published the entire watchlist as sold. It is a failure, and
                # failures are retried and then recorded as unknown.
                batch = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(batch, list):
                    tot = envelope_total(payload)
                    if tot is not None:
                        key = (t["id"], source_name)
                        TOTALS[key] = max(TOTALS.get(key, 0), tot)
                    elif batch and not ENVELOPE_WARNED:
                        ENVELOPE_WARNED = True
                        print("  ! no total count found in the response envelope — "
                              f"keys were {sorted(payload)[:8]}")
                    if batch and not SAMPLE.exists():
                        SAMPLE.write_text(json.dumps(batch[0], indent=2))
                    RAW_N[(t["id"], source_name)] += len(batch)
                    return batch
                err = ("HTTP 200 with no `data` list in the envelope"
                       + (f" — keys were {sorted(payload)[:8]}" if isinstance(payload, dict)
                          else " — the body did not parse as JSON"))
            else:
                err = f"HTTP {r.status_code} {r.text[:200]}"
        print(f"  ! {t['id']} {source_name} {sort} p{page} (try {attempt}): {err}")
        if attempt == 1:
            time.sleep(2)
    FAILED_FETCHES += 1
    # The scope is now UNKNOWN, not empty. Recorded per (target, source) so
    # delisted() can refuse to judge an absence against a query that never ran.
    FAILED_SCOPES.add((t["id"], source_name))
    return None


def normalize(rec, t, dropped):
    global GEOCODED, UNPLACED
    # Match trims against the trim-bearing fields only, never the whole
    # record, so a short match like "rs" cannot hit unrelated text.
    hay = " ".join(str(first(rec, [p])) for p in
                   ("vehicle.trim", "vehicle.style", "vehicle.series",
                    "vehicle.model")).lower()
    tm = t.get("trim_match", "").lower()
    if tm and tm not in hay:
        dropped["trim mismatch"] += 1
        return None
    tx = t.get("trim_exclude", "").lower()
    if tx and tx in hay:
        dropped["trim excluded"] += 1
        return None
    year = str(first(rec, ["vehicle.year", "year"]))
    if t["years"] and year not in [str(y) for y in t["years"]]:
        dropped["year out of range"] += 1
        return None
    vin = first(rec, ["vehicle.vin", "vin"])
    price = to_int(first(rec, ["retailListing.price", "price"], None))
    if not vin or not price:
        dropped["no vin/price"] += 1
        return None
    if price < t.get("min_price", 0):
        dropped["below min_price"] += 1
        return None
    miles = to_int(first(rec, ["retailListing.miles", "retailListing.mileage",
                               "vehicle.mileage", "mileage", "miles"], None))
    # The CPO watch targets: both filters run here, after the fetch, because
    # they are guaranteed correct here — the API's filter surface for these
    # fields is unverified, and a silently-ignored query param would fetch
    # the wrong market while looking healthy. The miles.asc sort those
    # targets use makes the post-filter efficient: every page is spent on
    # the low-mileage end where the answer lives.
    if t.get("cpo_only") and not dig(rec, "retailListing.cpo"):
        dropped["not certified"] += 1
        return None
    mm = to_int(t.get("max_miles"))
    if mm is not None and (miles is None or miles >= mm):
        # unknown mileage cannot prove "under the cap", so it is out too
        dropped["at/over max_miles"] += 1
        return None
    loc = rec.get("location")
    lat = lon = None
    if isinstance(loc, list) and len(loc) == 2:
        lon, lat = to_float(loc[0]), to_float(loc[1])
    if not coords_ok(lat, lon):
        lat, lon = zip_coords(first(rec, ["retailListing.zip", "zip",
                                          "dealer.zip"]))
        if coords_ok(lat, lon):
            GEOCODED += 1
        else:
            UNPLACED += 1
            lat = lon = None
    distance = dist_home(lat, lon)
    return {
        "snapshot_date": TODAY,
        "target": t["id"],
        "vin": vin,
        "year": year,
        "trim": first(rec, ["vehicle.trim", "vehicle.style", "vehicle.series"]),
        "miles": miles if miles is not None else "",
        "price": price,
        "dealer": first(rec, ["retailListing.dealer",
                              "retailListing.dealerName", "dealer.name",
                              "retailListing.dealer.name", "dealerName",
                              "retailListing.sellerName", "seller.name"]),
        "city": first(rec, ["retailListing.city", "dealer.city",
                            "location.city"]),
        "state": str(first(rec, ["retailListing.state", "dealer.state",
                                 "location.state"])).strip().upper(),
        "listed_since": str(first(rec, ["createdAt",
                                        "retailListing.createdAt"]))[:10],
        "url": first(rec, ["retailListing.vdp", "retailListing.vdpUrl",
                           "retailListing.url", "url", "retailListing.link",
                           "vdpUrl", "detailUrl"]),
        "msrp": int_or_blank(first(rec, ["vehicle.baseMsrp", "vehicle.msrp"],
                                   None)),
        "color": first(rec, ["vehicle.exteriorColor", "vehicle.color"]),
        "cpo": "1" if dig(rec, "retailListing.cpo") else "",
        "owners": int_or_blank(dig(rec, "history.ownerCount")),
        "accidents": int_or_blank(dig(rec, "history.accidentCount")),
        "usage": first(rec, ["history.usageType"]),
        "image": first(rec, ["retailListing.primaryImage"]),
        "carfax": first(rec, ["retailListing.carfaxUrl"]),
        "lat": "" if lat is None else round(lat, 5),
        "lon": "" if lon is None else round(lon, 5),
        "distance": distance if distance is not None else "",
        # Filled in by the fetch loop once every query that returned this row
        # is known — a row can arrive from several, and normalize sees one at
        # a time. Initialised here so a normalized row always carries the full
        # column set.
        "via": "",
    }


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------
def load_history():
    if not SNAPSHOTS.exists():
        return []
    rows = []
    # utf-8-sig, not utf-8. A UTF-8 BOM — which is what a round trip through
    # Excel leaves behind — turns the first header into "\ufeffsnapshot_date",
    # so every row reads snapshot_date as "". That silently defeats the
    # already-fetched guard (TODAY is in no row) and then write_rows rewrites
    # the whole file with 3,581 blank dates. utf-8-sig strips a BOM when there
    # is one and is identical to utf-8 when there is not.
    with SNAPSHOTS.open(newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            row = {k: r.get(k, "") or "" for k in FIELDS}
            row["target"] = LEGACY_IDS.get(row["target"], row["target"])
            row["state"] = row["state"].strip().upper()
            # distance means miles from the buyer's home; recompute it from
            # the stored coordinates so every row carries the same meaning
            d = dist_home(to_float(row["lat"]), to_float(row["lon"]))
            row["distance"] = d if d is not None else ""
            rows.append(row)
    return rows


def write_rows(rows):
    """Rewrite the whole file: a same-day re-run replaces today's rows
    instead of appending a second copy, and new columns get a header."""
    tmp = SNAPSHOTS.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore",
                           restval="")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(SNAPSHOTS)


def build_history(all_rows):
    per_day = defaultdict(dict)
    for r in all_rows:
        p = to_int(r["price"])
        if p is None:
            continue
        key = (r["target"], r["vin"])
        d = r["snapshot_date"]
        cur = per_day[key].get(d)
        per_day[key][d] = p if cur is None else min(cur, p)
    return {k: sorted(v.items()) for k, v in per_day.items()}


def build_local_history(all_rows):
    """When a car's DRIVABLE answer changed, for the few cars where it did.

    in_scope() reads the state field on the row, and a car's state field is not
    a constant: a listing can be moved between a dealer group's lots, or
    re-listed by a different store. Nine VINs in this record have changed state
    and three have crossed the buyer's own border doing it — WBY33FK09SCT64650
    was in Indiana on 2026-09-01 and is in Missouri now.

    The dashboard rebuilds a day's drivable count from the cars themselves
    whenever a filter is on, and the only state it holds per car is TODAY's, so
    it counted that i5 as beyond the border on a day when it was an hour's
    drive away. Python's daily_stats had it right — it reads the row as it was
    on the day — and the two series are supposed to be one definition in two
    languages, so the page needs the same fact.

    Emitted only at CHANGE points, and only for a car that ever changed: three
    of 1,209 VINs, about 150 bytes on an 876KB file. A car that never moved
    says nothing and the page falls back to the flag it already has.
    """
    per_day = defaultdict(dict)
    for r in all_rows:
        per_day[(r["target"], r["vin"])][r["snapshot_date"]] = 1 if in_scope(r) else 0
    out = {}
    for key, days in per_day.items():
        run, prev = [], None
        for d in sorted(days):
            if days[d] != prev:
                run.append([d, days[d]])
                prev = days[d]
        if len(run) > 1:
            out[key] = run
    return out


LOCAL_HISTORY = {}      # (target, vin) -> [[date, 0|1], ...] at each change


def summarize(key, hist):
    return summarize_series(hist.get(key, []), LOCAL_HISTORY.get(key))


def summarize_series(series, moved=None):
    if not series:
        return {"series": []}
    prices = [p for _, p in series]
    out = {
        "series": series,
        "cuts": sum(1 for a, b in zip(prices, prices[1:]) if b < a),
        "delta": prices[-1] - prices[0],
        "days_tracked": len(series),
        "first_seen": series[0][0],
    }
    if moved:
        out["local_hist"] = moved
    return out


def summarize_vin(vin, tids, hist, key):
    """One car's history across every target that returned it.

    The listings table is one row per VIN — pick_display_rows keeps the
    cheapest copy — but the record is keyed (target, vin), and summarize()
    read only the chosen copy's key. A certified eDrive40 the ordinary target
    had watched for ten days and the nationwide CPO watch first returned
    yesterday was therefore filed under the CPO copy — a dollar cheaper, or
    merely the copy that won the tie — with days_tracked 1, first_seen
    yesterday, no cuts and no delta, and "New today" announced a car the page
    had listed for a week and a half. Four VINs sat under two targets on
    2026-09-01 alone.

    The series is merged by day, taking the day's lowest price across the
    copies — the rule build_history already applies within one key — and
    first_seen, days_tracked, cuts and delta are derived from the merged
    series, so they describe the car. Everything else on the row — price,
    url, dealer, cpo, trim_id — still comes from the single chosen copy: a
    fact is never paired with another copy's. local_hist stays the chosen
    copy's own, because a listing's state is a fact about that listing.
    """
    per_day = {}
    for tid in tids:
        for d, p in hist.get((tid, vin), []):
            cur = per_day.get(d)
            per_day[d] = p if cur is None else min(cur, p)
    return summarize_series(sorted(per_day.items()), LOCAL_HISTORY.get(key))


# --------------------------------------------------------------------------
# Row facts
# --------------------------------------------------------------------------
def is_cpo(r):
    return str(r.get("cpo", "")).lower() in ("1", "true", "y")


def seller_named(r, brand):
    """Does the seller's own name carry the brand word?

    Manufacturer certification is issued by the manufacturer's own stores, so
    a car flagged certified at a dealership whose name says Acura or Nissan is
    a claim worth confirming before it is counted on — and on this record 21 of
    46 certified cars are exactly that, including the cheapest car in the
    certified watch, at "niello acura". The dashboard has said so since the
    promo strip was built; the report printed a bare CPO.

    Whole word, not substring, and the brand KEY rather than its label — the
    same test docs/index.html's sellerNamed() makes with /\bbmw\b/i, so the two
    surfaces cannot disagree about which sellers count. The word set below is
    that regex without importing one: every run of non-alphanumeric characters
    is a separator, which is where \b falls for these names — "bmw of
    chicago" and "bmw-of-chicago" match, "bmwofchicago" and "3bmw" do not,
    exactly as the page has them. An empty dealer name fails it, which is the
    conservative direction: a certification nobody is named for is exactly the
    one to confirm.
    """
    b = str(brand or "").strip().lower()
    name = str(r.get("dealer") or "")
    words = set("".join(c if c.isalnum() else " " for c in name).lower().split())
    return bool(b) and b in words


def flags(r):
    out = []
    if is_cpo(r):
        t = TARGETS.get(r.get("target")) or {}
        brand = t.get("brand")
        out.append("CPO" if not brand or seller_named(r, brand)
                   else f"CPO (seller not named {t.get('brand_label') or str(brand).upper()})")
    # Right after the certified chip, which is the slot flagsCell() uses on the
    # page — the report and the dashboard describe the same car from the same
    # list, and two surfaces that order it differently are two surfaces that
    # can be read as disagreeing. The page has marked rentals and fleet cars
    # since the filter was written; flags() said nothing, so a car the buyer's
    # own picks rule excludes read as clean in the one artefact that is
    # committed to the repository.
    u = str(r.get("usage", "")).lower()
    if is_rental(r):
        out.append("rental" if "rental" in u
                   else "multi-use" if "multiple" in u else "fleet")
    owners = to_int(r.get("owners"))
    if owners == 1:
        out.append("1-owner")
    elif owners and owners > 1:
        out.append(f"{owners} owners")
    acc = to_int(r.get("accidents"))
    if acc is not None:
        out.append("no accidents" if acc == 0
                   else f"{acc} accident{'s' if acc > 1 else ''}")
    if "lease" in u:
        out.append("ex-lease")
    return out


INDEX_DATES = set()     # listed_since values that are a bulk index load, not a listing date


def find_index_dates(all_rows, floor=20, ratio=10):
    """The listed_since values that are the API's own index date, not a date
    any car was listed on.

    listed_since comes from the API's `createdAt`, and createdAt is when the
    RECORD was created, which for a bulk load is the same instant for tens of
    thousands of cars. On this sheet that day is 2026-08-09: 106 of the 321
    rows of 2026-09-01 carry it, spread over 8 targets, 25 states and 85
    different dealers, while 2026-08-08 carries one row and 2026-08-10 none.
    Eighty-five dealers do not list on the same Tuesday and then stop.

    What it did to the published numbers is not subtle. median_days_listed came
    out at exactly (snapshot date - 2026-08-09) for six of the seven models,
    every day, incrementing 14, 15, 16 … 23 — the signature of a constant, not
    of a market. stale_pct is the same field's percentile, so every one of the
    118 cars marked "sits longer than 60% of the model" was simply indexed
    before the load, and how.html tells the reader that past 30 days "usually
    means a dealer who will negotiate".

    The test is deliberately a shape, not a date: a real listing date is a
    per-car event, so it cannot outnumber both its neighbours by an order of
    magnitude. Counted once per VIN, because a car sits in the sheet on every
    day it was seen and would otherwise vote ten times.
    """
    seen, by_date = set(), Counter()
    for r in all_rows:
        d = str(r.get("listed_since") or "")[:10]
        key = (r.get("target"), r.get("vin"))
        if not d or key in seen:
            continue
        seen.add(key)
        try:
            date.fromisoformat(d)
        except ValueError:
            continue
        by_date[d] += 1
    out = set()
    for d, n in by_date.items():
        if n < floor:
            continue
        here = date.fromisoformat(d)
        near = max((by_date.get(date.fromordinal(here.toordinal() + k).isoformat(), 0)
                    for k in (-2, -1, 1, 2)), default=0)
        if n >= ratio * max(near, 1):
            out.add(d)
    return out


def days_listed(r):
    since_raw = str(r.get("listed_since", ""))[:10]
    # A bulk index date is not a listing date, and a number built on one is a
    # fact about the API's loader. Nothing rather than something shaky.
    if since_raw in INDEX_DATES:
        return None
    try:
        since = date.fromisoformat(since_raw)
    except ValueError:
        return None
    # From the day this row was OBSERVED, not from the day the file is built.
    # Every dispatch rebuilds (see the report footer), and a rebuild run a week
    # later measured every listing against that week: the i5's median days on
    # market moved 23 -> 30 and a car's "21d listed" became "28d listed" over
    # identical rows, while `data through` correctly stayed put. The same drift
    # walked stale_pct and the ">= 30d on market" tag with it. On a live fetch
    # snapshot_date IS today, so nothing moves; on a slow-cadence trim it now
    # agrees with the "as of" its own model block already prints.
    as_of = str(r.get("snapshot_date") or TODAY)[:10]
    try:
        return (date.fromisoformat(as_of) - since).days
    except ValueError:
        return (date.fromisoformat(TODAY) - since).days


def seen_label(s):
    """'seen 21 of 42 days', never 'tracked 21d'.

    days_tracked is the length of the price series, and a series grows only on
    days the car's target was fetched — every second day for half the targets
    — so "tracked 21d" read as three weeks on a car that had been listed for
    six. The count is kept, because a slower cadence must not be able to
    inflate it, and the label says what it counts: sightings, over the span
    from the first to the last. The dashboard's seenLabel() is the same rule.
    """
    n = s.get("days_tracked", 0) or 0
    series = s.get("series") or []
    if n == 1:
        return "seen once"
    if len(series) < 2:
        return f"seen {n} days"
    try:
        span = (date.fromisoformat(str(series[-1][0])[:10])
                - date.fromisoformat(str(series[0][0])[:10])).days + 1
    except (TypeError, ValueError):
        return f"seen {n} days"
    return f"seen {n} of {max(span, n)} days"


REACH_DAYS = 14


def reach_not_arrival(x):
    """A car first seen today that carries a listing date a fortnight or more
    earlier was on the market all along and only now entered a fetch window:
    the API serves forty cars a query, sorted, and a car a rank outside the
    window is invisible until something ahead of it leaves. Reach, not
    arrival — the count is printed beside "new" so nine new i7s of which
    seven were listed in March, April and June do not read as nine arrivals.
    docs/index.html carries the same fourteen days as REACH_DAYS."""
    ls, fs = x.get("listed_since"), x.get("first_seen")
    if not ls or not fs:
        return False
    try:
        return (date.fromisoformat(str(fs)[:10]) - date.fromisoformat(str(ls)[:10])).days >= REACH_DAYS
    except ValueError:
        return False


def is_new_today(x):
    """First seen on THIS snapshot, not merely seen once.

    days_tracked is the length of a car's price series, and a series only grows
    on days its target was fetched — so a car seen once on Monday still reads
    days_tracked == 1 on Thursday, and on any Thursday when some other trim of
    its model was due, the whole "New today" block re-announced it as "first
    seen this run". Three cars first seen on 2026-09-01 were headlined as new
    on a quiet 09-04 in exactly that way.

    first_seen is the day of the first sighting, which is the thing the words
    actually claim. Where the record has no first_seen at all, fall back to the
    old test rather than announce nothing.
    """
    first = x.get("first_seen")
    if first:
        return str(first)[:10] == TODAY
    return x.get("days_tracked") == 1


def pick_display_rows(rows):
    """One row per VIN — the cheapest copy if a VIN was seen more than once."""
    by_vin = defaultdict(list)
    for r in rows:
        by_vin[r["vin"]].append(r)
    return [dict(min(rs, key=lambda r: to_int(r["price"]) or 10**9))
            for rs in by_vin.values()]


def fmt_row(r, s, entry=None):
    miles = to_int(r["miles"])
    adj, ship = landed(r)
    bits = [f"**{money(to_int(r['price']))}**"]
    if miles is not None:
        bits.append(f"{miles:,} mi")
    if ship:
        bits.append(f"+ {money(ship)} shipping"
                    + (f" = {money(adj)}" if adj is not None else ""))
    else:
        bits.append("no shipping")
    where = f"{r['year']} · {r['city']}, {r['state']}"
    d = row_distance(r)
    if d is not None:
        where += f" · ~{d:,} mi from {HOME_NAME}"
    bits.append(where)
    out = "- " + " · ".join(str(b) for b in bits)
    tags = []
    swing = two_prices(s.get("series"))
    if swing:
        tags.append(f"seen at {money(swing[0])} and {money(swing[1])}")
    elif s.get("cuts"):
        tags.append(cut_tag(s["cuts"], s.get("delta") or 0))
    if s.get("days_tracked", 0) >= 21:
        tags.append(seen_label(s))
    if is_new_today(s):
        tags.append("NEW")
    dl = days_listed(r)
    if dl is not None and dl >= 30:
        tags.append(f"on market {dl}d")
    # negotiation context: a car most of its own model has outsold is a car
    # whose dealer has a reason to talk
    if entry and (entry.get("stale_pct") or 0) >= 0.75:
        tags.append(f"sits longer than {entry['stale_pct']:.0%} of the "
                    + (f"model's {entry['stale_of']} dated cars"
                       if entry.get("stale_of") else "model"))
    tags += flags(r)
    if tags:
        out += f"\n  _{' · '.join(tags)}_"
    if r.get("dealer"):
        out += f"\n  {r['dealer']}"
    if r.get("url"):
        out += f" — [listing]({r['url']})"
    out += f"\n  `{r['vin']}`"
    return out


# --------------------------------------------------------------------------
# Aggregates
# --------------------------------------------------------------------------
def daily_stats(rows, days=None):
    """One row per snapshot day, over the cars the tracker KNEW about that day.

    Not the cars it fetched that day, which is what this used to count and is a
    different series entirely. Trims of one model run on their own cadences —
    the i5's eDrive40 daily, its xDrive40 and M60 every second day — so on half
    the days only one target reported and the model's row halved with it:
    127, 119, 71, 130, 73, 140, 79, 136, 80, 137. The chart above the table
    draws that as "lowest and median asking price each day among the cars in
    view", and the median duly swung $5,371 every other day out of pure
    bookkeeping, on a page whose own listings table showed 137 cars throughout.

    So each target is carried forward to its own most recent fetch, which is
    exactly the rule current_rows() already applies to today and the rule the
    page's "data through" line already describes. A day now holds what the
    record actually knew on it.
    """
    by_target_day = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_target_day[r["target"]][r["snapshot_date"]].append(r)
    # `days` is the MODEL's day list, so a trim's own series is reported on the
    # same days as the model it belongs to — otherwise the model row carries a
    # slow trim forward while the trim's own row simply is not there, and the
    # per-trim rows stop covering the model row they are supposed to decompose.
    # A day before this target's first fetch holds nothing and is skipped
    # rather than published as a market of zero cars.
    days = sorted(set(days) if days else {r["snapshot_date"] for r in rows})
    by_day = {}
    for d in days:
        held = []
        for tid, per_day in by_target_day.items():
            seen = [x for x in per_day if x <= d]
            if seen:
                held += per_day[max(seen)]
        if held:
            by_day[d] = held
    out = []
    for d in sorted(by_day):
        display = pick_display_rows(by_day[d])
        prices = [p for p in (to_int(r["price"]) for r in display) if p]
        out.append({
            "date": d,
            "n": len(display),
            "n_local": sum(1 for r in display if in_scope(r)),
            "min_price": min(prices) if prices else None,
            "median_price": int(median(prices)) if prices else None,
        })
    return out


def delisted(tids, all_rows, today_rows, hist):
    """Vehicles seen before but not today. Because each query only returns
    the cheapest N, a car priced above the fetch window for its trim may
    simply have been pushed out rather than sold — that is flagged, not
    hidden. When a query came back short it returned its scope's whole
    market, so absence there is a real delisting whatever the price.

    Each departure is judged at the day it VANISHED — the trim's first fetch
    day after the car was last seen — against that day's window, never
    today's. The snapshot CSV keeps every kept row of every fetch day, so
    that window is reconstructed from history: the day's max kept value on
    the target's own window axis (price for a cheapest-N fetch, MILES for
    the miles-sorted CPO watches — see window_dim) IS that day's cut-off,
    and a day with fewer kept rows than one page returned its scope's
    entire market, so no cut-off applies. When the vanish day is today's
    live fetch, the run's own per-source window (PRICE_WINDOW /
    MILES_WINDOW / EXHAUSTED) is used instead — it is exact.

    One reading note for the CPO watches: their market is "certified cars
    under the mileage cap", so a car that merely loses its CPO badge or
    rolls past the cap departs THAT market for real, even though it may
    still be listed — 'delisted' means gone from the tracked market, which
    is the market the promo financing applies to.

    That reading is right and the WORDS built on it were wrong. "GONE — the
    listing ended" printed over WBY13HG00SCU51722 on 2026-09-05 while the
    same VIN sat live in the listings table of the same page as an xDrive40:
    it left the certified watch, not the market.

    What it is NOT is one listing that changed queries. The snapshot CSV has
    both records on the same day: on 2026-09-03 the CPO query returned
    $54,476 certified and the xDrive40 query $50,986, and on T19677 the two
    even carry different dealer strings ("BMW of South Miami" against "BMW of
    Miami South and Mini of Miami South"). Two records of one car, and one of
    them stopped coming back. So the row names the other record as a separate
    thing — its trim, its price today, and whether it is certified — and
    never implies the departed listing was cut to it. The certification is
    the point, not a detail: the 2.99% promo is cpo_only, so a VIN that
    survives uncertified is a car whose payment just moved from $925 to
    $1,017 a month, and "still listed" alone would report that as good news.

    Only against a record seen on the model's own latest fetch day: eleven of
    fourteen targets run a slower cadence than their siblings, so "live on
    the sheet" can mean a sighting as old as the departure itself.

    The row keeps `likely` — leaving the certified pool is real news, and the
    label is what makes it findable. What it must not do is feed a count or a
    price that means "left the market", so departure_is_evidence() refuses it
    and the counts follow.

    Each entry carries its own trim's previous fetch day, because trims of
    one model can run on different cadences and the model's yesterday is
    not every trim's."""
    today_vins = {(r["target"], r["vin"]) for r in today_rows}
    # …and the same VINs without their target, so a departure from ONE query
    # can be told from a departure from the market: the model's own live rows
    # are the whole of what "still listed" can mean here, since a VIN decodes
    # to one model and cannot surface under another.
    live_day = max((r["snapshot_date"] for r in today_rows), default=None)
    live_by_vin = {}
    for r in today_rows:
        if r["snapshot_date"] == live_day:
            live_by_vin.setdefault(r["vin"], r)
    days_by_tid = defaultdict(set)
    log = load_fetch_log()
    # win_max is the POOLED window: the widest value either query kept that day,
    # which is exactly max(States cut-off, National cut-off). For a car in a
    # queried state that is the right test — it comes back through either query,
    # so it is only out of window when it is above both.
    #
    # nat_min is the National query's own window, bounded from BELOW: a kept row
    # whose state is outside SEARCH_STATES can only have come back through the
    # National query, so National's window provably reaches at least that far.
    # For a car outside the queried states that lower bound is the only honest
    # test there is — judging it against the pooled maximum tests it against the
    # States cut-off, which never had a chance of returning it.
    win_max, win_n, nat_min = {}, Counter(), {}
    for r in all_rows:
        if r["target"] in tids:
            d = r["snapshot_date"]
            days_by_tid[r["target"]].add(d)
            win_n[(r["target"], d)] += 1
            dim = window_dim(TARGETS[r["target"]])
            p = to_int(r["price"] if dim == "price" else r["miles"])
            if p is None:
                continue
            if p > win_max.get((r["target"], d), 0):
                win_max[(r["target"], d)] = p
            if str(r.get("state", "")).strip().upper() not in SEARCH_STATES:
                if p > nat_min.get((r["target"], d), 0):
                    nat_min[(r["target"], d)] = p
    by_key = defaultdict(list)
    for r in all_rows:
        if r["target"] in tids and (r["target"], r["vin"]) not in today_vins:
            by_key[(r["target"], r["vin"])].append(r)
    horizon = date.fromordinal(TODAY_ORD - 60).isoformat()
    out = []
    for (tid, vin), rows in by_key.items():
        last_day = max(r["snapshot_date"] for r in rows)
        if last_day < horizon:
            continue    # keep the gone list (and data.json) from growing forever
        r = pick_display_rows([x for x in rows
                               if x["snapshot_date"] == last_day])[0]
        s = summarize((tid, vin), hist)
        adj, ship = landed(r)
        t = TARGETS[tid]
        last_price = to_int(r["price"])
        tdays = sorted(days_by_tid[tid])
        prev_fetch = tdays[-2] if len(tdays) >= 2 else None
        # the day the car disappeared: its trim's first fetch after last_seen
        van_day = next((d for d in tdays if d > last_day), None)
        # a car in a queried state comes back through either query, so it is
        # only out of window when it is above both cut-offs
        keys = (["States", "National"] if r["state"] in SEARCH_STATES
                else ["National"])
        # judge the absence on the axis the target's window actually lives on
        dim = window_dim(t)
        live_win = PRICE_WINDOW if dim == "price" else MILES_WINDOW
        last_val = last_price if dim == "price" else to_int(r["miles"])
        # `unknown` means the day's queries cannot answer for this car at all:
        # one of them failed, or nothing on record says how far it reached. It is
        # NOT the same as "above the cut-off", and it must never read as a
        # departure — see fetch_log_row() for the three ways the reconstruction
        # used to turn silence into a confirmed sale.
        unknown = False
        # How the label below was reached, carried out with it. A "delisted"
        # from the live run or from the fetch log is a query that looked and
        # did not find the car; a "delisted" reconstructed for a two-window
        # target from rows alone is a guess the file's own exit_stats() refuses
        # to price. Both used to read the same downstream, so the market line
        # counted the second kind as cars that left. Recorded here rather than
        # re-derived later, because this is the only place that knows.
        exact = False
        logged = (log.get(van_day) or {}).get(tid) if van_day else None
        if van_day == TODAY and any((tid, k) in live_win
                                    or (tid, k) in EXHAUSTED
                                    or (tid, k) in FAILED_SCOPES for k in keys):
            # live run, vanished at today's fetch: use its exact window
            cutoffs = [c for c in (live_win.get((tid, k)) for k in keys)
                       if c is not None]
            cutoff = max(cutoffs) if cutoffs else None
            exhausted = any((tid, k) in EXHAUSTED for k in keys)
            # A scope that failed might have been the one that would have
            # returned this car, so its absence proves nothing — UNLESS a
            # scope that did answer already reached past the car's own value,
            # in which case that query looked and did not find it, and the
            # other one failing changes nothing about what this one saw.
            unknown = (any((tid, k) in FAILED_SCOPES for k in keys)
                       and not (cutoff is not None and last_val is not None
                                and last_val <= cutoff))
            exact = True
        elif logged:
            # the run wrote down what each query did that day: reproduce it
            # exactly, so a rebuild cannot disagree with the run it rebuilds
            facts = [logged.get(k) for k in keys]
            unknown = any(f is None or f.get("failed") for f in facts)
            cutoffs = [f["window"] for f in facts
                       if f and f.get("window") is not None]
            cutoff = max(cutoffs) if cutoffs else None
            exhausted = any(f.get("exhausted") for f in facts if f)
            exact = True
        else:
            # An older day with no fetch log: reconstruct what the ROWS can
            # prove, and nothing more. Exhaustion is not among it — EXHAUSTED
            # is set from the RAW page length and the CSV only holds the rows
            # that survived the filters — so it is never inferred here.
            exhausted = False
            # True only for a target whose one window the rows can rebuild. On
            # a two-window target this branch cannot currently reach a
            # "delisted" at all — above the pooled maximum is "out of window"
            # and at or below it is "not checked" — so the False case is a
            # guard rather than a live path, and
            # test_the_offline_path_never_calls_a_two_window_absence_a_delisting
            # is what says so out loud and will fail the day that changes.
            exact = window_reconstructable(t)
            pooled = win_max.get((tid, van_day))
            if not window_reconstructable(t):
                # A second sort or a newest probe put rows above the window
                # into the same day. The one thing the pooled maximum still
                # proves is its own direction: it is at least as wide as every
                # window, so a car above IT was above all of them. Anything at
                # or below it is a question the record cannot answer.
                cutoff = pooled
                if not (last_val is not None and pooled is not None
                        and last_val > pooled):
                    unknown = True
            elif "States" in keys:
                # reachable through either query: the pooled maximum IS
                # max(States cut-off, National cut-off), which is the test
                cutoff = pooled
            else:
                # National only, and the queried states are the whole of what
                # the States query can return — so a kept row from outside them
                # came back through National, and National's window provably
                # reaches at least that far. Above the pooled maximum the car
                # was outside every window. Between the two the record cannot
                # say, and neither may this.
                cutoff = nat_min.get((tid, van_day))
                if (cutoff is None or last_val is None
                        or (last_val > cutoff
                            and (pooled is None or last_val <= pooled))):
                    unknown = True
        if van_day is None:
            likely = "not checked"      # not fetched again since last seen
        elif unknown:
            likely = "not checked"      # the day's queries cannot answer for it
        elif exhausted:
            likely = "delisted"         # a query that saw everything missed it
        elif cutoff is None or last_val is None:
            likely = "not checked"      # no window to judge the absence by
        elif last_val > cutoff:
            likely = "out of window"    # beyond that day's fetch cut-off (price or miles)
        else:
            likely = "delisted"
        live = live_by_vin.get(vin)
        # The other record of this car, as its own record: the trim it is
        # listed under, what THAT listing asks, and whether it is certified —
        # never a continuation of the departed one. Emitted only when true,
        # which is two rows of 254 on this record: a key repeated 252 times
        # to say "no" is 5KB of sheet for nothing.
        still = ({"trim_id": live["target"], "trim": (live.get("trim") or "").strip(),
                  "price": to_int(live.get("price")),
                  "cpo": bool(to_int(live.get("cpo")))} if live else None)
        out.append({
            "likely": likely,
            **({"still_listed": still} if still else {}),
            # …and whether that label came from a query that actually looked.
            # See `exact` above; departure_is_evidence() is its one consumer.
            "exact": bool(exact) and likely != "not checked",
            "vin": vin, "year": to_int(r["year"]), "trim": r["trim"],
            "trim_id": tid, "trim_label": t["label"],
            "state": r["state"], "local": in_scope(r),
            "distance": row_distance(r), "ship": ship,
            "miles": to_int(r["miles"]),
            "last_price": last_price,
            "city": r["city"], "dealer": r["dealer"],
            "url": r["url"], "last_seen": last_day,
            "listed_since": ("" if str(r["listed_since"])[:10] in INDEX_DATES
                             else r["listed_since"]),
            "prev_fetch_day": prev_fetch,
            "first_seen": s.get("first_seen"),
            "days_tracked": s.get("days_tracked", 0),
            "cuts": s.get("cuts", 0), "delta": s.get("delta", 0),
            # A departed car is still part of every day it was on the market.
            # The dashboard rebuilds the price history for whatever scope the
            # reader has selected, and without these it would rebuild it from
            # survivors only — the cheap car that sold on Tuesday would vanish
            # from Monday too, and the floor would look like it fell when it
            # was simply bought. accidents and usage ride along so the clean
            # and no-rental filters judge a departed car by the same rule as
            # a live one.
            "accidents": to_int(r.get("accidents")),
            "usage": r.get("usage", ""),
            "cpo": is_cpo(r),
            "series": s.get("series", []),
            **({"local_hist": s["local_hist"]} if s.get("local_hist") else {}),
            "flags": flags(r),
        })
    out.sort(key=lambda x: (x["last_seen"], -(x["last_price"] or 0)),
             reverse=True)
    return out


def listing_entry(r, s):
    t = TARGETS[r["target"]]
    # landed() also returns the mileage-adjusted value, and it is NOT exported:
    # nothing on the page reads it, it cost 19KB of data.json, and it is the
    # one number here that includes buyer.cents_per_mile while the page's own
    # landed() is price plus shipping — a copy that would silently diverge from
    # every figure on screen the day that knob is turned on. The reader only
    # ever sees asking.
    _, ship = landed(r)
    return {
        "vin": r["vin"], "year": to_int(r["year"]), "trim": r["trim"],
        "trim_id": t["id"], "trim_label": t["label"],
        "state": r["state"], "local": in_scope(r),
        # the car's own public location (already in the committed CSV),
        # so the dashboard can put every listing on the map
        "lat": to_float(r.get("lat")), "lon": to_float(r.get("lon")),
        "distance": row_distance(r), "ship": ship,
        "miles": to_int(r["miles"]), "price": to_int(r["price"]),
        "msrp": to_int(r.get("msrp")),
        "dealer": r["dealer"], "city": r["city"],
        "url": r["url"], "image": r.get("image", ""),
        "carfax": r.get("carfax", ""), "color": r.get("color", ""),
        "cpo": is_cpo(r), "owners": to_int(r.get("owners")),
        "accidents": to_int(r.get("accidents")),
        "usage": r.get("usage", ""), "flags": flags(r),
        # withheld, not blanked by accident: see find_index_dates()
        "listed_since": ("" if str(r["listed_since"])[:10] in INDEX_DATES
                         else r["listed_since"]),
        "days_listed": days_listed(r),
        "first_seen": s.get("first_seen"),
        "cuts": s.get("cuts", 0), "delta": s.get("delta", 0),
        "days_tracked": s.get("days_tracked", 0),
        "series": s["series"],
        # absent for the ~99.8% of cars that never crossed the border
        **({"local_hist": s["local_hist"]} if s.get("local_hist") else {}),
    }


# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------
def current_rows(all_rows, tids):
    """The latest snapshot for each target: today's rows, or the last day a
    slower-cadence target was fetched."""
    by_target = defaultdict(list)
    for r in all_rows:
        if r["target"] in tids:
            by_target[r["target"]].append(r)
    out = []
    for rs in by_target.values():
        last = max(r["snapshot_date"] for r in rs)
        out += [r for r in rs if r["snapshot_date"] == last]
    return out


def brief_lines(m_entry, listings, prev_day):
    """Three lines that say what changed — for the models being shopped."""
    daily = m_entry["daily"]
    today = daily[-1] if daily else None
    prev = daily[-2] if len(daily) >= 2 else None
    out = []
    priced = [x for x in listings if x["price"] is not None]     # sorted by asking
    if priced:
        b = priced[0]
        line = f"- Lowest asking **{money(b['price'])}** ({place(b)})"
        if not b["local"] and b["ship"]:
            line += f" · + {money(b['ship'])} shipping"
        if (today and prev and today.get("min_price") is not None
                and prev.get("min_price") is not None):
            d = today["min_price"] - prev["min_price"]
            line += (" · = vs " if d == 0 else
                     f" · {'▼' if d < 0 else '▲'} {money(abs(d))} vs ") + prev["date"]
        out.append(line)
    local = [x for x in priced if x["local"]]
    if local:
        b = local[0]
        out.append(f"- Lowest drivable **{money(b['price'])}** "
                   f"({place(b)}) · no shipping")
    elif STATES:
        out.append(f"- Nothing drivable ({scope_label()})")
    # A car seen at two prices moves every fetch and took none of them: it is
    # out of every cut figure already, and counting its flip as a price change
    # made one line disagree with the rest of the sentence — 37 against 33.
    #
    # `swings` is what the filter REMOVED, not how many sawtooth cars exist.
    # The first version of this clause printed the latter — 10 on the i5 where
    # four had moved, 15 on the i7 where eight had — and a reader adding 33+10
    # got a number that was never any count. The clause is a subtraction from
    # the figure beside it, so it has to count the same cars that figure lost.
    def moved(x):
        return len(x["series"]) >= 2 and x["series"][-1][1] != x["series"][-2][1]
    swings = sum(1 for x in listings if moved(x) and two_prices(x.get("series")))
    movers = sum(1 for x in listings if moved(x) and not two_prices(x.get("series")))
    new = sum(1 for x in listings if is_new_today(x)) if prev else 0
    # each trim vanishes on its own cadence — compare against the trim's own
    # previous fetch day, or a slower trim's departures never count
    gone = sum(1 for g in m_entry["gone"]
               if g["likely"] == "delisted" and g["prev_fetch_day"]
               and g["last_seen"] == g["prev_fetch_day"]
               and departure_is_evidence(g))
    # A car the sheet carries no state for is not drivable and not beyond the
    # buyer's states either: in_scope() asks whether the state is one of the
    # buyer's, so a blank state falls out of the first bucket and the page used
    # to drop it silently into the second. Three live cars and eight VINs over
    # the record are in that position. The record names them for the same
    # reason the page does — a count that opposes two buckets has to say when
    # a car is in neither.
    unplaced = sum(1 for x in listings
                   if not x["local"] and not str(x.get("state") or "").strip())
    line = (f"- {len(listings)} on the market · "
            f"{sum(1 for x in listings if x['local'])} drivable"
            + (f" · {unplaced} with no state" if unplaced else ""))
    if prev_day:
        line += (f" · {movers} price change{'s' if movers != 1 else ''}"
                 + (f" ({swings} more moved, seen at two prices, not counted)" if swings else "")
                 + f" · {new} new · {gone} gone")
    out.append(line)
    return out


def trim_detail(sec, t, tl, rows_by_vin, hist, gone, prev_day):
    """Movers, departures, in-state cars by state, five lowest asking out of state."""
    best = next((x for x in tl if x["price"] is not None), None)
    head = f"### {t['label']} — {len(tl)} vehicles"
    tot = TOTALS.get((t["id"], "National"))
    if tot and tot > len(tl):
        head += f" tracked of {tot:,} the API lists nationwide"
    if best:
        head += (f" · lowest asking {money(best['price'])} "
                 f"({place(best)})")
    sec += [head, ""]
    if t["note"]:
        sec += [f"_{t['note']}_", ""]
    # Largest move first, not cheapest car first. A $1 tick and a $7,500 cut
    # read alike in a list ordered by asking price, and the answer to that is
    # to make scale visible rather than to delete the tick: it is exactly what
    # happened, and no threshold on this record can be defended — the sizes
    # below $200 run 1, 1, 1, 1, 1, 1, 10, 40, 44, 49, 49, 50, 58, 59, 76, 80,
    # 80, 80, 80, 85 nine times, 95, 99, 100 five times, so $100 falls at the
    # densest point of the tail rather than at a gap.
    movers = sorted([x for x in tl if len(x["series"]) >= 2
                     and x["series"][-1][1] != x["series"][-2][1]
                     and not two_prices(x.get("series"))],
                    key=lambda x: -abs(x["series"][-1][1] - x["series"][-2][1]))
    if movers:
        sec.append("**Price changes**")
        for x in movers:
            old, new = x["series"][-2][1], x["series"][-1][1]
            sec.append(f"- {money(old)} -> **{money(new)}** "
                       f"({x['city']}, {x['state']}) `{x['vin']}`")
        sec.append("")
    just_gone = [g for g in gone if g["trim_id"] == t["id"]
                 and g["likely"] == "delisted" and g["prev_fetch_day"]
                 and g["last_seen"] == g["prev_fetch_day"]
                 and departure_is_evidence(g)]
    if just_gone:
        sec.append(f"**Gone since {just_gone[0]['last_seen']}**")
        for g in just_gone:
            sec.append(f"- {money(g['last_price'])} · {g['year']} · "
                       f"{g['city']}, {g['state']} · {seen_label(g)} `{g['vin']}`")
        sec.append("")
    # …and the cars that left this watch without leaving the market. They are
    # no departure, so no count and no exit price carries them, but for a
    # certified watch this is the news itself: the car a 2.99% rate applied to
    # yesterday is a car it does not apply to today, and it is still for sale.
    left_watch = [g for g in gone if g["trim_id"] == t["id"] and g.get("still_listed")]
    if left_watch:
        sec.append(f"**Left this watch, the car still listed ({len(left_watch)})**")
        for g in left_watch:
            st = g["still_listed"]
            sec.append(f"- {money(g['last_price'])} · {g['year']} · "
                       f"{g['city']}, {g['state']} · last returned by this watch "
                       f"{g['last_seen']} — the same VIN is listed as "
                       f"{st['trim'] or 'another trim'}"
                       + (f" at {money(st['price'])}" if st["price"] else "")
                       + (", still certified" if st.get("cpo") else ", not certified")
                       + f" `{g['vin']}`")
        sec.append("")
    if STATES and not any(x["local"] for x in tl):
        sec += [f"_Nothing drivable ({scope_label()})._", ""]
    # the buyer's states, in their configured order
    local_states = [st for st in STATES if any(
        x["local"] and x["state"] == st for x in tl)]
    for st in local_states:
        in_st = [x for x in tl if x["local"] and x["state"] == st]
        sec.append(f"**{STATE_NAMES.get(st, st)} ({len(in_st)})**")
        for x in in_st:
            sec.append(fmt_row(rows_by_vin[x["vin"]],
                               summarize((t["id"], x["vin"]), hist), x))
        sec.append("")
    best5 = [x for x in tl if x["price"] is not None and not x["local"]][:5]
    if best5:
        # "Lowest asking", not "cheapest": best5 is chosen by ASKING price and
        # every row prints a landed total, so the list is not in landed order.
        # The M60's own five are the proof — $47,946 / $50,813 / $53,653 /
        # $55,395 / $55,203 landed, the fourth row above the fifth. No car
        # outside any trim's five lands below its fifth today, which is luck
        # rather than a property of the list: the heading has to promise what
        # the sort can defend, and the sort is on asking.
        sec.append("**Lowest asking beyond your states (shipping estimated)**")
        for x in best5:
            sec.append(fmt_row(rows_by_vin[x["vin"]],
                               summarize((t["id"], x["vin"]), hist), x))
        sec.append("")


def compact_line(m_entry, label):
    """One line per comparison model: counts, the floor, the in-state floor."""
    xs = m_entry["listings"]
    if not xs and not m_entry["as_of"]:
        return (f"- **{label}** — not fetched yet · first run "
                f"{m_entry['next_due']} _(every {m_entry['cadence']} days)_")
    if not xs:
        line = f"- **{label}** — nothing found"
    else:
        priced = [x for x in xs if x["price"] is not None]    # sorted by asking
        local = [x for x in priced if x["local"]]
        bits = [f"{len(xs)} cars", f"{sum(1 for x in xs if x['local'])} drivable"]
        if priced:
            b = priced[0]
            bits.append(f"lowest asking {money(b['price'])} ({place(b)})"
                        + (f" + {money(b['ship'])} shipping"
                           if (not b["local"] and b["ship"]) else ""))
        if local:
            bits.append(f"drivable from {money(local[0]['price'])} "
                        f"({place(local[0])})")
        if priced:
            bits.append(f"median asking {money(int(median([x['price'] for x in priced])))}")
        line = f"- **{label}** — " + " · ".join(bits)
    tail = []
    if m_entry["cadence"] > 1:
        tail.append(f"every {m_entry['cadence']} days")
    if m_entry["as_of"] and m_entry["as_of"] != TODAY:
        tail.append(f"as of {m_entry['as_of']}")
    return line + (f" _({' · '.join(tail)})_" if tail else "")


def build_outputs(today_rows, all_rows, hist):
    INDEX_DATES.clear()
    INDEX_DATES.update(find_index_dates(all_rows))
    # Populated HERE, beside INDEX_DATES, and not in main(): tools/rebuild_outputs.py
    # and the tests call build_outputs() directly, and a global filled only on
    # the live path is a global that is empty for every other caller — which is
    # how a rebuild would have quietly dropped the very fact it exists to carry.
    LOCAL_HISTORY.clear()
    LOCAL_HISTORY.update(build_local_history(all_rows))
    if INDEX_DATES:
        print("  ! listed_since " + ", ".join(sorted(INDEX_DATES))
              + " looks like an API index load, not a listing date — "
                "days on market withheld for those cars")
    site = {
        "app": APP,
        "generated": TODAY,
        # The DATA day: the newest snapshot anywhere in the record. generated
        # is the day this file was BUILT — an offline rebuild
        # (tools/rebuild_outputs.py) stamps it with no fetch — so the pages
        # date the numbers by data_through, never by generated.
        "data_through": max((r["snapshot_date"] for r in all_rows),
                            default=None),
        # The oldest day the DEPARTURE record can vouch for. delisted() retires
        # a car once it has been gone 60 days, to stop the gone list growing
        # forever, but snapshots.csv is never pruned — so before this date the
        # file knows a day's cars only through the survivors of it. The
        # dashboard rebuilds "lowest asking in your scope" from per-car
        # history, and rebuilding it past this line would quietly reinstate
        # exactly the survivorship bias that history was added to remove, so
        # the page stops there instead.
        "departures_from": date.fromordinal(TODAY_ORD - 60).isoformat(),
        "buyer": {
            "id": BUYER.get("id", ""), "label": BUYER.get("label", ""),
            "states": STATES,
            "state_names": {s: STATE_NAMES.get(s, s) for s in STATES},
            "search_states": SEARCH_STATES,
            # the anchor is published ONLY when it came from the public
            # buyer.anchor config — never coordinates a legacy home zip resolved to
            "anchor": ([HOME[0], HOME[1]]
                       if (ANCHOR and coords_ok(*HOME)) else None),
            "scope_label": scope_label(),
            "shopping": SHOPPING,
            "picks": {"count": PICKS.get("count", 4), "per_model": PICKS.get("per_model", 2),
                      # the page hard-coded 2 and nothing published it; both
                      # sides read this now, and 0 means "rank by margin alone"
                      "reserve_shopping": to_int(PICKS.get("reserve_shopping")) or 0,
                      "max_miles": PICKS.get("max_miles", 50000),
                      "cents_per_mile": PICKS.get("cents_per_mile", 0.30),
                      "mileage_baseline": PICKS.get("mileage_baseline", 20000),
                      "exclude_accidents": PICKS.get("exclude_accidents", True),
                      "exclude_rental": PICKS.get("exclude_rental", True)},
            # The bands ride along with the flat keys because this block is the
            # published record of how the `ship` on every row was arrived at.
            # Without them a reader reconstructs `d x ship_per_mile` and gets a
            # different number than the one sitting beside it in the same file.
            "ship_per_mile": BUYER.get("ship_per_mile"),
            "ship_min": BUYER.get("ship_min"),
            "ship_cost": BUYER.get("ship_cost"),
            "ship_bands": BUYER.get("ship_bands") or None,
            "ship_road_factor": BUYER.get("ship_road_factor"),
            "ship_calibrated": BUYER.get("ship_calibrated"),
            # ship_calibration() was written, tested three ways and then never
            # called, so the README's promise that a malformed quote "is
            # announced and skipped" described a code path no run could reach.
            # It is None while buyer.ship_quotes is empty, which is exactly what
            # it should be — nothing on any surface changes today — and the day
            # a quote lands, the run says on its own log how far the bands are
            # from it and the number is in the export rather than in a function
            # nobody calls.
            "ship_calibration": ship_calibration(),
            "cents_per_mile": BUYER.get("cents_per_mile"),
            "mileage_baseline": BUYER.get("mileage_baseline"),
            "shortlist": [{"vin": v, "note": n} for v, n in SHORTLIST.items()],
            "finance": finance_export(),
            "fees": fees_export(),
        },
        "brands": {},
    }
    days = sorted({r["snapshot_date"] for r in all_rows})

    # group targets brand -> model
    tree = defaultdict(lambda: defaultdict(list))
    for t in TARGETS.values():
        tree[t["brand"]][t["model_key"]].append(t)

    full, compact, all_scored = [], [], []      # report sections, assembled at the end
    live_by_vin, gone_by_vin = {}, {}           # shortlist lookups, across every model
    events = {"cuts": [], "new": [], "gone": []}    # what changed today, once
    for bkey, models in tree.items():
        b_entry = {"label": WATCHLIST[bkey].get("label", bkey),
                   "models": {}}
        site["brands"][bkey] = b_entry
        for mkey, trims in models.items():
            m0 = trims[0]
            label = m0["model_label"]
            tids = {t["id"] for t in trims}
            shopping = any(t["shopping"] for t in trims) or not SHOPPING
            m_rows_all = [r for r in all_rows if r["target"] in tids]
            m_days = sorted({r["snapshot_date"] for r in m_rows_all})
            m_rows = current_rows(all_rows, tids)
            as_of = max((r["snapshot_date"] for r in m_rows), default=None)
            prev_day = m_days[-2] if len(m_days) >= 2 else None
            # Hoisted out of the literal below: the trim entries need it, and
            # a dict cannot reference a key it has not finished defining.
            # Computed once either way.
            m_gone = delisted(tids, all_rows, m_rows, hist)
            m_entry = {
                "label": label, "note": m0["model_note"],
                "notes": m0["model_notes"],
                "years": sorted({str(y) for t in trims for y in t["years"]}),
                "shopping": shopping,
                "cadence": min(t["cadence"] for t in trims),
                "as_of": as_of,
                "fetched_today": any(due_on(t, TODAY_ORD) for t in trims),
                "next_due": min(next_due(t) for t in trims),
                "params": {"min_price": m0.get("min_price")},
                "trims": {t["id"]: {"label": t["label"], "note": t["note"],
                                    "depth": t["depth"], "cadence": t["cadence"],
                                    "shopping": t["shopping"],
                                    "years": [str(y) for y in t["years"]],
                                    "min_price": t.get("min_price"),
                                    "max_miles": t.get("max_miles"),
                                    "cpo_only": bool(t.get("cpo_only")),
                                    "market_total": TOTALS.get((t["id"], "National")),
                                    # Per TRIM, not per model: a median mixing an
                                    # eDrive50 with an M70 describes no car that
                                    # exists. The trim is the cohort a reader is
                                    # actually shopping within.
                                    **exit_stats(m_gone, t["id"])}
                          for t in trims},
                "listings": [],
                "daily": daily_stats(m_rows_all, m_days),
                "daily_by_trim": {t["id"]: daily_stats(
                    [r for r in m_rows_all if r["target"] == t["id"]], m_days)
                    for t in trims},
                # The days each TARGET actually fetched, which is not a thing
                # the rest of this file can be reconstructed from. daily_stats
                # carries a target forward to its most recent fetch, so its
                # dates say nothing about when the fetch happened; and a car
                # carries only one trim_id, so a target whose every current car
                # is filed under a sibling target leaves no sightings of its
                # own at all. The i5's nationwide CPO watch is exactly that: it
                # fetched on 2026-09-03 and 09-04 and returned two certified
                # cars, both of which the export files under eDrive40 — so the
                # page, reading fetch days off the cars, believed the watch had
                # not run since 09-01 and carried two DEPARTED certified cars
                # forward into every day since. Its rebuilt row read 135 cars
                # against a precomputed 133. Nine bytes a day per target ends
                # the guessing.
                "fetch_days": {t["id"]: sorted({r["snapshot_date"] for r in m_rows_all
                                                if r["target"] == t["id"]})
                               for t in trims},
                "gone": m_gone,
            }
            b_entry["models"][mkey] = m_entry
            if SHORTLIST:
                for g in m_entry["gone"]:
                    gone_by_vin.setdefault(str(g["vin"]).upper(), g)

            if not m_rows:
                if shopping:
                    full += [f"## Shopping: {label}", "", "No listings found yet.", ""]
                else:
                    compact.append(compact_line(m_entry, label))
                continue

            display = pick_display_rows(m_rows)
            # Across the model's targets, not the chosen copy's alone: see
            # summarize_vin. `display` is already one row per VIN.
            listings = [listing_entry(r, summarize_vin(r["vin"], tids, hist, (r["target"], r["vin"])))
                        for r in display]
            m_entry["listings"] = sorted(listings, key=lambda x: x["price"] or 10**9)
            m_entry["market"] = {**market_stats(m_entry["listings"]),
                                 **sale_stats(m_gone),
                                 # Whether the cars behind the pooled exit
                                 # median are ONE cohort — measured on the
                                 # departed cars themselves, not on the
                                 # watchlist.
                                 "one_trim": one_cohort(m_gone)}
            scored = score_picks(m_entry["listings"], label)
            # which cars are being SHOPPED, carried on the pick itself — the
            # reservation below is across models, so it cannot ask the loop
            for p in scored:
                p["shopping"] = shopping
            all_scored += scored
            if SHORTLIST:
                for x in m_entry["listings"]:
                    live_by_vin.setdefault(str(x["vin"]).upper(), (x, label))
            if prev_day and m_entry["fetched_today"]:
                by_vin = {p["vin"]: p for p in scored}
                for x in m_entry["listings"]:
                    s_ = x["series"]
                    tl = str(x.get("trim_label") or "")
                    name = label if tl.lower() in ("", "all", "all trims") else f"{label} {tl}"
                    # `as_of`, not TODAY: the model's own last fetch day. Its
                    # sibling detectors below and above (new, gone) are already
                    # relative to the data, so on a day the tracker has not
                    # fetched — a local rebuild — the "## Today" section lost
                    # every price-cut bullet while still printing "79 new · 31
                    # gone" and per-model lines counting 42 price changes. Per
                    # MODEL, because a slow-cadence model's last fetch is its
                    # own, which is the same rule new and gone follow.
                    # a two-price car's downward day is its other price, not a
                    # cut a dealer took today — see two_prices()
                    if (len(s_) >= 2 and s_[-1][0] == as_of and s_[-1][1] < s_[-2][1]
                            and not two_prices(s_)):
                        events["cuts"].append({"amount": s_[-2][1] - s_[-1][1],
                                               "x": x, "label": name,
                                               "shopping": shopping})
                    if is_new_today(x):
                        p = by_vin.get(x["vin"])
                        events["new"].append({"x": x, "label": name,
                                              # the margin only where the
                                              # cohort's interval supports
                                              # the word: see score_picks
                                              "pct": p["pick_pct"] if p and p["pick_stand"] == "under" else None,
                                              "shopping": shopping})
                for g in m_entry["gone"]:
                    if (g["likely"] == "delisted" and g["prev_fetch_day"]
                            and g["last_seen"] == g["prev_fetch_day"]
                            and departure_is_evidence(g)):
                        events["gone"].append({"vin": g["vin"],
                                               "label": f"{label} {g.get('trim_label') or ''}".strip(),
                                               "last_seen": g["last_seen"],
                                               "last_price": g["last_price"],
                                               "shopping": shopping})

            if not shopping:
                compact.append(compact_line(m_entry, label))
                continue

            sec = [f"## Shopping: {label}", ""]
            if as_of != TODAY:
                sec += [f"_Not fetched today — showing {as_of}._", ""]
            sec += brief_lines(m_entry, m_entry["listings"], prev_day) + [""]
            # cars first seen this run lead the section — a well-priced new
            # listing is the one thing the buyer must catch before it sells
            if prev_day and m_entry["fetched_today"]:
                by_vin = {p["vin"]: p for p in scored}
                new_today = sorted(
                    [x for x in m_entry["listings"] if is_new_today(x)],
                    key=lambda x: -(by_vin[x["vin"]]["pick_pct"]
                                    if x["vin"] in by_vin else -1.0))
                if new_today:
                    reach = sum(1 for x in new_today if reach_not_arrival(x))
                    sec += [f"**New today ({len(new_today)})** — first seen this run,"
                            + (f" {reach} of them listed {REACH_DAYS}+ days before the tracker saw "
                               f"{'it' if reach == 1 else 'them'} — reach, not arrival;" if reach else "")
                            + " best value first", ""]
                    sec += [fmt_new(x, by_vin.get(x["vin"])) for x in new_today[:8]]
                    if len(new_today) > 8:
                        sec.append(f"- …and {len(new_today) - 8} more on the dashboard")
                    sec.append("")
            local_picks, ship_picks = split_picks(
                scored, PICKS.get("count", 4), None,
                to_int(PICKS.get("reserve_shopping")) or 0)
            if local_picks or ship_picks:
                sec += [f"**Spicy picks** — {picks_rule()}", ""]
                if local_picks:
                    sec += [f"_Drivable ({scope_label()}):_", ""]
                    sec += [fmt_pick(p) for p in local_picks] + [""]
                else:
                    sec += [f"_Nothing drivable qualifies yet ({scope_label()})._", ""]
                if ship_picks:
                    sec += ["_Worth the ship:_", ""]
                    sec += [fmt_pick(p) for p in ship_picks] + [""]
            counts = Counter(x["state"] for x in listings if x["local"])
            n_out = sum(1 for x in listings if not x["local"])
            summary = " · ".join([f"{st} {counts.get(st, 0)}" for st in STATES]
                                 + [f"beyond {n_out}"])
            mline = market_line(m_entry["market"])
            sec += [f"_{len(listings)} vehicles across {len(trims)} "
                    f"trim{'s' if len(trims) != 1 else ''} · {summary}_"
                    + (f"\n_{mline}_" if mline else ""), ""]
            rows_by_vin = {r["vin"]: r for r in display}
            for t in trims:
                tl = [x for x in m_entry["listings"] if x["trim_id"] == t["id"]]
                if not tl:
                    sec += [f"### {t['label']} — none found", ""]
                    continue
                trim_detail(sec, t, tl, rows_by_vin, hist, m_entry["gone"], prev_day)
            full += sec

    scored_by_vin = {str(p["vin"]).upper(): p for p in all_scored}
    today_sec, subject = build_today(events)
    report = ([f"# {APP} — {TODAY}", ""]
              + shortlist_section(live_by_vin, gone_by_vin, scored_by_vin)
              + today_sec
              + full)
    top_local, top_ship = split_picks(all_scored, PICKS.get("count", 4),
                                      PICKS.get("per_model", 2),
                                      to_int(PICKS.get("reserve_shopping")) or 0)
    if top_local or top_ship:
        report += ["## Spicy picks across the watchlist", "",
                   f"_{picks_rule()}. Asking prices shown._", ""]
        if top_local:
            report += [f"### Drivable — {scope_label()}", ""]
            report += [fmt_pick(p) for p in top_local] + [""]
        else:
            report += [f"_Nothing drivable qualifies yet ({scope_label()})._", ""]
        if top_ship:
            report += ["### Worth the ship — nationwide", ""]
            report += [fmt_pick(p) for p in top_ship] + [""]
    if compact:
        report += ["## Comparison", "",
                   "_By asking price, with a shipping estimate per car, on a slower "
                   f"cadence: the 20 lowest asking in {'/'.join(SEARCH_STATES) or 'your states'} "
                   "and the 20 lowest asking nationwide per model. Every car is on the "
                   "dashboard._", ""]
        report += compact + [""]
    # CALLS is this PROCESS's counter, and an offline rebuild makes none — so
    # the footer printed "0 API calls today" over a day that had really spent
    # 24, and every dispatch rebuilds. A rebuild says what it is instead of
    # overwriting the day's cost with a zero.
    spent = (f"{CALLS} API call{'s' if CALLS != 1 else ''} today" if CALLS
             else "outputs rebuilt from the snapshot on disk — no calls made")
    report += ["---",
               f"_{len(hist)} vehicle histories across {len(days)} "
               f"day{'s' if len(days) != 1 else ''} · {spent}._"]
    return "\n".join(report), site, subject


def send_email(report, subject=None):
    key = os.environ.get("RESEND_API_KEY")
    to = os.environ.get("EMAIL_TO")
    if not (key and to):
        # Not a warning. Email is OFF ON PURPOSE here: the dashboard is this
        # tool's delivery surface and the owner said plainly they do not want
        # the email. RESEND_API_KEY and EMAIL_TO have never been set, and an
        # annotation on every run for a feature nobody wants is just noise
        # that trains you to ignore annotations. Set both secrets and it
        # starts sending; until then this line is a fact, not a complaint.
        print("Email off (no RESEND_API_KEY / EMAIL_TO) — report written to REPORT.md.")
        return
    try:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {key}"},
            json={"from": f"{APP} <onboarding@resend.dev>", "to": [to],
                  "subject": subject or f"{APP} — {TODAY}", "text": report},
            timeout=30)
        print(f"Email: HTTP {r.status_code}")
    except requests.RequestException as e:
        print(f"Email failed: {e}")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    today_calls, worst, avg = planned_calls()
    monthly = avg * 30.5
    print(f"{len(TARGETS)} targets · API calls today {today_calls} · worst day "
          f"{worst} (cap {BUDGET}) · average {avg:.1f}/day ≈ {monthly:,.0f}/month "
          f"(plan {MONTHLY:,})")
    if worst > BUDGET or monthly > MONTHLY:
        sys.exit(f"Plan too big: worst day {worst} vs budget_per_day={BUDGET}, "
                 f"≈{monthly:,.0f}/month vs budget_per_month={MONTHLY:,}. Give "
                 f"more targets a cadence of 2 or more, set trims to depth "
                 f"'light', or raise the budgets.")

    # A day already fetched must not be fetched again. The cron fires once
    # (0 11 * * *); every extra run is a workflow_dispatch, and each one
    # re-bills the whole day at full price. Reconstructed from the snapshot
    # commits' own footers, that habit spent 529 calls over nine days —
    # 58.8/day, about 1,790 a month against a 1,000-call tier — while
    # planned_calls() reported 30.0/day and approved every run, because it
    # reads INTENT and never sees what a second run costs.
    #
    # The guard is here rather than in the workflow because the workflow is
    # not the only way in. Outputs can always be regenerated for free with
    # tools/rebuild_outputs.py, which is what a re-run is almost always
    # actually for. ALLOW_REFETCH=1 is the escape hatch for the one case
    # that reaches this guard with a thin day behind it: a COMPLETED run in
    # which a source failed after retry. A run that died mid-fetch never
    # gets here — rows are written only after the whole fetch loop, so it
    # left nothing and the next attempt is not a re-fetch at all.
    already = {r["snapshot_date"] for r in load_history()}
    if TODAY in already and not os.environ.get("ALLOW_REFETCH"):
        due_today = [t["id"] for t in TARGETS.values() if due_on(t, TODAY_ORD)]
        # Exit 3, not 1, and it is not a failure. A re-run is almost always
        # somebody wanting FRESH OUTPUTS after a config or code change, which
        # costs nothing — so daily.yml reads this code and regenerates them
        # offline instead of stopping. The first version exited 1 and painted
        # the run red, which reads as a broken tracker rather than a saving:
        # the operator's own words on seeing it were "got this error".
        print(f"{TODAY} has already been fetched — not spending "
              f"{today_calls} calls on it again.\n"
              f"  Nothing is wrong. To rebuild REPORT.md and docs/data.json "
              f"from the snapshot already on disk, for free:\n"
              f"      python3 tools/rebuild_outputs.py\n"
              f"  (A dispatched run does this for you automatically.)\n"
              f"  To genuinely RE-FETCH all {today_calls} calls for the day — "
              f"only when a completed run reported sources failed after retry; "
              f"a run that died mid-fetch wrote nothing and simply runs again, "
              f"and tomorrow's run fetches afresh anyway ({len(due_today)} "
              f"targets due today):\n"
              f"      ALLOW_REFETCH=1 python3 Tracking.py")
        sys.exit(ALREADY_FETCHED)

    rows = {}
    dropped = Counter()
    via = defaultdict(set)      # (target id, vin) -> the queries that returned it
    for tid, t in TARGETS.items():
        if not due_on(t, TODAY_ORD):
            print(f"{tid}: not today (every {t['cadence']} days, next {next_due(t)})")
            continue
        raw_n = 0
        sorts, pages = sorts_pages(t)
        for source_name, source in sources_for(t):
            for si, sort in enumerate(sorts):
                if si and (tid, source_name) in EXHAUSTED:
                    break   # the first sort already returned this scope's
                            # entire result set; another sort re-fetches it
                for page in range(1, pages + 1):
                    batch = fetch(source_name, source, sort, page, t)
                    if batch is None:
                        break   # failed even after the retry: keep what we
                                # have, and never call this scope exhausted
                    raw_n += len(batch)
                    for rec in batch:
                        n = normalize(rec, t, dropped)
                        if not n:
                            continue
                        if sort == "price.asc":
                            wk = (tid, source_name)
                            PRICE_WINDOW[wk] = max(PRICE_WINDOW.get(wk, 0),
                                                   n["price"])
                        elif sort == "miles.asc" and n["miles"] != "":
                            wk = (tid, source_name)
                            MILES_WINDOW[wk] = max(MILES_WINDOW.get(wk, 0),
                                                   n["miles"])
                        key = (tid, n["vin"])
                        SOURCE_VINS.setdefault((tid, source_name), set()).add(n["vin"])
                        # Which query returned it, accumulated across every
                        # sort and source that did. See FIELDS["via"].
                        via[key].add(f"{source_name}:{sort}")
                        cur = rows.get(key)
                        if cur is None or n["price"] < to_int(cur["price"]):
                            rows[key] = n
                    if len(batch) < PER_PAGE:
                        # a short page means the query returned everything in
                        # its scope — the sort order cannot change the set
                        EXHAUSTED.add((tid, source_name))
                        break
            # newest-first pages: catch cars listed since the last run before
            # they ever rank among the cheapest. An exhausted scope was
            # already fetched in full, so newest would only repeat it.
            for page in range(1, t["newest"] + 1):
                if (tid, source_name) in EXHAUSTED:
                    break
                batch = fetch(source_name, source, NEWEST_SORT, page, t)
                if batch is None:
                    break
                raw_n += len(batch)
                for rec in batch:
                    n = normalize(rec, t, dropped)
                    if not n:
                        continue
                    key = (tid, n["vin"])
                    SOURCE_VINS.setdefault((tid, source_name), set()).add(n["vin"])
                    via[key].add(f"{source_name}:{NEWEST_SORT}")
                    cur = rows.get(key)
                    if cur is None or n["price"] < to_int(cur["price"]):
                        rows[key] = n
                if len(batch) < PER_PAGE:
                    EXHAUSTED.add((tid, source_name))
                    break
        kept = sum(1 for k in rows if k[0] == tid)
        print(f"{tid}: {raw_n} raw -> {kept} kept")
    if dropped:
        print("Dropped: " + ", ".join(f"{k} x{v}" for k, v in dropped.items()))
    print(f"API calls made: {CALLS}"
          + (f" · {FAILED_FETCHES} failed after retry" if FAILED_FETCHES else "")
          + (f" · {len(EXHAUSTED)} exhaustive queries" if EXHAUSTED else ""))
    OVERLAP.update(source_overlap(rows))
    report_source_overlap(OVERLAP)
    save_overlap_history(OVERLAP)
    # Written BEFORE the outputs are built, because build_outputs() -> delisted()
    # reads it back: today's departures are then judged by the same recorded
    # facts a rebuild will use tomorrow, so the two can never disagree.
    save_fetch_log(fetch_log_row())
    if FAILED_SCOPES:
        print(f"  ! {len(FAILED_SCOPES)} quer{'y' if len(FAILED_SCOPES) == 1 else 'ies'} failed "
              f"after retry — every car only they could see is 'not checked', not gone: "
              + ", ".join(f"{tid} {src}" for tid, src in sorted(FAILED_SCOPES)))
    spend = spend_report(today_calls)
    report_spend(spend, save_spend_history(spend))
    print(f"Geocoding: {GEOCODED} rescued from zip, {UNPLACED} unplaceable, "
          f"{ZIP_LOOKUPS} zip lookups ({len(ZIP_CACHE)} cached)")
    save_zip_cache()

    # Stamp each kept row with every query that returned it. Accumulated over
    # the whole fetch, not taken from the winning record, because a row is
    # replaced whenever a cheaper duplicate arrives and the replacement would
    # otherwise drop the earlier query from its own provenance.
    for (tid, vin), r in rows.items():
        r["via"] = "|".join(sorted(via.get((tid, vin), ())))
    today_rows = list(rows.values())
    if not today_rows:
        msg = ("No listings fetched for any target — "
               "leaving data, report and site untouched.")
        send_email(msg, subject=f"{APP} — run FAILED {TODAY}")
        sys.exit(msg)

    history_rows = [r for r in load_history() if r["snapshot_date"] != TODAY]
    all_rows = history_rows + today_rows
    write_rows(all_rows)

    hist = build_history(all_rows)
    report, site, subject = build_outputs(today_rows, all_rows, hist)

    Path("REPORT.md").write_text(report)
    (DOCS / "data.json").write_text(json.dumps(site, indent=1))
    print("\n" + report)
    print(f"\nSubject: {subject}")
    send_email(report, subject=subject)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:      # a silent dead tracker looks like a quiet market
        send_email(f"The daily run crashed before finishing:\n\n{e!r}",
                   subject=f"{APP} — run FAILED {TODAY}")
        raise
