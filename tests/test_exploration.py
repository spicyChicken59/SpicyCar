"""Deep discovery must buy new observations without inventing query reach."""
import copy
import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("AUTODEV_API_KEY", "test-key-not-used")
import Tracking as T
import fair_collection as F


class PageRotation(unittest.TestCase):
    def setUp(self):
        self.t = copy.deepcopy(T.TARGETS["kia-ev9"])
        self.obs = {self.t["id"]: {"model": "kia/ev9", "complete": True, "queries": [
            {"source": "National", "sort": "price.asc", "page": 1, "raw": 20, "total": 200},
            {"source": "States", "sort": "price.asc", "page": 1, "raw": 20, "total": 200}]}}

    def candidates(self, state=None, attempted=None):
        return F.exploration_candidates({self.t["id"]: self.t}, state or {}, "2026-09-09",
                                        self.obs, T.sources_for, 20, attempted or set())

    def test_progress_resumes_and_query_changes_reset_it(self):
        c = self.candidates()[0]; state = {}
        F.advance_exploration(state, c, "2026-09-08", {"failed": False, "raw": 20, "total": 200}, None, 20)
        resumed = next(x for x in self.candidates(state) if x["key"] == c["key"])
        self.assertEqual(resumed["page"], 3)
        self.t["years"] = ["2027"]
        reset = next(x for x in self.candidates(state) if x["key"] == c["key"])
        self.assertEqual(reset["page"], 2)

    def test_short_page_and_shrinking_market_rewind_without_exhaustive_claim(self):
        c = self.candidates()[0]; c["page"] = 8
        state = {}
        F.advance_exploration(state, c, "2026-09-08", {"failed": False, "raw": 1, "total": 141}, None, 20)
        self.assertEqual(state["exploration"][c["key"]]["next_page"], 2)
        state["exploration"][c["key"]]["next_page"] = 30
        resumed = next(x for x in self.candidates(state) if x["key"] == c["key"])
        self.assertEqual(resumed["page"], 2)

    def test_cursor_continues_after_50_and_failure_recovers(self):
        c = self.candidates()[0]; c["page"] = 50
        state = {}
        F.advance_exploration(state, c, "2026-09-08", {"failed": False, "raw": 20, "total": 2000}, "opaque-token", 20)
        record = state["exploration"][c["key"]]
        self.assertEqual((record["next_page"], record["cursor"]), (51, "opaque-token"))
        c.update(page=51, cursor="opaque-token")
        F.advance_exploration(state, c, "2026-09-09", {"failed": True}, None, 20)
        self.assertEqual(state["exploration"][c["key"]]["next_page"], 2)
        self.assertIsNone(state["exploration"][c["key"]]["cursor"])

    def test_transport_uses_opaque_cursor_without_following_response_host(self):
        response = Mock(status_code=200)
        response.json.return_value = {"data": [], "total": 0,
                                      "links": {"next": "https://untrusted.invalid/listings?cursor=opaque-out"}}
        with patch.object(T.requests, "get", return_value=response) as http, \
             patch.object(T, "REQUEST_JOURNAL", None), patch.object(T, "REQUEST_ALLOWANCE", None), \
             patch.object(T, "CALLS", 0), patch.object(T, "SPENT", {}), patch.object(T, "NEXT_CURSORS", {}), \
             patch.object(T, "TOTALS", {}):
            T.fetch("National", None, "price.asc", 51, {**self.t, "_cursor": "opaque-in"})
            self.assertEqual(http.call_args.args[0], T.BASE)
            params = http.call_args.kwargs["params"]
            self.assertEqual(params["cursor"], "opaque-in")
            self.assertEqual(params["includes"], "total")
            self.assertNotIn("page", params)
            self.assertEqual(T.NEXT_CURSORS[(self.t["id"], "National")], "opaque-out")

    def test_empty_failed_or_already_attempted_scopes_are_not_rebought(self):
        self.assertEqual(self.candidates(attempted={(x["key"], x["page"]) for x in self.candidates()}), [])
        self.obs[self.t["id"]]["complete"] = False
        self.assertEqual(self.candidates(), [])
        self.obs[self.t["id"]]["complete"] = True
        for q in self.obs[self.t["id"]]["queries"]: q["total"] = 20
        self.assertEqual(self.candidates(), [])


class LiveLoop(unittest.TestCase):
    def drive(self, extra_calls, fail_depth=False):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            root = Path(temp); data = root/"data"; docs = root/"docs"
            data.mkdir(); docs.mkdir()
            day = "2026-09-30"
            remaining = 950 - 3 - extra_calls
            ledger = {}
            for d in range(1, 30):
                n = min(40, remaining); remaining -= n
                ledger[f"2026-09-{d:02}"] = {"actual": n}
            F.atomic_json(data/"spend.json", ledger)
            t = copy.deepcopy(T.TARGETS["kia-ev9"])
            t.update(cadence=1, model_cadence=1, offset=0)
            updates = {"TODAY":day, "TODAY_ORD":date.fromisoformat(day).toordinal(),
                "DATA":data, "DOCS":docs, "SPEND_LOG":data/"spend.json", "FETCH_LOG":data/"fetch_log.json",
                "SAMPLE":data/"sample.json", "TARGETS":{t["id"]:t}, "CALLS":0, "SPENT":{},
                "RAW_N":T.Counter(), "KEPT_N":T.Counter(), "SOURCE_VINS":{}, "TOTALS":{}, "NEXT_CURSORS":{},
                "OVERLAP":{}, "FAILED_SCOPES":set(), "EXHAUSTED":set(), "PRICE_WINDOW":{},
                "REQUEST_JOURNAL":None, "REQUEST_ALLOWANCE":None, "FAILED_FETCHES":0}
            for k,v in updates.items(): stack.enter_context(patch.object(T,k,v))
            stack.enter_context(patch.dict(os.environ, {"ALLOW_REFETCH":"", "FILL_MISSING":""}))
            stack.enter_context(patch.object(T,"load_history",return_value=[]))
            write = stack.enter_context(patch.object(T,"write_rows"))
            for name in ["save_zip_cache", "save_overlap_history", "update_sheet_size"]:
                stack.enter_context(patch.object(T,name))
            stack.enter_context(patch.object(T,"build_outputs",return_value=("report",{},"subject")))
            stack.enter_context(patch.object(T.time,"sleep"))
            stack.enter_context(patch.object(T,"normalize", side_effect=lambda rec, target, dropped:
                                            {**rec,"snapshot_date":day,"target":target["id"]}))
            params_seen = []
            def http(*args, **kwargs):
                p = kwargs["params"]; params_seen.append(dict(p))
                self.assertEqual(p["includes"], "total")
                deep = p.get("page", 1) > 1 or "cursor" in p
                if deep and fail_depth: return Mock(status_code=503, text="unavailable")
                prefix = "DEEP" if deep else "NEW" if p["sort"] == T.NEWEST_SORT else "ST" if "retailListing.state" in p else "NAT"
                price = 90000 if deep else 50000 if prefix == "NEW" else 30000 if prefix == "ST" else 20000
                rows = [{"vin":prefix+str(i), "price":price+i, "miles":10000, "year":"2025", "state":"IL"}
                        for i in range(1 if deep else 20)]
                response = Mock(status_code=200)
                response.json.return_value = {"data":rows,"total":200}
                return response
            stack.enter_context(patch.object(T.requests,"get",side_effect=http))
            cwd = Path.cwd(); os.chdir(root)
            try:
                F.run(T)
                facts = F.read_json(data/"fetch_log.json")[day][t["id"]]
                obs = F.read_json(data/"collection.json")["days"][day][t["id"]]
                self.assertEqual(len(params_seen), 3 + extra_calls)
                self.assertTrue(obs["complete"])
                self.assertEqual(obs["bonus_calls"], extra_calls)
                self.assertEqual(sum(F.read_json(data/"requests.json").values()), 950)
                self.assertTrue(all(not f["exhausted"] and not f["failed"] for f in facts.values()))
                self.assertEqual(facts["National"]["window"], 20019)
                self.assertEqual(facts["States"]["window"], 30019)
                saved = write.call_args.args[0]
                self.assertEqual(len(saved), 60 if fail_depth else 61)
                if not fail_depth:
                    deep = next(r for r in saved if r["vin"] == "DEEP0")
                    self.assertIn(":page=2", deep["via"])
                    self.assertEqual(deep["snapshot_date"], day)
            finally: os.chdir(cwd)

    def test_one_spare_call_reaches_a_deeper_page_without_touching_reserve(self):
        self.drive(1)

    def test_two_spare_calls_are_recycled_across_both_sources(self):
        self.drive(2)

    def test_failed_deep_pages_keep_successful_baseline_and_stop_at_allowance(self):
        self.drive(2, fail_depth=True)
