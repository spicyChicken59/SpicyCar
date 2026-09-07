# Offline rebuild: regenerate REPORT.md and docs/data.json from the snapshot
# history under the CURRENT targets.json — no API call, no email. Used after a
# config change so the site reflects it before the next tracker run.
#
# The summary below is the whole point of running this by hand: it is the only
# place a config change is described before the next fetch. It used to end with
#
#     print("bmw models in site:", list(site["brands"]["bmw"]["models"].keys()))
#
# which is a KeyError the moment BMW is not on the watchlist — reproduced by
# standing BMW down and running this: both files are written CORRECTLY and then
# the tool exits 1, so a caller reading the exit code, or a human reading the
# traceback, is told a rebuild failed that had already succeeded. Two more
# lines were the same vintage: "i7 rows in history" named one model of the
# thirty-six, and "drive keys still exported" checked for an export removed
# long enough ago that it has printed [] every run since.
#
# What replaces them is what a thirty-six-model watchlist actually needs
# answered after an edit: which models the record can say nothing about yet,
# and when each of them first fetches.
import os, sys, json
os.environ.setdefault("AUTODEV_API_KEY", "offline-rebuild")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
os.chdir(__import__("pathlib").Path(__file__).resolve().parent.parent)
from pathlib import Path
import Tracking as T

all_rows = T.load_history()
days = sorted({r["snapshot_date"] for r in all_rows})
latest = days[-1]
today_rows = [r for r in all_rows if r["snapshot_date"] == latest]
print(f"history: {len(all_rows)} rows over {len(days)} days; latest {latest} has {len(today_rows)} rows")

hist = T.build_history(all_rows)
report, site, subject = T.build_outputs(today_rows, all_rows, hist)
Path("REPORT.md").write_text(report)
(T.DOCS / "data.json").write_text(json.dumps(site, indent=1))

models = [(bk, mk, m) for bk, b in site["brands"].items()
          for mk, m in b["models"].items()]
empty = [(bk, mk, m) for bk, mk, m in models if not m.get("listings")]
print(f"subject: {subject}")
print(f"{len(site['brands'])} brands, {len(models)} models, {len(T.TARGETS)} targets"
      f" — {len(models) - len(empty)} carry listings, {len(empty)} do not")
if empty:
    # Named, not counted: after a config edit the useful question is WHICH
    # models the page will show empty and when each one first has something to
    # say. next_due is the target's own answer and is already on the sheet.
    print("  no listings yet (first fetch):")
    for bk, mk, m in sorted(empty, key=lambda x: (x[2].get("next_due") or "", x[0])):
        print(f"    {bk + '/' + mk:<30} {m.get('next_due') or 'not scheduled'}"
              f"   every {m.get('cadence')} day(s)")
today, worst, avg = T.planned_calls()
print(f"call plan: {today} today, worst day {worst} of {T.BUDGET},"
      f" ~{avg * 30.5:.0f}/month of {T.MONTHLY} over"
      # The horizon is derived now, so the article cannot be typed either.
      f" {T.a_or_an(T.plan_horizon())} {T.plan_horizon()}-day cycle")
