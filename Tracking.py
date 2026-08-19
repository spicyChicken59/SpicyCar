#!/usr/bin/env python3
"""
Daily BMW i5 eDrive40 price tracker.
Appends a snapshot per day to data/snapshots.csv (never overwrites),
then derives price-cut history per VIN and writes REPORT.md.
"""

import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------- config

API_KEY = os.environ.get("AUTODEV_API_KEY")
if not API_KEY:
    sys.exit("Missing AUTODEV_API_KEY secret")

BASE = "https://api.auto.dev/listings"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

TODAY = datetime.now(timezone.utc).date().isoformat()

DATA = Path("data")
DATA.mkdir(exist_ok=True)
SNAPSHOTS = DATA / "snapshots.csv"
SAMPLE = DATA / "sample_record.json"

MARKETS = [
    {"name": "Cincinnati",   "zip": "45202", "distance": 100},
    {"name": "Chicago",      "zip": "60601", "distance": 100},
    {"name": "Indianapolis", "zip": "46204", "distance": 75},
    {"name": "National",     "zip": None,    "distance": None},
]

TRIM_MATCH = "edrive40"     # matched case-insensitively anywhere in the record
PAGES = 3                   # 3 pages x 20 = cheapest ~60 per market
PER_PAGE = 20               # Starter plan caps ?limit= at 20

# Rough mileage normalization so a 9k-mile car isn't compared to a 34k-mile car.
MILEAGE_BASELINE = 20_000
CENTS_PER_MILE = 0.20

FIELDS = [
    "snapshot_date", "market", "vin", "year", "trim", "miles", "price",
    "dealer", "city", "state", "listed_since", "url",
]

# ---------------------------------------------------------------- helpers


def dig(obj, path):
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def first(obj, paths, default=""):
    """Try several candidate field paths; return the first non-empty one."""
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


def adjusted(price, miles):
    """Price normalized to MILEAGE_BASELINE miles. Heuristic, not gospel."""
    if price is None or miles is None:
        return price
    return int(price + (miles - MILEAGE_BASELINE) * CENTS_PER_MILE)


# ---------------------------------------------------------------- fetch


def fetch_market(market):
    rows = []
    for page in range(1, PAGES + 1):
        params = {
            "vehicle.make": "BMW",
            "vehicle.model": "i5",
            "vehicle.year": "2024-2025",
            "sort": "price.asc",
            "limit": PER_PAGE,
            "page": page,
        }
        if market["zip"]:
            params["zip"] = market["zip"]
            params["distance"] = market["distance"]

        try:
            r = requests.get(BASE, headers=HEADERS, params=params, timeout=45)
        except requests.RequestException as e:
            print(f"  ! {market['name']} p{page}: {e}")
            break

        if r.status_code != 200:
            print(f"  ! {market['name']} p{page}: HTTP {r.status_code} {r.text[:300]}")
            break

        batch = (r.json() or {}).get("data") or []
        if not batch:
            break
        rows.extend(batch)

        # Save one raw record so field paths can be verified/corrected later.
        if not SAMPLE.exists() and batch:
            SAMPLE.write_text(json.dumps(batch[0], indent=2))

    return rows


def normalize(rec, market_name):
    if TRIM_MATCH not in json.dumps(rec).lower():
        return None

    vin = first(rec, ["vehicle.vin", "vin"])
    price = to_int(first(rec, ["retailListing.price", "price"], None))
    if not vin or not price:
        return None

    miles = to_int(first(
        rec,
        ["retailListing.miles", "retailListing.mileage", "vehicle.mileage",
         "mileage", "miles"],
        None,
    ))

    return {
        "snapshot_date": TODAY,
        "market": market_name,
        "vin": vin,
        "year": first(rec, ["vehicle.year", "year"]),
        "trim": first(rec, ["vehicle.trim", "vehicle.style", "vehicle.series"]),
        "miles": miles if miles is not None else "",
        "price": price,
        "dealer": first(rec, ["retailListing.dealerName", "dealer.name",
                              "retailListing.dealer.name"]),
        "city": first(rec, ["retailListing.city", "dealer.city", "location.city"]),
        "state": first(rec, ["retailListing.state", "dealer.state", "location.state"]),
        "listed_since": str(first(rec, ["createdAt", "retailListing.createdAt"]))[:10],
        "url": first(rec, ["retailListing.vdpUrl", "retailListing.url", "url"]),
    }


# ---------------------------------------------------------------- storage


def load_history():
    if not SNAPSHOTS.exists():
        return []
    with SNAPSHOTS.open() as f:
        return list(csv.DictReader(f))


def append_rows(rows):
    new_file = not SNAPSHOTS.exists()
    with SNAPSHOTS.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------- analysis


def build_history(all_rows):
    """vin -> sorted [(date, price)] using the min price seen per day."""
    per_day = defaultdict(dict)
    for r in all_rows:
        p = to_int(r["price"])
        if p is None:
            continue
        d = r["snapshot_date"]
        cur = per_day[r["vin"]].get(d)
        per_day[r["vin"]][d] = p if cur is None else min(cur, p)
    return {vin: sorted(days.items()) for vin, days in per_day.items()}


def summarize(vin, hist):
    series = hist.get(vin, [])
    if not series:
        return {}
    prices = [p for _, p in series]
    cuts = sum(1 for a, b in zip(prices, prices[1:]) if b < a)
    return {
        "min_ever": min(prices),
        "first_price": prices[0],
        "delta": prices[-1] - prices[0],
        "cuts": cuts,
        "days_tracked": len(series),
        "first_seen": series[0][0],
    }


def money(n):
    return f"${n:,}" if isinstance(n, int) else "—"


def fmt_row(r, hist):
    s = summarize(r["vin"], hist)
    miles = to_int(r["miles"])
    adj = adjusted(to_int(r["price"]), miles)
    bits = [f"**{money(to_int(r['price']))}**"]
    if miles is not None:
        bits.append(f"{miles:,} mi")
    if adj is not None and miles is not None:
        bits.append(f"adj {money(adj)}")
    bits.append(f"{r['year']} · {r['city']}, {r['state']}")
    line = " · ".join(str(b) for b in bits)

    tags = []
    if s.get("cuts"):
        tags.append(f"↓{s['cuts']} cut{'s' if s['cuts'] > 1 else ''} "
                    f"({money(s['delta'])})")
    if s.get("days_tracked", 0) >= 30:
        tags.append(f"tracked {s['days_tracked']}d")
    if s.get("days_tracked") == 1:
        tags.append("NEW")

    out = f"- {line}"
    if tags:
        out += f"\n  _{' · '.join(tags)}_"
    if r.get("dealer"):
        out += f"\n  {r['dealer']}"
    if r.get("url"):
        out += f" — [listing]({r['url']})"
    out += f"\n  `{r['vin']}`"
    return out


def build_report(today_rows, all_rows, hist):
    lines = [f"# BMW i5 eDrive40 — {TODAY}", ""]

    if not today_rows:
        lines.append("No matching listings returned today. "
                     "Check the Actions log for API errors.")
        return "\n".join(lines)

    # --- movers: price changed since the previous snapshot
    movers = []
    for r in today_rows:
        series = hist.get(r["vin"], [])
        if len(series) >= 2 and series[-1][1] != series[-2][1]:
            movers.append((r, series[-2][1], series[-1][1]))
    if movers:
        lines += ["## 🔻 Price changes since yesterday", ""]
        for r, old, new in sorted(movers, key=lambda m: m[2] - m[1]):
            arrow = "↓" if new < old else "↑"
            lines.append(f"- {arrow} {money(old)} → **{money(new)}** "
                         f"({r['city']}, {r['state']}) `{r['vin']}`")
        lines.append("")

    # --- per-market floors
    by_market = defaultdict(list)
    for r in today_rows:
        by_market[r["market"]].append(r)

    for m in MARKETS:
        rows = by_market.get(m["name"], [])
        if not rows:
            continue
        rows.sort(key=lambda r: to_int(r["price"]) or 10**9)
        radius = f" (within {m['distance']} mi)" if m["distance"] else ""
        lines += [f"## {m['name']}{radius} — {len(rows)} listings", ""]
        for r in rows[:5]:
            lines.append(fmt_row(r, hist))
        lines.append("")

    # --- best mileage-adjusted anywhere
    scored = [(adjusted(to_int(r["price"]), to_int(r["miles"])), r)
              for r in today_rows if to_int(r["miles"]) is not None]
    scored = [s for s in scored if s[0] is not None]
    if scored:
        seen, best = set(), []
        for adj, r in sorted(scored, key=lambda s: s[0]):
            if r["vin"] in seen:
                continue
            seen.add(r["vin"])
            best.append((adj, r))
            if len(best) == 5:
                break
        lines += ["## Best value (mileage-adjusted, all markets)", ""]
        for adj, r in best:
            lines.append(fmt_row(r, hist))
        lines.append("")

    lines += [
        "---",
        f"_Adjusted price normalizes to {MILEAGE_BASELINE:,} mi at "
        f"${CENTS_PER_MILE:.2f}/mi. Rough heuristic — sanity-check it._",
        f"_{len(hist)} unique VINs tracked across "
        f"{len({r['snapshot_date'] for r in all_rows})} days._",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- email


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
            json={
                "from": "i5 tracker <onboarding@resend.dev>",
                "to": [to],
                "subject": f"i5 tracker — {TODAY}",
                "text": report,
            },
            timeout=30,
        )
        print(f"Email: HTTP {r.status_code}")
    except requests.RequestException as e:
        print(f"Email failed: {e}")


# ---------------------------------------------------------------- main


def main():
    today_rows = []
    for m in MARKETS:
        raw = fetch_market(m)
        norm = [n for n in (normalize(r, m["name"]) for r in raw) if n]
        print(f"{m['name']}: {len(raw)} raw → {len(norm)} eDrive40")
        today_rows.extend(norm)

    history_rows = [r for r in load_history() if r["snapshot_date"] != TODAY]
    if today_rows:
        append_rows(today_rows)

    all_rows = history_rows + today_rows
    hist = build_history(all_rows)

    report = build_report(today_rows, all_rows, hist)
    Path("REPORT.md").write_text(report)
    print("\n" + report)
    send_email(report)


if __name__ == "__main__":
    main()
