#!/usr/bin/env python3
"""Multi-brand vehicle market tracker. All config lives in targets.json.

targets.json is organised brand -> model -> trim. Each trim is a tracked
target (id "brand-model-trim"). Every target is fetched twice a day: once
for the configured region (one wide radius that covers all local markets)
and once nationally. Listings are then placed into the local markets by
distance from each market's centre, so adding a market costs no API calls.
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

CFG = json.loads(Path("targets.json").read_text())
MARKETS = CFG["markets"]
REGION = CFG.get("region")
DEFAULTS = CFG.get("defaults", {})
LEGACY_IDS = CFG.get("legacy_ids", {})
BUDGET = CFG.get("budget_per_day", 33)
PER_PAGE = 20                       # the free plan clamps limit to 20
PARAM_KEYS = ["cents_per_mile", "mileage_baseline", "ship_cost", "min_price",
              "depth", "sorts", "pages", "years"]

DATA = Path("data")
DATA.mkdir(exist_ok=True)
DOCS = Path("docs")
DOCS.mkdir(exist_ok=True)
SNAPSHOTS = DATA / "snapshots.csv"
SAMPLE = DATA / "sample_record.json"
ZIPCODES = DATA / "zipcodes.json"

FIELDS = ["snapshot_date", "target", "market", "vin", "year", "trim", "miles",
          "price", "dealer", "city", "state", "listed_since", "url",
          "msrp", "color", "cpo", "owners", "accidents", "usage", "image",
          "carfax", "lat", "lon", "distance"]


# --------------------------------------------------------------------------
# Config resolution: defaults <- brand <- model <- trim
# --------------------------------------------------------------------------
def build_targets():
    targets = {}
    for bkey, b in CFG["brands"].items():
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
SOURCES = [("Region", REGION), ("National", None)] if REGION else [("National", None)]


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


def market_hits(lat, lon):
    """[(miles, market)] for every local market whose radius contains the point,
    nearest first; plus the nearest market overall as the last element."""
    if not coords_ok(lat, lon):
        return [], None
    dists = sorted((haversine(lat, lon, m["lat"], m["lon"]), name)
                   for name, m in MARKETS.items())
    hits = [(d, n) for d, n in dists if d <= MARKETS[n]["distance"]]
    return hits, dists[0]


def markets_for(r):
    """Local markets a row belongs to (computed from coordinates when present)."""
    lat, lon = to_float(r.get("lat")), to_float(r.get("lon"))
    if coords_ok(lat, lon):
        hits, _ = market_hits(lat, lon)
        return [n for _, n in hits]
    return [r["market"]] if r.get("market") and r["market"] != "National" else []


def in_region(r):
    """Would this row come back from the regional query?"""
    lat, lon = to_float(r.get("lat")), to_float(r.get("lon"))
    if REGION and coords_ok(lat, lon) and "lat" in REGION:
        return haversine(lat, lon, REGION["lat"], REGION["lon"]) <= REGION["distance"]
    return r.get("market", "National") != "National"


def params_for(r):
    return TARGETS.get(r.get("target"), DEFAULTS)


def adjusted(price, miles, t, ship=0):
    if price is None or miles is None:
        return None
    base = t.get("mileage_baseline", 20000)
    cpm = t.get("cents_per_mile", 0.25)
    return int(price + ship + (miles - base) * cpm)


def landed(r, t=None):
    t = t or params_for(r)
    ship = t.get("ship_cost", 0) if r["market"] == "National" else 0
    return adjusted(to_int(r["price"]), to_int(r["miles"]), t, ship), ship


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
        params["zip"] = source["zip"]
        params["distance"] = source["distance"]
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
        global GEOCODED, UNPLACED
        lat, lon = zip_coords(first(rec, ["retailListing.zip", "zip",
                                          "dealer.zip"]))
        if coords_ok(lat, lon):
            GEOCODED += 1
        else:
            UNPLACED += 1
            lat = lon = None
    hits, nearest = market_hits(lat, lon)
    market = hits[0][1] if hits else "National"
    distance = int(round(nearest[0])) if nearest else ""
    return {
        "snapshot_date": TODAY,
        "target": t["id"],
        "market": market,
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
        "state": first(rec, ["retailListing.state", "dealer.state",
                             "location.state"]),
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
        "distance": distance,
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
    """One row per VIN. Older snapshots stored one row per market; newer ones
    store one row with coordinates. Either way: prefer local, then cheapest."""
    by_vin = defaultdict(list)
    for r in rows:
        by_vin[r["vin"]].append(r)
    out = []
    for vin, rs in by_vin.items():
        local = [r for r in rs if r["market"] != "National"]
        pick = dict(min(local or rs, key=lambda r: to_int(r["price"]) or 10**9))
        mk = set(markets_for(pick))
        for r in rs:
            mk.update(markets_for(r))
        pick["markets"] = sorted(mk)
        out.append(pick)
    return out


def fmt_row(r, s):
    t = params_for(r)
    miles = to_int(r["miles"])
    adj, ship = landed(r, t)
    bits = [f"**{money(to_int(r['price']))}**"]
    if miles is not None:
        bits.append(f"{miles:,} mi")
    if adj is not None:
        bits.append(f"{'landed' if ship else 'adj'} {money(adj)}")
    where = f"{r['year']} · {r['city']}, {r['state']}"
    if r.get("distance") not in ("", None):
        where += f" · {to_int(r['distance']):,} mi away"
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
            "n_local": sum(1 for r in display if r["market"] != "National"),
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
        adj, _ = landed(r)
        t = TARGETS[tid]
        last_price = to_int(r["price"])
        cutoff = window_max.get((tid, "Region" if in_region(r) else "National"))
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
            "market": r["market"], "markets": r["markets"],
            "miles": to_int(r["miles"]),
            "last_price": to_int(r["price"]), "adj": adj,
            "city": r["city"], "state": r["state"], "dealer": r["dealer"],
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
    adj, _ = landed(r, t)
    return {
        "vin": r["vin"], "year": to_int(r["year"]), "trim": r["trim"],
        "trim_id": t["id"], "trim_label": t["label"],
        "market": r["market"], "markets": r["markets"],
        "distance": to_int(r.get("distance")),
        "miles": to_int(r["miles"]), "price": to_int(r["price"]),
        "adj": adj, "msrp": to_int(r.get("msrp")),
        "dealer": r["dealer"], "city": r["city"], "state": r["state"],
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
    report = [f"# Auto Market Tracker — {TODAY}", ""]
    site = {
        "generated": TODAY,
        "region": REGION,
        "markets": {n: {"distance": m["distance"], "lat": m["lat"],
                        "lon": m["lon"]} for n, m in MARKETS.items()},
        "brands": {},
    }
    days = sorted({r["snapshot_date"] for r in all_rows})
    prev_day = days[-2] if len(days) >= 2 else None

    # group targets brand -> model
    tree = defaultdict(lambda: defaultdict(list))
    for t in TARGETS.values():
        tree[t["brand"]][t["model_key"]].append(t)

    for bkey, models in tree.items():
        b_entry = {"label": CFG["brands"][bkey].get("label", bkey),
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
                "market_names": list(MARKETS) + ["National"],
                "params": {k: m0.get(k) for k in
                           ("cents_per_mile", "mileage_baseline",
                            "ship_cost", "min_price")},
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

            counts = Counter()
            for x in listings:
                for mk in x["markets"]:
                    counts[mk] += 1
            counts["National"] = sum(1 for x in listings
                                     if x["market"] == "National")
            summary = " · ".join(f"{mk} {counts[mk]}"
                                 for mk in list(MARKETS) + ["National"])
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
                local = [x for x in tl if x["market"] != "National"]
                if local:
                    report.append(f"**Local ({len(local)})**")
                    for x in local:
                        report.append(fmt_row(rows_by_vin[x["vin"]],
                                              summarize((t["id"], x["vin"]), hist)))
                    report.append("")
                best5 = [x for x in tl if x["adj"] is not None
                         and x["market"] == "National"][:5]
                if best5:
                    report.append("**Best value nationwide (landed)**")
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
            json={"from": "tracker <onboarding@resend.dev>", "to": [to],
                  "subject": f"Auto Market Tracker — {TODAY}", "text": report},
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
