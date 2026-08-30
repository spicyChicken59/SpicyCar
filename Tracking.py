#!/usr/bin/env python3
"""SpicyCar — used-car purchase analyzer. All config lives in targets.json.

Two things are configured, separately:

  buyer      who is purchasing: home zip, the states they will drive to for
             a car (no shipping), and how they value miles and shipping.
  watchlist  what to track: brand -> model -> trim. Each trim is a target
             (id "brand-model-trim").

Every target is fetched twice on its day: once filtered to the buyer's
states plus search_states (one call, the API takes a comma list) and once
nationally. A target's cadence (1 = daily, 2 = every other day, ...)
spreads the comparison brands across days so the whole watchlist fits the
API plan; buyer.shopping names the targets that lead the report in full,
while the rest get one line each. A listing is
"drivable" — no shipping — when its own state field is one of the buyer's
states, and nothing else: no coordinates involved, so listings the API
could not geocode still land in the right bucket, and the buyer decides
scope by naming states rather than by a radius. search_states widens the
query net to neighbouring states worth watching from beyond. Coordinates
price shipping, from the distance to home.
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
PARAM_KEYS = ["min_price", "depth", "cadence", "sorts", "pages", "years", "newest"]
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
SAMPLE = DATA / "sample_record.json"
ZIPCODES = DATA / "zipcodes.json"

FIELDS = ["snapshot_date", "target", "vin", "year", "trim", "miles",
          "price", "dealer", "city", "state", "listed_since", "url",
          "msrp", "color", "cpo", "owners", "accidents", "usage", "image",
          "carfax", "lat", "lon", "distance"]


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
            m_offset = None       # a model's trims all run on the same days
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
                if m_offset is None:
                    m_offset = seen[t["cadence"]] % t["cadence"]
                    seen[t["cadence"]] += 1
                t["offset"] = m_offset
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
    """Which sorts, and how many pages each, a target fetches per source."""
    if t["depth"] == "full":
        return list(t["sorts"]), int(t["pages"])
    return [t["sorts"][0]], 1


def calls_for(t):
    sorts, pages = sorts_pages(t)
    return len(SOURCES) * (len(sorts) * pages + t["newest"])


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


def ship_for(r):
    """Shipping for a listing: nothing in-state; otherwise by distance from
    home when it is known, else the flat ship_cost."""
    if in_scope(r):
        return 0
    d = row_distance(r)
    rate = to_float(BUYER.get("ship_per_mile"))
    if d is not None and rate:
        floor = to_float(BUYER.get("ship_min")) or 0
        return int(round(max(floor, d * rate)))
    return to_int(BUYER.get("ship_cost")) or 0


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


def score_picks(listings, model_label):
    """Every eligible car of one model, scored against the tightest cohort
    with three or more eligible cars: its own trim and model year first,
    then the model year, then the whole model. Without the cohorts a 2023
    eDrive50 i7 reads as half price simply because the blended median
    carries six-figure M70s and 2026 cars."""
    pool = [x for x in listings if pick_eligible(x)]
    if len(pool) < 3:
        return []
    values = {id(x): pick_value(x) for x in pool}
    med_all = median(values.values())
    by_year, by_ty = defaultdict(list), defaultdict(list)
    for x in pool:
        y = str(x.get("year") or "")
        tr = str(x.get("trim") or "").strip().lower()
        by_year[y].append(values[id(x)])
        if tr:
            by_ty[(tr, y)].append(values[id(x)])
    year_med = {y: median(vs) for y, vs in by_year.items() if len(vs) >= 3}
    ty_med = {k: median(vs) for k, vs in by_ty.items() if len(vs) >= 3}
    out = []
    for x in pool:
        y = str(x.get("year") or "")
        tr = str(x.get("trim") or "").strip().lower()
        if (tr, y) in ty_med:
            med, p_year, p_trim = ty_med[(tr, y)], y, trim_disp(model_label, x.get("trim"))
        elif y in year_med:
            med, p_year, p_trim = year_med[y], y, ""
        else:
            med, p_year, p_trim = med_all, "", ""
        v = values[id(x)]
        out.append({**x, "model_label": model_label,
                    "pick_year": p_year, "pick_trim": p_trim,
                    "pick_under": int(round(med - v)),
                    "pick_pct": (med - v) / med if med else 0.0})
    out.sort(key=lambda p: -p["pick_pct"])
    return out


def choose_picks(scored, n, per_model=None):
    out, per = [], Counter()
    for p in sorted(scored, key=lambda p: -p["pick_pct"]):
        if p["pick_pct"] <= 0:
            break    # sorted, so nothing after this is under typical either
        if per_model and per[p["model_label"]] >= per_model:
            continue
        out.append(p)
        per[p["model_label"]] += 1
        if len(out) >= n:
            break
    return out


def split_picks(scored, n, per_model=None):
    """Two lists under the same rule: drivable picks, and worth-the-ship
    picks from everywhere else. Scoring stays within-model across the whole
    market, so a drivable pick means the same thing as a national one."""
    return (choose_picks([p for p in scored if p.get("local")], n, per_model),
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
    line += (f"\n  _spicy pick: {p['pick_pct']:.0%} under typical for a {cohort} "
             f"({money(p['pick_under'])} less)_")
    if p.get("flags"):
        line += f" · _{' · '.join(p['flags'])}_"
    if p.get("url"):
        line += f"\n  [listing]({p['url']})"
    line += f" `{p.get('vin', '')}`"
    return line


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
    tracked = [x for x in listings if x.get("days_tracked", 0) >= 2]
    cut_cars = [x for x in tracked if x.get("cuts")]
    drops = []
    for x in listings:
        s = x.get("series") or []
        drops += [a - b for (_, a), (_, b) in zip(s, s[1:]) if b < a]
    return {
        "median_days_listed": int(median(dl)) if dl else None,
        "tracked_2d": len(tracked),
        "cut_share": round(len(cut_cars) / len(tracked), 2) if tracked else None,
        "median_cut": int(median(drops)) if drops else None,
    }


def sale_stats(gone):
    """How fast this model's cars actually leave, from the ones that really
    left: days from the listing date (or first sighting, when the dealer
    never said) to the last day the car was seen. Out-of-window and
    unchecked departures are not sales and are left out."""
    spans = []
    for g in gone:
        if g.get("likely") != "delisted":
            continue
        start = g.get("listed_since") or g.get("first_seen")
        try:
            spans.append(max(0, (date.fromisoformat(str(g.get("last_seen"))[:10])
                                 - date.fromisoformat(str(start)[:10])).days))
        except (TypeError, ValueError):
            continue
    return {"n_sold": len(spans),
            "median_days_to_sale": int(median(spans)) if spans else None}


def market_line(stats):
    """The market context as one report phrase, or ''. """
    bits = []
    if stats.get("median_days_listed") is not None:
        bits.append(f"typical car {stats['median_days_listed']}d on market")
    if stats.get("cut_share") is not None and stats.get("tracked_2d", 0) >= 5:
        cut = f"{stats['cut_share']:.0%} cut while tracked"
        if stats.get("median_cut"):
            cut += f", median {money(stats['median_cut'])}"
        bits.append(cut)
    if stats.get("median_days_to_sale") is not None and stats.get("n_sold", 0) >= 5:
        bits.append(f"sold cars lasted ~{stats['median_days_to_sale']}d "
                    f"({stats['n_sold']} sold)")
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
        sec.append(f"- …and {len(cuts) - 3} more price change{'s' if len(cuts) - 3 != 1 else ''}"
                   " in the sections below")
    if cuts and not bits:
        e = cuts[0]
        bits.append(f"▼{money(e['amount'])} cut on "
                    f"{'drivable ' if e['x']['local'] else ''}{e['label']}")
    news = [e for e in events["new"] if e["shopping"]]
    if news:
        best = max(news, key=lambda e: e["pct"] if e["pct"] is not None else -1)
        line = f"- {len(news)} new on the shopped models"
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
            if (len(series) >= 2 and series[-1][0] == TODAY
                    and series[-1][1] < series[-2][1]):
                tags.append(f"▼ CUT {money(series[-2][1] - series[-1][1])} today")
            elif x.get("cuts"):
                tags.append(f"down {x['cuts']}x ({money(x['delta'])})")
            if x.get("days_listed") is not None:
                tags.append(f"on market {x['days_listed']}d")
            p = scored_by_vin.get(vin)
            if p and p["pick_pct"] > 0:
                tags.append(f"{p['pick_pct']:.0%} under typical")
            tags += x.get("flags") or []
            if tags:
                line += f"\n  _{' · '.join(tags)}_"
        elif vin in gone_by_vin:
            g = gone_by_vin[vin]
            obj = g
            verdict = {
                "delisted": "**GONE — likely sold or pulled**",
                "out of window": "missing today — priced above the fetch "
                                 "cut-off, probably still for sale",
                "not checked": "missing — not checked since it was last "
                               "seen, so nothing is known yet",
            }.get(g["likely"], "missing today")
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
        bits.append(f"on market {dl}d, new to the tracker")
    else:
        bits.append("new to the tracker")
    line = "- " + " · ".join(b for b in bits if b)
    if p and p.get("pick_pct", 0) > 0:
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
    return (", ".join(bits) + "; ranked by how far under the typical price "
            "for the model — its own trim and model year when there are "
            f"enough of them — a car sits, allowing ${cpm:.2f} a mile")


# --------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------
CALLS = 0
PRICE_WINDOW = {}      # (target id, source) -> highest price its price.asc query returned today
EXHAUSTED = set()      # (target id, source): a query came back short, so it returned
                       # that scope's ENTIRE result set — no cheapest-N cut-off applies
FAILED_FETCHES = 0     # requests that still failed after the retry
TOTALS = {}            # (target id, source) -> the API's own total result count,
                       # when the response envelope carries one — the honest
                       # denominator behind "N tracked"
ENVELOPE_WARNED = False


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
                    payload = {}
                batch = payload.get("data") or [] if isinstance(payload, dict) else []
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
                return batch
            err = f"HTTP {r.status_code} {r.text[:200]}"
        print(f"  ! {t['id']} {source_name} {sort} p{page} (try {attempt}): {err}")
        if attempt == 1:
            time.sleep(2)
    FAILED_FETCHES += 1
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
    }


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------
def load_history():
    if not SNAPSHOTS.exists():
        return []
    rows = []
    with SNAPSHOTS.open(newline="") as f:
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


def summarize(key, hist):
    series = hist.get(key, [])
    if not series:
        return {"series": []}
    prices = [p for _, p in series]
    return {
        "series": series,
        "cuts": sum(1 for a, b in zip(prices, prices[1:]) if b < a),
        "delta": prices[-1] - prices[0],
        "days_tracked": len(series),
        "first_seen": series[0][0],
    }


# --------------------------------------------------------------------------
# Row facts
# --------------------------------------------------------------------------
def is_cpo(r):
    return str(r.get("cpo", "")).lower() in ("1", "true", "y")


def flags(r):
    out = []
    if is_cpo(r):
        out.append("CPO")
    owners = to_int(r.get("owners"))
    if owners == 1:
        out.append("1-owner")
    elif owners and owners > 1:
        out.append(f"{owners} owners")
    acc = to_int(r.get("accidents"))
    if acc is not None:
        out.append("no accidents" if acc == 0
                   else f"{acc} accident{'s' if acc > 1 else ''}")
    if "lease" in str(r.get("usage", "")).lower():
        out.append("ex-lease")
    return out


def days_listed(r):
    try:
        since = date.fromisoformat(str(r.get("listed_since", ""))[:10])
    except ValueError:
        return None
    return (date.fromisoformat(TODAY) - since).days


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
    if s.get("cuts"):
        tags.append(f"down {s['cuts']}x ({money(s['delta'])})")
    if s.get("days_tracked", 0) >= 21:
        tags.append(f"tracked {s['days_tracked']}d")
    if s.get("days_tracked") == 1:
        tags.append("NEW")
    dl = days_listed(r)
    if dl is not None and dl >= 30:
        tags.append(f"on market {dl}d")
    # negotiation context: a car most of its own model has outsold is a car
    # whose dealer has a reason to talk
    if entry and (entry.get("stale_pct") or 0) >= 0.75:
        tags.append(f"sits longer than {entry['stale_pct']:.0%} of the model")
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
def daily_stats(rows):
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["snapshot_date"]].append(r)
    out = []
    for d in sorted(by_day):
        display = pick_display_rows(by_day[d])
        adjs = [a for a, _ in (landed(r) for r in display) if a is not None]
        prices = [p for p in (to_int(r["price"]) for r in display) if p]
        out.append({
            "date": d,
            "n": len(display),
            "n_local": sum(1 for r in display if in_scope(r)),
            "min_price": min(prices) if prices else None,
            "min_adj": min(adjs) if adjs else None,
            "median_adj": int(median(adjs)) if adjs else None,
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
    that window is reconstructed from history: the day's max kept price IS
    the cheapest-N cut-off, and a day with fewer kept rows than one page
    returned its scope's entire market, so no cut-off applies. When the
    vanish day is today's live fetch, the run's own per-source window
    (PRICE_WINDOW / EXHAUSTED) is used instead — it is exact.

    Each entry carries its own trim's previous fetch day, because trims of
    one model can run on different cadences and the model's yesterday is
    not every trim's."""
    today_vins = {(r["target"], r["vin"]) for r in today_rows}
    days_by_tid = defaultdict(set)
    win_max, win_n = {}, Counter()      # (target, fetch day) -> window
    for r in all_rows:
        if r["target"] in tids:
            d = r["snapshot_date"]
            days_by_tid[r["target"]].add(d)
            win_n[(r["target"], d)] += 1
            p = to_int(r["price"])
            if p is not None and p > win_max.get((r["target"], d), 0):
                win_max[(r["target"], d)] = p
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
        if van_day == TODAY and any((tid, k) in PRICE_WINDOW
                                    or (tid, k) in EXHAUSTED for k in keys):
            # live run, vanished at today's fetch: use its exact window
            cutoffs = [c for c in (PRICE_WINDOW.get((tid, k)) for k in keys)
                       if c is not None]
            cutoff = max(cutoffs) if cutoffs else None
            exhausted = any((tid, k) in EXHAUSTED for k in keys)
        else:
            # older departure, or an offline rebuild: reconstruct the vanish
            # day's window from the snapshots themselves
            cutoff = win_max.get((tid, van_day))
            exhausted = (van_day is not None
                         and win_n[(tid, van_day)] < PER_PAGE)
        if van_day is None:
            likely = "not checked"      # not fetched again since last seen
        elif exhausted:
            likely = "delisted"         # a query that saw everything missed it
        elif cutoff is None or last_price is None:
            likely = "not checked"      # no window to judge the absence by
        elif last_price > cutoff:
            likely = "out of window"    # pricier than that day's cheapest-N cut-off
        else:
            likely = "delisted"
        out.append({
            "likely": likely,
            "vin": vin, "year": to_int(r["year"]), "trim": r["trim"],
            "trim_id": tid, "trim_label": t["label"],
            "state": r["state"], "local": in_scope(r),
            "distance": row_distance(r), "ship": ship,
            "miles": to_int(r["miles"]),
            "last_price": last_price, "adj": adj,
            "city": r["city"], "dealer": r["dealer"],
            "url": r["url"], "last_seen": last_day,
            "listed_since": r["listed_since"],
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
            "series": s.get("series", []),
            "flags": flags(r),
        })
    out.sort(key=lambda x: (x["last_seen"], -(x["last_price"] or 0)),
             reverse=True)
    return out


def listing_entry(r, s):
    t = TARGETS[r["target"]]
    adj, ship = landed(r)
    return {
        "vin": r["vin"], "year": to_int(r["year"]), "trim": r["trim"],
        "trim_id": t["id"], "trim_label": t["label"],
        "state": r["state"], "local": in_scope(r),
        # the car's own public location (already in the committed CSV),
        # so the dashboard can put every listing on the map
        "lat": to_float(r.get("lat")), "lon": to_float(r.get("lon")),
        "distance": row_distance(r), "ship": ship,
        "miles": to_int(r["miles"]), "price": to_int(r["price"]),
        "adj": adj, "msrp": to_int(r.get("msrp")),
        "dealer": r["dealer"], "city": r["city"],
        "url": r["url"], "image": r.get("image", ""),
        "carfax": r.get("carfax", ""), "color": r.get("color", ""),
        "cpo": is_cpo(r), "owners": to_int(r.get("owners")),
        "accidents": to_int(r.get("accidents")),
        "usage": r.get("usage", ""), "flags": flags(r),
        "listed_since": r["listed_since"], "days_listed": days_listed(r),
        "first_seen": s.get("first_seen"),
        "cuts": s.get("cuts", 0), "delta": s.get("delta", 0),
        "days_tracked": s.get("days_tracked", 0),
        "series": s["series"],
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
    movers = sum(1 for x in listings if len(x["series"]) >= 2
                 and x["series"][-1][1] != x["series"][-2][1])
    new = sum(1 for x in listings if x["days_tracked"] == 1) if prev else 0
    # each trim vanishes on its own cadence — compare against the trim's own
    # previous fetch day, or a slower trim's departures never count
    gone = sum(1 for g in m_entry["gone"]
               if g["likely"] == "delisted" and g["prev_fetch_day"]
               and g["last_seen"] == g["prev_fetch_day"])
    line = (f"- {len(listings)} on the market · "
            f"{sum(1 for x in listings if x['local'])} drivable")
    if prev_day:
        line += (f" · {movers} price change{'s' if movers != 1 else ''} · "
                 f"{new} new · {gone} gone")
    out.append(line)
    return out


def trim_detail(sec, t, tl, rows_by_vin, hist, gone, prev_day):
    """Movers, departures, in-state cars by state, best value out of state."""
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
    movers = [x for x in tl if len(x["series"]) >= 2
              and x["series"][-1][1] != x["series"][-2][1]]
    if movers:
        sec.append("**Price changes**")
        for x in movers:
            old, new = x["series"][-2][1], x["series"][-1][1]
            sec.append(f"- {money(old)} -> **{money(new)}** "
                       f"({x['city']}, {x['state']}) `{x['vin']}`")
        sec.append("")
    just_gone = [g for g in gone if g["trim_id"] == t["id"]
                 and g["likely"] == "delisted" and g["prev_fetch_day"]
                 and g["last_seen"] == g["prev_fetch_day"]]
    if just_gone:
        sec.append(f"**Gone since {just_gone[0]['last_seen']}**")
        for g in just_gone:
            sec.append(f"- {money(g['last_price'])} · {g['year']} · "
                       f"{g['city']}, {g['state']} · tracked "
                       f"{g['days_tracked']}d `{g['vin']}`")
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
        sec.append("**Cheapest beyond your states (shipping stated)**")
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
                      "max_miles": PICKS.get("max_miles", 50000),
                      "cents_per_mile": PICKS.get("cents_per_mile", 0.30),
                      "mileage_baseline": PICKS.get("mileage_baseline", 20000),
                      "exclude_accidents": PICKS.get("exclude_accidents", True),
                      "exclude_rental": PICKS.get("exclude_rental", True)},
            "ship_per_mile": BUYER.get("ship_per_mile"),
            "ship_min": BUYER.get("ship_min"),
            "ship_cost": BUYER.get("ship_cost"),
            "cents_per_mile": BUYER.get("cents_per_mile"),
            "mileage_baseline": BUYER.get("mileage_baseline"),
            "shortlist": [{"vin": v, "note": n} for v, n in SHORTLIST.items()],
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
                                    "market_total": TOTALS.get((t["id"], "National"))}
                          for t in trims},
                "listings": [],
                "daily": daily_stats(m_rows_all),
                "daily_by_trim": {t["id"]: daily_stats(
                    [r for r in m_rows_all if r["target"] == t["id"]])
                    for t in trims},
                "gone": delisted(tids, all_rows, m_rows, hist),
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
            listings = [listing_entry(r, summarize((r["target"], r["vin"]), hist))
                        for r in display]
            m_entry["listings"] = sorted(listings, key=lambda x: x["price"] or 10**9)
            m_entry["market"] = {**market_stats(m_entry["listings"]),
                                 **sale_stats(m_entry["gone"])}
            scored = score_picks(m_entry["listings"], label)
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
                    if len(s_) >= 2 and s_[-1][0] == TODAY and s_[-1][1] < s_[-2][1]:
                        events["cuts"].append({"amount": s_[-2][1] - s_[-1][1],
                                               "x": x, "label": name,
                                               "shopping": shopping})
                    if x["days_tracked"] == 1:
                        p = by_vin.get(x["vin"])
                        events["new"].append({"x": x, "label": name,
                                              "pct": p["pick_pct"] if p else None,
                                              "shopping": shopping})
                for g in m_entry["gone"]:
                    if (g["likely"] == "delisted" and g["prev_fetch_day"]
                            and g["last_seen"] == g["prev_fetch_day"]):
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
                    [x for x in m_entry["listings"] if x["days_tracked"] == 1],
                    key=lambda x: -(by_vin[x["vin"]]["pick_pct"]
                                    if x["vin"] in by_vin else -1.0))
                if new_today:
                    sec += [f"**New today ({len(new_today)})** — first seen this run,"
                            " best value first", ""]
                    sec += [fmt_new(x, by_vin.get(x["vin"])) for x in new_today[:8]]
                    if len(new_today) > 8:
                        sec.append(f"- …and {len(new_today) - 8} more on the dashboard")
                    sec.append("")
            local_picks, ship_picks = split_picks(scored, PICKS.get("count", 4))
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
                                      PICKS.get("per_model", 2))
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
                   "_By asking price, shipping stated per car, on a slower cadence: "
                   f"the cheapest 20 in {'/'.join(SEARCH_STATES) or 'your states'} and the "
                   "cheapest 20 nationwide per model. Every car is on the dashboard._", ""]
        report += compact + [""]
    report += ["---",
               f"_{len(hist)} vehicle histories across {len(days)} "
               f"day{'s' if len(days) != 1 else ''} · {CALLS} API calls today._"]
    return "\n".join(report), site, subject


def send_email(report, subject=None):
    key = os.environ.get("RESEND_API_KEY")
    to = os.environ.get("EMAIL_TO")
    if not (key and to):
        print("Email not configured — skipping.")
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

    rows = {}
    dropped = Counter()
    for tid, t in TARGETS.items():
        if not due_on(t, TODAY_ORD):
            print(f"{tid}: not today (every {t['cadence']} days, next {next_due(t)})")
            continue
        raw_n = 0
        sorts, pages = sorts_pages(t)
        for source_name, source in SOURCES:
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
                        key = (tid, n["vin"])
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
    print(f"Geocoding: {GEOCODED} rescued from zip, {UNPLACED} unplaceable, "
          f"{ZIP_LOOKUPS} zip lookups ({len(ZIP_CACHE)} cached)")
    save_zip_cache()

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
