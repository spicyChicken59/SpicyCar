#!/usr/bin/env python3
"""Multi-target vehicle market tracker. All config lives in targets.json."""

import csv
import json
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
TARGETS = {k: t for k, t in CFG["targets"].items() if t.get("active", True)}

DATA = Path("data")
DATA.mkdir(exist_ok=True)
DOCS = Path("docs")
DOCS.mkdir(exist_ok=True)
SNAPSHOTS = DATA / "snapshots.csv"
SAMPLE = DATA / "sample_record.json"

SORTS = ["price.asc", "miles.asc"]
PAGES = 2
PER_PAGE = 20
MIN_PRICE_DEFAULT = 5000   # below this it's a monthly payment or a typo, not a car

FIELDS = ["snapshot_date", "target", "market", "vin", "year", "trim", "miles",
          "price", "dealer", "city", "state", "listed_since", "url",
          "msrp", "color", "cpo", "owners", "accidents", "usage", "image",
          "carfax"]


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


def int_or_blank(v):
    n = to_int(v)
    return n if n is not None else ""


def adjusted(price, miles, t, ship=0):
    if price is None or miles is None:
        return None
    base = t.get("mileage_baseline", 20000)
    cpm = t.get("cents_per_mile", 0.25)
    return int(price + ship + (miles - base) * cpm)


def fetch(market_name, year, sort, t):
    m = MARKETS.get(market_name)
    rows = []
    for page in range(1, PAGES + 1):
        params = {
            "vehicle.make": t["make"],
            "vehicle.model": t["model"],
            "vehicle.year": year,
            "sort": sort,
            "limit": PER_PAGE,
            "page": page,
        }
        if t.get("trim_query"):
            params["vehicle.trim"] = t["trim_query"]
        if m:
            params["zip"] = m["zip"]
            params["distance"] = m["distance"]
        try:
            r = requests.get(BASE, headers=HEADERS, params=params, timeout=45)
        except requests.RequestException as e:
            print(f"  ! {market_name} {year} {sort} p{page}: {e}")
            break
        if r.status_code != 200:
            print(f"  ! {market_name} {year} {sort} p{page}: "
                  f"HTTP {r.status_code} {r.text[:200]}")
            break
        batch = (r.json() or {}).get("data") or []
        if not batch:
            break
        rows.extend(batch)
        if not SAMPLE.exists():
            SAMPLE.write_text(json.dumps(batch[0], indent=2))
    return rows


def normalize(rec, tid, t, market_name, dropped):
    tm = t.get("trim_match", "").lower()
    if tm and tm not in json.dumps(rec).lower():
        dropped["trim mismatch"] += 1
        return None
    vin = first(rec, ["vehicle.vin", "vin"])
    price = to_int(first(rec, ["retailListing.price", "price"], None))
    if not vin or not price:
        dropped["no vin/price"] += 1
        return None
    if price < t.get("min_price", MIN_PRICE_DEFAULT):
        dropped["below min_price"] += 1
        return None
    miles = to_int(first(rec, ["retailListing.miles", "retailListing.mileage",
                               "vehicle.mileage", "mileage", "miles"], None))
    return {
        "snapshot_date": TODAY,
        "target": tid,
        "market": market_name,
        "vin": vin,
        "year": first(rec, ["vehicle.year", "year"]),
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
    }


def load_history():
    if not SNAPSHOTS.exists():
        return []
    with SNAPSHOTS.open(newline="") as f:
        return [{k: r.get(k, "") or "" for k in FIELDS}
                for r in csv.DictReader(f)]


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
        key = (r.get("target", ""), r["vin"])
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


def money(n):
    if not isinstance(n, int):
        return "—"
    return f"-${-n:,}" if n < 0 else f"${n:,}"


def is_cpo(r):
    return str(r.get("cpo", "")).lower() in ("1", "true", "y")


def flags(r):
    """Short buyer-relevant facts from the history block, when present."""
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


def pick_display_rows(t_rows):
    """One row per VIN; prefer a local-market listing over National."""
    by_vin = defaultdict(list)
    for r in t_rows:
        by_vin[r["vin"]].append(r)
    out = []
    for vin, rows in by_vin.items():
        local = [r for r in rows if r["market"] != "National"]
        pick = min(local or rows, key=lambda r: to_int(r["price"]) or 10**9)
        pick = dict(pick)
        pick["markets"] = sorted({r["market"] for r in rows})
        out.append(pick)
    return out


def landed(r, t):
    ship = t.get("ship_cost", 0) if r["market"] == "National" else 0
    return adjusted(to_int(r["price"]), to_int(r["miles"]), t, ship), ship


def fmt_row(r, t, s):
    miles = to_int(r["miles"])
    adj, ship = landed(r, t)
    bits = [f"**{money(to_int(r['price']))}**"]
    if miles is not None:
        bits.append(f"{miles:,} mi")
    if adj is not None:
        bits.append(f"{'landed' if ship else 'adj'} {money(adj)}")
    bits.append(f"{r['year']} · {r['city']}, {r['state']}")
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


def daily_stats(tid, t, all_rows):
    """Per snapshot day: how many vehicles, and where the floor sat."""
    by_day = defaultdict(list)
    for r in all_rows:
        if r["target"] == tid:
            by_day[r["snapshot_date"]].append(r)
    out = []
    for d in sorted(by_day):
        display = pick_display_rows(by_day[d])
        adjs = [a for a, _ in (landed(r, t) for r in display)
                if a is not None]
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


def delisted(tid, t, all_rows, today_rows, hist):
    """Vehicles seen on an earlier day but not today — sold or pulled."""
    today_vins = {r["vin"] for r in today_rows if r["target"] == tid}
    by_vin = defaultdict(list)
    for r in all_rows:
        if r["target"] == tid and r["vin"] not in today_vins:
            by_vin[r["vin"]].append(r)
    out = []
    for vin, rows in by_vin.items():
        last_day = max(r["snapshot_date"] for r in rows)
        r = pick_display_rows([x for x in rows
                               if x["snapshot_date"] == last_day])[0]
        s = summarize((tid, vin), hist)
        adj, _ = landed(r, t)
        out.append({
            "vin": vin, "year": to_int(r["year"]), "trim": r["trim"],
            "market": r["market"], "miles": to_int(r["miles"]),
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


def listing_entry(r, t, s):
    adj, _ = landed(r, t)
    return {
        "vin": r["vin"], "year": to_int(r["year"]), "trim": r["trim"],
        "market": r["market"], "markets": r["markets"],
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


def build_outputs(today_rows, all_rows, hist):
    report = [f"# Auto Market Tracker — {TODAY}", ""]
    site = {"generated": TODAY, "markets": MARKETS, "targets": {}}
    days = sorted({r["snapshot_date"] for r in all_rows})
    prev_day = days[-2] if len(days) >= 2 else None

    for tid, t in TARGETS.items():
        t_rows = [r for r in today_rows if r["target"] == tid]
        report += [f"## {t.get('label', tid)}", ""]
        entry = {
            "label": t.get("label", tid), "note": t.get("note", ""),
            "years": t.get("years", []), "market_names": t.get("markets", []),
            "params": {
                "cents_per_mile": t.get("cents_per_mile", 0.25),
                "mileage_baseline": t.get("mileage_baseline", 20000),
                "ship_cost": t.get("ship_cost", 0),
                "min_price": t.get("min_price", MIN_PRICE_DEFAULT),
            },
            "listings": [],
            "daily": daily_stats(tid, t, all_rows),
            "gone": delisted(tid, t, all_rows, today_rows, hist),
        }
        site["targets"][tid] = entry
        if not t_rows:
            report += ["No listings found today.", ""]
            continue

        display = pick_display_rows(t_rows)
        listings = [listing_entry(r, t, summarize((tid, r["vin"]), hist))
                    for r in display]
        entry["listings"] = sorted(listings, key=lambda x: x["adj"] or 10**9)

        movers = [x for x in listings
                  if len(x["series"]) >= 2
                  and x["series"][-1][1] != x["series"][-2][1]]
        if movers:
            report += ["### Price changes", ""]
            for x in movers:
                old, new = x["series"][-2][1], x["series"][-1][1]
                report.append(f"- {money(old)} -> **{money(new)}** "
                              f"({x['city']}, {x['state']}) `{x['vin']}`")
            report.append("")

        just_gone = [g for g in entry["gone"] if g["last_seen"] == prev_day]
        if just_gone:
            report += [f"### Gone since {prev_day}", ""]
            for g in just_gone:
                report.append(
                    f"- {money(g['last_price'])} · {g['year']} · "
                    f"{g['city']}, {g['state']} · tracked "
                    f"{g['days_tracked']}d `{g['vin']}`")
            report.append("")

        for mname in t["markets"]:
            m_rows = [r for r in t_rows if r["market"] == mname]
            if not m_rows:
                report += [f"### {mname} — none found", ""]
                continue
            m_rows.sort(key=lambda r: to_int(r["price"]) or 10**9)
            seen = set()
            report += [f"### {mname} — "
                       f"{len({r['vin'] for r in m_rows})} listings", ""]
            for r in m_rows:
                if r["vin"] in seen:
                    continue
                seen.add(r["vin"])
                report.append(fmt_row(r, t, summarize((tid, r["vin"]), hist)))
                if len(seen) == 5:
                    break
            report.append("")

        best = [x for x in entry["listings"] if x["adj"] is not None][:5]
        if best:
            report += ["### Best value (landed, mileage-adjusted)", ""]
            for x in best:
                rr = next(r for r in display if r["vin"] == x["vin"])
                report.append(fmt_row(rr, t, summarize((tid, x["vin"]), hist)))
            report.append("")

    report += ["---",
               f"_{len(hist)} vehicle histories across {len(days)} "
               f"day{'s' if len(days) != 1 else ''}._"]
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


def main():
    rows = {}
    dropped = Counter()
    for tid, t in TARGETS.items():
        for mname in t["markets"]:
            raw_n = 0
            for year in t["years"]:
                for sort in SORTS:
                    for rec in fetch(mname, year, sort, t):
                        raw_n += 1
                        n = normalize(rec, tid, t, mname, dropped)
                        if n:
                            rows[(tid, mname, n["vin"])] = n
            kept = sum(1 for k in rows if k[0] == tid and k[1] == mname)
            print(f"{tid} / {mname}: {raw_n} raw -> {kept} kept")
    if dropped:
        print("Dropped: " + ", ".join(f"{k} x{v}" for k, v in dropped.items()))

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
