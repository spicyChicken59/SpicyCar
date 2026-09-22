# What docs/data.json will weigh once the whole watchlist is fetching, and how
# it grows as the record lengthens — built, not projected.
#
# The page's whole fetch-on-load design is argued from this number, and
# tools/dashboard_smoke.mjs fails the build at 400 KB gzipped. This was written
# when the file was 92 KB with seven of thirty-six models carrying listings and
# the question was what happens when the other twenty-nine start — a question
# not to answer by multiplying: this repo's notes record two estimates of a
# sibling file disagreeing by a factor of two, settled only by building the
# real thing. Every target on the watchlist carries rows now, so the
# projections mean something again the day a model is added. Measure committed
# bytes directly and use the sheet's own compact writer for synthetic output.
#
# Synthetic rows are cloned from REAL ones, per model, so every field a real
# listing carries is present at a realistic length — a row of placeholder
# strings compresses quite differently from one holding a dealer name, a URL
# and a photo link.
import os, sys, csv, json, gzip, random, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AUTODEV_API_KEY", "offline-measure")
import Tracking as T
from sheet_transport import parse_sheet

# Two sources at one page of PER_PAGE each, minus the overlap a light target
# actually shows: the frozen window puts states_only at 88% of the States
# catch, so the two queries are nearly disjoint and a light model lands near
# the cap. Deliberately the HIGH end — a budget is checked against the worst
# case, not the median.
LIGHT_CARS = 2 * T.PER_PAGE - 2


def clone_rows(rows, target, day, n, seed):
    """n rows for `target` on `day`, cloned field-for-field from real ones."""
    rnd = random.Random(seed)
    pool = [r for r in rows if r["snapshot_date"] == day] or rows
    out = []
    for i in range(n):
        src = dict(rnd.choice(pool))
        src["target"] = target
        # a VIN is 17 chars and the page keys on it; keep the shape and the
        # length, vary the tail so rows do not dedupe
        src["vin"] = f"{target[:3].upper()}{seed:04d}{i:04d}".ljust(17, "X")[:17]
        src["snapshot_date"] = day
        out.append(src)
    return out


def measure(rows, label):
    hist = T.build_history(rows)
    days = sorted({r["snapshot_date"] for r in rows})
    today = [r for r in rows if r["snapshot_date"] == days[-1]]
    _, site, _ = T.build_outputs(today, rows, hist)
    # The writer's own serialisation, not this file's: Tracking.write_sheet()
    # sorts the keys, and the same content in two orderings is 2% apart once
    # gzipped — a projection measured the other way is not comparable with the
    # committed row beside it in the README.
    blob = T.sheet_text(site).encode()
    return summarize(site, blob, label)


def summarize(site, blob, label):
    from pathlib import Path
    loader = len(gzip.compress(Path("docs/sheet-transport.js").read_bytes(), 9))
    raw, gz = len(blob), len(gzip.compress(blob, 9)) + loader
    cars = sum(len(m.get("listings") or [])
               for b in site["brands"].values() for m in b["models"].values())
    live = sum(1 for b in site["brands"].values() for m in b["models"].values()
               if m.get("listings"))
    cap = 400 * 1024   # Tracking.SHEET_BUDGET
    print(f"  {label:<44} {live:>2} models · {cars:>4} cars · "
          f"raw {raw} bytes · Python gzip-9 data + decoder {gz} bytes ({gz/1024:.2f} KiB) · "
          f"{gz/cap:>5.1%} of budget · {cap - gz} bytes headroom"
          + ("   OVER" if gz > cap else ""))
    return gz


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0,
                    help="also project the record lengthening by N more fetch days")
    args = ap.parse_args()

    rows = T.load_history()
    days = sorted({r["snapshot_date"] for r in rows})
    newest = days[-1]
    have = {t["model_key"]: t for t in T.TARGETS.values()}
    live_models = {r["target"] for r in rows}
    empty = [t for t in T.TARGETS.values() if t["id"] not in live_models]

    print(f"record: {len(rows)} rows over {len(days)} days, newest {newest}")
    print(f"targets: {len(T.TARGETS)} · carrying rows: {len(T.TARGETS) - len(empty)} · "
          f"never fetched: {len(empty)}\n")

    from pathlib import Path
    blob = Path("docs/data.json").read_bytes()
    summarize(parse_sheet(blob), blob, "as committed (exact bytes)")
    if not empty:
        print("All active targets have observations; no unfetched-target projection is needed.")
        print("This is not a forecast of retained missing vehicles or future history growth.")
        return

    grown = list(rows)
    for i, t in enumerate(empty):
        grown += clone_rows(rows, t["id"], newest, LIGHT_CARS, seed=i + 1)
    measure(grown, f"every target fetching ({LIGHT_CARS} cars each)")

    # …and the same file once each of those targets has a price series rather
    # than a single point. A light target on the long tail fetches twice a
    # month, so a quarter is about six points.
    for extra in (2, 6):
        deeper = list(grown)
        back = days[-min(len(days), extra * 2):]
        for i, t in enumerate(empty):
            for k, day in enumerate(back[:extra]):
                deeper += clone_rows(rows, t["id"], day, LIGHT_CARS, seed=i + 1)
        measure(deeper, f"…and {extra + 1} fetches deep on each")


if __name__ == "__main__":
    main()
