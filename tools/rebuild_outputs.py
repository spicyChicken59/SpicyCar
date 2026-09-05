# Offline rebuild: regenerate REPORT.md and docs/data.json from the snapshot
# history under the CURRENT targets.json — no API call, no email. Used after a
# config change so the site reflects it before the next tracker run.
import os, sys, json
os.environ.setdefault("AUTODEV_API_KEY", "offline-rebuild")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
os.chdir(__import__("pathlib").Path(__file__).resolve().parent.parent)
import Tracking as T

all_rows = T.load_history()
days = sorted({r["snapshot_date"] for r in all_rows})
latest = days[-1]
today_rows = [r for r in all_rows if r["snapshot_date"] == latest]
print(f"history: {len(all_rows)} rows over {len(days)} days; latest {latest} has {len(today_rows)} rows")
print(f"targets now: {len(T.TARGETS)}; i7 rows in history: {sum(1 for r in all_rows if 'i7' in str(r.get('target','')))}")
hist = T.build_history(all_rows)
report, site, subject = T.build_outputs(today_rows, all_rows, hist)
from pathlib import Path
Path("REPORT.md").write_text(report)
(T.DOCS / "data.json").write_text(json.dumps(site, indent=1))
print("subject:", subject)
print("brands in site:", list(site["brands"].keys()))
print("bmw models in site:", list(site["brands"]["bmw"]["models"].keys()))
print("scope_label:", site["buyer"].get("scope_label"))
print("drive keys still exported:", [k for k in site["buyer"] if "drive" in k])
