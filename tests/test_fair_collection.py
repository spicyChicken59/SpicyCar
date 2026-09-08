"""Economic and inventory invariants for the fair collector; no live API."""
import copy
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("AUTODEV_API_KEY", "test-key-not-used")
import Tracking as T
import fair_collection as F


class FairPlan(unittest.TestCase):
    def test_model_turns_rotate_every_trim_without_changing_model_share(self):
        for key, ts in F.groups(T.TARGETS).items():
            days = [sum(T.due_on(t, d) for t in ts) for d in range(0, 120)]
            self.assertEqual(set(days), {0, 1}, key)
            hits = [i for i, n in enumerate(days) if n]
            self.assertEqual({b-a for a, b in zip(hits, hits[1:])}, {2})
            self.assertAlmostEqual(sum(T.calls_for(t)/t["cadence"] for t in ts), 1.5)
            for t in ts:
                self.assertEqual(sum(T.due_on(t, d) for d in range(t["cadence"])), 1)

    def test_every_31_day_window_fits_with_recovery_reserve(self):
        for start in range(24):
            costs = [sum(T.calls_for(t) for t in T.TARGETS.values() if T.due_on(t, d))
                     for d in range(start, start+31)]
            self.assertLessEqual(max(costs), T.BUDGET)
            self.assertLessEqual(sum(costs), T.MONTHLY-50)

    def test_shopping_does_not_change_collection(self):
        with patch.object(T, "SHOPPING", ["kia-ev9"]):
            changed = T.build_targets()
        keys = ("cadence", "offset", "depth", "pages", "newest")
        self.assertEqual({k: [v[x] for x in keys] for k,v in T.TARGETS.items()},
                         {k: [v[x] for x in keys] for k,v in changed.items()})


class Budget(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ledger, self.journal = self.root/"spend.json", self.root/"requests.json"

    def budget(self, day="2026-09-08"):
        return F.RequestBudget(day, self.ledger, self.journal, 40, 1000)

    def test_restart_does_not_refund_or_double_count_reserved_requests(self):
        F.atomic_json(self.ledger, {"2026-09-08": {"actual": 35}})
        b = self.budget()
        for _ in range(3): self.assertTrue(b.charge())
        self.assertEqual(self.budget().remaining, 2)
        F.atomic_json(self.ledger, {"2026-09-08": {"actual": 38}})
        self.assertEqual(self.budget().remaining, 2)
        b = self.budget()
        self.assertTrue(b.charge()); self.assertTrue(b.charge())
        self.assertFalse(b.charge())
        self.assertEqual(self.budget().day_spent, 40)

    def test_month_ceiling_and_rollover(self):
        F.atomic_json(self.ledger, {"2026-09-01": {"actual": 998}})
        b = self.budget()
        self.assertTrue(b.charge()); self.assertTrue(b.charge())
        self.assertFalse(b.charge())
        self.assertEqual(self.budget("2026-10-01").remaining, 40)

    def test_corrupt_spend_fails_before_requests(self):
        for bad in [-1, True, "4", None]:
            F.atomic_json(self.ledger, {"2026-09-08": {"actual": bad}})
            with self.assertRaises(ValueError): self.budget()

    def test_bonus_preserves_future_baseline_and_reserve(self):
        F.atomic_json(self.ledger, {"2026-09-01": {"actual": 900}})
        b = self.budget()
        for _ in range(30): b.charge()
        self.assertEqual(b.bonus_remaining(20, 50, 30, 30), 0)

    def test_recycles_exhaustion_but_subtracts_each_bonus(self):
        b = self.budget("2026-09-30")
        for _ in range(20): b.charge()
        self.assertEqual(b.bonus_remaining(0, 50, 30, 20), 20)
        for _ in range(3): b.charge()
        self.assertEqual(b.bonus_remaining(0, 50, 30, 20), 17)


class Learning(unittest.TestCase):
    def test_page_count_cannot_masquerade_as_market_size(self):
        self.assertIsNone(T.envelope_total({"count": 20}, strict=True))
        self.assertIsNone(T.envelope_total({"meta": {"count": 20}}, strict=True))
        self.assertEqual(T.envelope_total({"meta": {"total": 1200}}, strict=True), 1200)

    def test_weights_stay_fixed_through_week_and_relearn_next_week(self):
        ts = {k: copy.deepcopy(v) for k,v in T.TARGETS.items()}
        state = {"weights": {}}
        F.bonus_order(ts, state, "2026-09-08", set(), {})
        old = copy.deepcopy(state["weights"])
        t = ts["kia-ev9"]
        state["markets"] = {"kia/ev9": {"total": 0, "as_of": "2026-09-09", "query": F.signature(t)}}
        F.bonus_order(ts, state, "2026-09-09", set(), {})
        self.assertEqual(old, state["weights"])
        F.bonus_order(ts, state, "2026-09-14", set(), {})
        self.assertNotEqual(old["values"], state["weights"]["values"])

    def test_counts_are_dated_broad_queries_and_never_summed_trim_totals(self):
        t = next(iter(T.TARGETS.values()))
        state = {"markets": {F.model_key(t): {"total": 100, "as_of": "2026-09-01", "query": F.signature(t)}}}
        self.assertEqual(F.fresh_market(state, F.model_key(t), t, "2026-09-08")["total"], 100)
        self.assertIsNone(F.fresh_market(state, F.model_key(t), t, "2026-09-30"))
        altered = {**t, "years": ["2020"]}
        self.assertIsNone(F.fresh_market(state, F.model_key(t), altered, "2026-09-08"))

    def test_unseen_and_overdue_win_against_giant_market(self):
        ts = list(T.TARGETS.values())
        small, large = ts[0], ts[-1]
        targets = {t["id"]: t for t in [small, large]}
        state = {"markets": {F.model_key(large): {"total": 100000000, "as_of": "2026-09-08", "query": F.signature(large)}}}
        order = F.bonus_order(targets, state, "2026-09-08", set(), {large["id"]: "2026-09-07"})
        self.assertEqual(order[0]["id"], small["id"])
        self.assertEqual(F.bonus_order(targets, state, "2026-09-08", set(targets), {}), [])

    def test_empty_complete_observation_clears_current_rows_but_failure_does_not(self):
        t = next(iter(T.TARGETS.values()))
        row = {"target": t["id"], "snapshot_date": "2026-09-07", "year": "2025"}
        for complete in [False, True]:
            log = {"2026-09-08": {t["id"]: {"National": {"observation_complete": complete}}}}
            with patch.object(T, "load_fetch_log", return_value=log):
                self.assertEqual(len(T.current_rows([row], {t["id"]})), 0 if complete else 1)


class Runtime(unittest.TestCase):
    def test_complete_empty_run_accounts_calls_and_same_day_dispatch_is_free(self):
        """Drive real orchestration/transport with mocked HTTP; write only temp data."""
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            root = Path(temp); data = root/"data"; docs = root/"docs"
            data.mkdir(); docs.mkdir()
            t = copy.deepcopy(T.TARGETS["kia-ev9"])
            t.update(cadence=1, model_cadence=1, offset=0)
            updates = {"DATA":data,"DOCS":docs,"SPEND_LOG":data/"spend.json",
                       "FETCH_LOG":data/"fetch_log.json", "SAMPLE":data/"sample.json",
                       "TARGETS":{t["id"]:t},"CALLS":0,"SPENT":{},"RAW_N":T.Counter(),
                       "KEPT_N":T.Counter(),"SOURCE_VINS":{},"TOTALS":{},"OVERLAP":{},
                       "FAILED_SCOPES":set(),"EXHAUSTED":set(),"PRICE_WINDOW":{},
                       "REQUEST_JOURNAL":None,"REQUEST_ALLOWANCE":None,"FAILED_FETCHES":0}
            for k,v in updates.items(): stack.enter_context(patch.object(T,k,v))
            stack.enter_context(patch.dict(os.environ, {"ALLOW_REFETCH":"", "FILL_MISSING":""}))
            stack.enter_context(patch.object(T,"load_history",return_value=[]))
            stack.enter_context(patch.object(T,"write_rows"))
            stack.enter_context(patch.object(T,"save_zip_cache"))
            stack.enter_context(patch.object(T,"save_overlap_history"))
            stack.enter_context(patch.object(T,"build_outputs",return_value=("report",{},"subject")))
            stack.enter_context(patch.object(T,"update_sheet_size"))
            response = unittest.mock.Mock(status_code=200)
            response.json.return_value = {"data":[], "totalCount":0}
            http = stack.enter_context(patch.object(T.requests,"get",return_value=response))
            cwd = Path.cwd(); os.chdir(root)
            try:
                F.run(T)
                self.assertEqual(http.call_count,2)  # exhausted National skips newest
                self.assertEqual(F.read_json(data/"requests.json")[T.TODAY],2)
                self.assertEqual(F.read_json(data/"spend.json")[T.TODAY]["actual"],2)
                self.assertEqual(F.read_json(data/"collection.json")["markets"]["kia/ev9"]["total"],0)
                with self.assertRaises(SystemExit) as exit_info: F.run(T)
                self.assertEqual(exit_info.exception.code,T.ALREADY_FETCHED)
                self.assertEqual(http.call_count,2)
                # A failed explicit retry cannot erase the successful empty
                # observation or refund its charges in collection metrics.
                response.status_code = 500
                response.text = "temporary failure"
                with patch.dict(os.environ, {"ALLOW_REFETCH":"1"}), patch.object(T.time,"sleep"), \
                     patch.object(T,"CALLS",0), patch.object(T,"SPENT",{}), \
                     patch.object(T,"EXHAUSTED",set()), patch.object(T,"FAILED_SCOPES",set()):
                    F.run(T)
                self.assertEqual(http.call_count,6)
                self.assertEqual(F.read_json(data/"spend.json")[T.TODAY]["actual"],6)
                recorded = F.read_json(data/"collection.json")["days"][T.TODAY][t["id"]]
                self.assertEqual(recorded["calls"],6)
                self.assertTrue(recorded["complete"])
                self.assertTrue(all(f["observation_complete"] for f in F.read_json(data/"fetch_log.json")[T.TODAY][t["id"]].values()))
            finally: os.chdir(cwd)
