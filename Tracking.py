#!/usr/bin/env python3
"""SpicyCar — used-car purchase analyzer. All config lives in targets.json.

Two things are configured, separately:

  buyer      who is purchasing: home zip, the states they will drive to for
             a car (no shipping), and how they value miles and shipping.
  watchlist  what to track: brand -> model -> trim. Each trim is a target
             (id "brand-model-trim").

Every target is fetched twice a day: once filtered to the buyer's states
(one call, the API takes a comma list) and once nationally. A listing is
"in-state" when its own state field is one of the buyer's states — no
coordinates involved, so listings the API could not geocode still land in
the right bucket. Coordinates are only used for the distance from home,
which prices shipping for out-of-state cars.
"""

import csv
import json
import math
import os
import sys
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
WATCHLIST = CFG["watchlist"]
DEFAULTS = CFG.get("defaults", {})
LEGACY_IDS = CFG.get("legacy_ids", {})
BUDGET = CFG.get("budget_per_day", 33)
PER_PAGE = 20                       # the free plan clamps limit to 20
PARAM_KEYS = ["min_price", "depth", "sorts", "pages", "years"]

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
    for bkey, b in WATCHLIST.items():
        if not b.get("active", True):
            continue
        for mkey, m in b["models"].items():
            if not m.get("active", True):
                continue
            for tkey, tr in m["trims"].items():
                if not tr.get("active", True):
                    continue
                t = {}
                for layer in (DEFAULTS, b, m, tr):
                    for k in PARAM_KEYS:
                        if k in layer:
                            t[k] = layer[k]
                t.update({
                    "id": f"{bkey}-{mkey}-{tkey}",
                    "brand": bkey, "brand_label": b.get("label", bkey),
                    "make": b["make"],
                    "model_key": mkey, "model_label": m.get("label", mkey),
                    "model": m.get("model", mkey),
                    "model_note": m.get("note", ""),
                    "trim_key": tkey, "label": tr.get("label", tkey),
                    "note": tr.get("note", ""),
                    "trim_query": tr.get("trim_query", ""),
                    "trim_match": tr.get("trim_match", ""),
                })
                t.setdefault("years", [])
                t.setdefault("sorts", ["price.asc"])
                t.setdefault("pages", 1)
                t.setdefault("depth", "light")
                targets[t["id"]] = t
    return targets


TARGETS = build_targets()
# Each source is a dict of extra query params. The States source asks the
# API for the buyer's states directly (comma = OR), one call per sort/page.
SOURCES = ([("States", {"retailListing.state": ",".join(STATES)})] if STATES
           else []) + [("National", None)]


def sorts_pages(t):
    """Which sorts, and how many pages each, a target fetches per source."""
    if t["depth"] == "full":
        return list(t["sorts"]), int(t["pages"])
    return [t["sorts"][0]], 1


def planned_calls():
    total = 0
    for t in TARGETS.values():
        sorts, pages = sorts_pages(t)
        total += len(SOURCES) * len(sorts) * pages
    return total


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


def zip_coords(z):
    """lat/lon for a 5-digit US zip, cached in data/zipcodes.json so a zip is
    only ever looked up once. Misses are cached too; network errors are not."""
    global ZIP_LOOKUPS
    z = str(z or "").strip()[:5]
    if not (len(z) == 5 and z.isdigit()):
        return None, None
    if z in ZIP_CACHE:
        hit = ZIP_CACHE[z]
        return (hit[0], hit[1]) if hit else (None, None)
    try:
        r = requests.get(f"https://api.zippopotam.us/us/{z}", timeout=15)
        ZIP_LOOKUPS += 1
        if r.status_code == 200:
            place = ((r.json() or {}).get("places") or [{}])[0]
            lat = to_float(place.get("latitude"))
            lon = to_float(place.get("longitude"))
            if coords_ok(lat, lon):
                ZIP_CACHE[z] = [round(lat, 5), round(lon, 5)]
                return lat, lon
        ZIP_CACHE[z] = None
    except requests.RequestException as e:
        print(f"  ! zip {z}: {e}")
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

HOME = zip_coords(BUYER.get("home_zip"))       # (lat, lon) or (None, None)
if not coords_ok(*HOME):
    print(f"  ! home zip {BUYER.get('home_zip')!r} could not be located — "
          f"distances unavailable, flat ship_cost applies")


def dist_home(lat, lon):
    """Whole miles from the buyer's home, or None."""
    if coords_ok(lat, lon) and coords_ok(*HOME):
        return int(round(haversine(lat, lon, HOME[0], HOME[1])))
    return None


def row_distance(r):
    d = to_int(r.get("distance"))
    if d is not None:
        return d
    return dist_home(to_float(r.get("lat")), to_float(r.get("lon")))


def in_scope(r):
    """In one of the buyer's states: drivable, no shipping."""
    return str(r.get("state", "")).strip().upper() in STATES


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
    if price is None or miles is None:
        return None
    base = to_int(BUYER.get("mileage_baseline")) or 20000
    cpm = to_float(BUYER.get("cents_per_mile"))
    if cpm is None:
        cpm = 0.25
    return int(price + ship + (miles - base) * cpm)


def landed(r):
    ship = ship_for(r)
    return adjusted(to_int(r["price"]), to_int(r["miles"]), ship), ship


# --------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------
CALLS = 0
PRICE_WINDOW = {}      # target id -> highest price returned by its price.asc query today


def year_param(years):
    ys = sorted(str(y) for y in years)
    if not ys:
        return None
    return ys[0] if len(ys) == 1 else f"{ys[0]}-{ys[-1]}"


def fetch(source_name, source, sort, page, t):
    global CALLS
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
    CALLS += 1
    try:
        r = requests.get(BASE, headers=HEADERS, params=params, timeout=45)
    except requests.RequestException as e:
        print(f"  ! {t['id']} {source_name} {sort} p{page}: {e}")
        return []
    if r.status_code != 200:
        print(f"  ! {t['id']} {source_name} {sort} p{page}: "
              f"HTTP {r.status_code} {r.text[:200]}")
        return []
    batch = (r.json() or {}).get("data") or []
    if batch and not SAMPLE.exists():
        SAMPLE.write_text(json.dumps(batch[0], indent=2))
    return batch


def normalize(rec, t, dropped):
    global GEOCODED, UNPLACED
    tm = t.get("trim_match", "").lower()
    if tm and tm not in json.dumps(rec).lower():
        dropped["trim mismatch"] += 1
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


def fmt_row(r, s):
    miles = to_int(r["miles"])
    adj, ship = landed(r)
    bits = [f"**{money(to_int(r['price']))}**"]
    if miles is not None:
        bits.append(f"{miles:,} mi")
    if adj is not None:
        bits.append(f"landed {money(adj)}"
                    + (f" (ship {money(ship)})" if ship else ""))
    where = f"{r['year']} · {r['city']}, {r['state']}"
    d = row_distance(r)
    if d is not None:
        where += f" · {d:,} mi from home"
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
            "min_adj": min(adjs) if adjs else None,
            "median_adj": int(median(adjs)) if adjs else None,
            "median_price": int(median(prices)) if prices else None,
        })
    return out


def delisted(tids, all_rows, today_rows, hist):
    """Vehicles seen before but not today. Because each query only returns
    the cheapest N, a car priced above today's window for its trim may simply
    have been pushed out rather than sold — that is flagged, not hidden."""
    today_vins = {(r["target"], r["vin"]) for r in today_rows}
    window_max = PRICE_WINDOW
    by_key = defaultdict(list)
    for r in all_rows:
        if r["target"] in tids and (r["target"], r["vin"]) not in today_vins:
            by_key[(r["target"], r["vin"])].append(r)
    out = []
    for (tid, vin), rows in by_key.items():
        last_day = max(r["snapshot_date"] for r in rows)
        r = pick_display_rows([x for x in rows
                               if x["snapshot_date"] == last_day])[0]
        s = summarize((tid, vin), hist)
        adj, ship = landed(r)
        t = TARGETS[tid]
        last_price = to_int(r["price"])
        # an in-state car comes back through either query, so it is only
        # out of window when it is above both cut-offs
        keys = ["States", "National"] if in_scope(r) else ["National"]
        cutoffs = [c for c in (window_max.get((tid, k)) for k in keys)
                   if c is not None]
        cutoff = max(cutoffs) if cutoffs else None
        if cutoff is None:
            likely = "unknown"          # nothing fetched for this trim today
        elif last_price is not None and last_price > cutoff:
            likely = "out of window"    # pricier than today's cheapest-N cut-off
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
            "first_seen": s.get("first_seen"),
            "days_tracked": s.get("days_tracked", 0),
            "cuts": s.get("cuts", 0), "delta": s.get("delta", 0),
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
def build_outputs(today_rows, all_rows, hist):
    report = [f"# {APP} — {TODAY}", ""]
    site = {
        "app": APP,
        "generated": TODAY,
        "buyer": {
            "id": BUYER.get("id", ""), "label": BUYER.get("label", ""),
            "home_zip": BUYER.get("home_zip", ""),
            "home": ({"lat": HOME[0], "lon": HOME[1]}
                     if coords_ok(*HOME) else None),
            "states": STATES,
            "state_names": {s: STATE_NAMES.get(s, s) for s in STATES},
            "ship_per_mile": BUYER.get("ship_per_mile"),
            "ship_min": BUYER.get("ship_min"),
            "ship_cost": BUYER.get("ship_cost"),
            "cents_per_mile": BUYER.get("cents_per_mile"),
            "mileage_baseline": BUYER.get("mileage_baseline"),
        },
        "brands": {},
    }
    days = sorted({r["snapshot_date"] for r in all_rows})
    prev_day = days[-2] if len(days) >= 2 else None

    # group targets brand -> model
    tree = defaultdict(lambda: defaultdict(list))
    for t in TARGETS.values():
        tree[t["brand"]][t["model_key"]].append(t)

    for bkey, models in tree.items():
        b_entry = {"label": WATCHLIST[bkey].get("label", bkey),
                   "models": {}}
        site["brands"][bkey] = b_entry
        for mkey, trims in models.items():
            m0 = trims[0]
            tids = {t["id"] for t in trims}
            m_rows_all = [r for r in all_rows if r["target"] in tids]
            m_today = [r for r in today_rows if r["target"] in tids]
            m_entry = {
                "label": m0["model_label"], "note": m0["model_note"],
                "years": sorted({str(y) for t in trims for y in t["years"]}),
                "params": {"min_price": m0.get("min_price")},
                "trims": {t["id"]: {"label": t["label"], "note": t["note"],
                                    "depth": t["depth"],
                                    "years": [str(y) for y in t["years"]],
                                    "min_price": t.get("min_price")}
                          for t in trims},
                "listings": [],
                "daily": daily_stats(m_rows_all),
                "daily_by_trim": {t["id"]: daily_stats(
                    [r for r in m_rows_all if r["target"] == t["id"]])
                    for t in trims},
                "gone": delisted(tids, all_rows, today_rows, hist),
            }
            b_entry["models"][mkey] = m_entry

            report += [f"## {m0['model_label']}", ""]
            if not m_today:
                report += ["No listings found today.", ""]
                continue

            display = pick_display_rows(m_today)
            listings = [listing_entry(r, summarize((r["target"], r["vin"]),
                                                   hist)) for r in display]
            m_entry["listings"] = sorted(listings,
                                         key=lambda x: x["adj"] or 10**9)

            counts = Counter(x["state"] for x in listings if x["local"])
            n_out = sum(1 for x in listings if not x["local"])
            summary = " · ".join([f"{st} {counts.get(st, 0)}" for st in STATES]
                                 + [f"out of state {n_out}"])
            report += [f"_{len(listings)} vehicles across "
                       f"{len(trims)} trim{'s' if len(trims) != 1 else ''} · "
                       f"{summary}_", ""]

            for t in trims:
                tl = [x for x in m_entry["listings"] if x["trim_id"] == t["id"]]
                if not tl:
                    report += [f"### {t['label']} — none found", ""]
                    continue
                best = next((x for x in tl if x["adj"] is not None), None)
                head = f"### {t['label']} — {len(tl)} vehicles"
                if best:
                    head += (f" · lowest landed {money(best['adj'])} "
                             f"({best['city']}, {best['state']})")
                report += [head, ""]
                if t["note"]:
                    report += [f"_{t['note']}_", ""]

                movers = [x for x in tl if len(x["series"]) >= 2
                          and x["series"][-1][1] != x["series"][-2][1]]
                if movers:
                    report.append("**Price changes**")
                    for x in movers:
                        old, new = x["series"][-2][1], x["series"][-1][1]
                        report.append(f"- {money(old)} -> **{money(new)}** "
                                      f"({x['city']}, {x['state']}) `{x['vin']}`")
                    report.append("")

                just_gone = [g for g in m_entry["gone"]
                             if g["trim_id"] == t["id"]
                             and g["last_seen"] == prev_day
                             and g["likely"] == "delisted"]
                if just_gone:
                    report.append(f"**Gone since {prev_day}**")
                    for g in just_gone:
                        report.append(
                            f"- {money(g['last_price'])} · {g['year']} · "
                            f"{g['city']}, {g['state']} · tracked "
                            f"{g['days_tracked']}d `{g['vin']}`")
                    report.append("")

                rows_by_vin = {r["vin"]: r for r in display}
                if STATES and not any(x["local"] for x in tl):
                    report += [f"_Nothing in {'/'.join(STATES)} today._", ""]
                for st in STATES:
                    in_st = [x for x in tl if x["local"] and x["state"] == st]
                    if not in_st:
                        continue
                    report.append(f"**{STATE_NAMES.get(st, st)} ({len(in_st)})**")
                    for x in in_st:
                        report.append(fmt_row(rows_by_vin[x["vin"]],
                                              summarize((t["id"], x["vin"]), hist)))
                    report.append("")
                best5 = [x for x in tl if x["adj"] is not None
                         and not x["local"]][:5]
                if best5:
                    report.append("**Best value out of state (landed)**")
                    for x in best5:
                        report.append(fmt_row(rows_by_vin[x["vin"]],
                                              summarize((t["id"], x["vin"]), hist)))
                    report.append("")

    report += ["---",
               f"_{len(hist)} vehicle histories across {len(days)} "
               f"day{'s' if len(days) != 1 else ''} · {CALLS} API calls today._"]
    return "\n".join(report), site


def send_email(report):
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
                  "subject": f"{APP} — {TODAY}", "text": report},
            timeout=30)
        print(f"Email: HTTP {r.status_code}")
    except requests.RequestException as e:
        print(f"Email failed: {e}")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    planned = planned_calls()
    print(f"{len(TARGETS)} targets · planned API calls: {planned}/day "
          f"(≈{planned * 30:,}/month) · budget {BUDGET}/day")
    if planned > BUDGET:
        sys.exit(f"Planned {planned} API calls exceed budget_per_day={BUDGET}. "
                 f"Set more trims to depth 'light', reduce pages/sorts, "
                 f"or raise the budget.")

    rows = {}
    dropped = Counter()
    for tid, t in TARGETS.items():
        raw_n = 0
        sorts, pages = sorts_pages(t)
        for source_name, source in SOURCES:
            for sort in sorts:
                for page in range(1, pages + 1):
                    batch = fetch(source_name, source, sort, page, t)
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
                        break   # short page: nothing further for this sort
        kept = sum(1 for k in rows if k[0] == tid)
        print(f"{tid}: {raw_n} raw -> {kept} kept")
    if dropped:
        print("Dropped: " + ", ".join(f"{k} x{v}" for k, v in dropped.items()))
    print(f"API calls made: {CALLS}")
    print(f"Geocoding: {GEOCODED} rescued from zip, {UNPLACED} unplaceable, "
          f"{ZIP_LOOKUPS} zip lookups ({len(ZIP_CACHE)} cached)")
    save_zip_cache()

    today_rows = list(rows.values())
    if not today_rows:
        sys.exit("No listings fetched for any target — "
                 "leaving data, report and site untouched.")

    history_rows = [r for r in load_history() if r["snapshot_date"] != TODAY]
    all_rows = history_rows + today_rows
    write_rows(all_rows)

    hist = build_history(all_rows)
    report, site = build_outputs(today_rows, all_rows, hist)

    Path("REPORT.md").write_text(report)
    (DOCS / "data.json").write_text(json.dumps(site, indent=1))
    print("\n" + report)
    send_email(report)


if __name__ == "__main__":
    main()
