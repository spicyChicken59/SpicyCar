"""SpicyCar regression suite.

Run from the repo root:  python -m unittest discover -s tests -t . -v

Two jobs. First, keep the daily run inside the free API plan — the budget
tests fail the build before a config change can overspend it. Second, hold
every bug that reached production down: each test names the failure it
guards against, so a future edit that reintroduces one gets caught in CI
instead of in a snapshot.

Importing Tracking needs AUTODEV_API_KEY set (any value) and makes no
network call as long as no home zip is configured; conftest-free, so the
key is set here before the import.
"""

import html as html_mod
import io
import json
import math
import os
import re
import shutil
import struct
import sys
import unittest
import unittest.mock
import contextlib
import copy
import io as _io
from collections import Counter
from datetime import date
from pathlib import Path

os.environ.setdefault("AUTODEV_API_KEY", "test-key-not-used")
os.environ.pop("BUYER_HOME_ZIP", None)          # keep the import offline

import Tracking as T                            # noqa: E402

_BAD = object()   # a response body that will not parse as JSON

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "records.json").read_text())
CHICAGO = (41.8855, -87.6221)
INDY = (39.7684, -86.1581)


def target(tid):
    """A real target from targets.json, so config resolution is under test too."""
    assert tid in T.TARGETS, f"{tid} missing from targets.json (targets: {sorted(T.TARGETS)})"
    return T.TARGETS[tid]


def listing(**over):
    """A normalized listing entry as the dashboard and picks see it."""
    base = {"price": 45000, "miles": 20000, "local": False, "ship": 1000,
            "accidents": 0, "usage": "Personal Use", "state": "CA"}
    base.update(over)
    return base


# --------------------------------------------------------------------------
# The free-tier invariant. These are the tests that keep the bill at zero.
# --------------------------------------------------------------------------
class TestBudget(unittest.TestCase):
    def test_projected_month_fits_the_api_plan(self):
        _, worst, avg = T.planned_calls()
        monthly = avg * 30.5
        self.assertLessEqual(
            monthly, T.MONTHLY,
            f"\nThe watchlist would use ~{monthly:,.0f} calls/month against a plan of "
            f"{T.MONTHLY:,}.\nGive a target a higher cadence, drop it to depth 'light', "
            f"or remove one.\n{len(T.TARGETS)} targets, average {avg:.1f}/day.")

    def test_no_single_day_exceeds_the_daily_cap(self):
        _, worst, _ = T.planned_calls()
        self.assertLessEqual(
            worst, T.BUDGET,
            f"\nBusiest day in the next two weeks needs {worst} calls, cap is {T.BUDGET}.")

    def test_headroom_is_reported_honestly(self):
        """A soft guard: warn in the failure message when we are near the ceiling."""
        _, _, avg = T.planned_calls()
        monthly = avg * 30.5
        self.assertLess(monthly / T.MONTHLY, 0.98,
                        f"\nAt {monthly / T.MONTHLY:.0%} of the plan there is no room for a "
                        f"re-run or a manual trigger. Keep some slack.")

    def test_every_target_is_reachable_on_its_cadence(self):
        """A target nobody ever fetches is a silent hole in the watchlist."""
        for tid, t in T.TARGETS.items():
            due = any(T.due_on(t, T.TODAY_ORD + k) for k in range(t["cadence"]))
            self.assertTrue(due, f"{tid} is never due within its own {t['cadence']}-day cycle")

    def test_shopping_ids_exist(self):
        for tid in T.SHOPPING:
            self.assertIn(tid, T.TARGETS,
                          f"buyer.shopping names {tid!r}, which is not a target")

    def test_a_models_trims_share_a_day_per_cadence(self):
        """One page, one fetch day — but only among trims that run at the same
        rate. Taking a model's offset from whichever trim was listed first gave
        the i7's every-third-day trims the CPO watch's every-second-day slot,
        and left them never claiming a place in the cadence-3 rotation: they
        landed on the Ioniq 5's and Lucid's day and pushed the worst day from
        34 to 36 of 40 while the monthly average went down."""
        # every model's trims at one cadence sit on one day...
        by_model = {}
        for t in T.TARGETS.values():
            by_model.setdefault((t["model_key"], t["cadence"]), set()).add(t["offset"])
        for (mk, cad), offsets in by_model.items():
            self.assertEqual(len(offsets), 1,
                             f"{mk} at cadence {cad} is split across days {offsets}")
        # ...and each cadence's models are dealt round-robin across its slots,
        # which is the half that broke: the i7's cadence-3 trims took a slot
        # from the cadence-2 counter, so the three-day rotation held 2/3/1
        # models instead of 2/2/2 and one day carried the extra.
        for cad in {t["cadence"] for t in T.TARGETS.values() if t["cadence"] > 1}:
            slots = Counter(off for (mk, c), (off,) in
                            ((k, tuple(v)) for k, v in by_model.items()) if c == cad)
            spread = max(slots.values()) - min(slots[i] for i in range(cad))
            self.assertLessEqual(spread, 1,
                                 f"cadence {cad} deals models unevenly: "
                                 f"{dict(slots)} over {cad} slots")

    def test_newest_pages_are_budgeted(self):
        """The newest-first fetch must be counted, or the plan lies."""
        i5 = target("bmw-i5-edrive40")
        self.assertEqual(i5["newest"], 1)
        self.assertEqual(T.calls_for(i5), 10,
                         "2 sources x (2 sorts x 2 pages + 1 newest page)")
        self.assertEqual(T.calls_for(target("bmw-i7-edrive50")), 10,
                         "the second shopped target budgets like the first")
        self.assertEqual(target("bmw-i5-xdrive40")["newest"], 0,
                         "newest is a shopped-target parameter, not a default")


# --------------------------------------------------------------------------
# Geography. The null-island bug cost a week; it gets three tests.
# --------------------------------------------------------------------------
class TestGeography(unittest.TestCase):
    def test_null_island_is_not_a_location(self):
        self.assertFalse(T.coords_ok(0, 0))
        self.assertFalse(T.coords_ok(0.0, 0.0))
        self.assertFalse(T.coords_ok(None, None))
        self.assertTrue(T.coords_ok(*INDY))

    def test_scope_reads_the_state_field_not_coordinates(self):
        """A car with unusable coordinates must still land in its own state."""
        self.assertTrue(T.in_scope({"state": "IN"}))
        self.assertTrue(T.in_scope({"state": "il"}))       # case-insensitive
        self.assertFalse(T.in_scope({"state": "CA"}))
        self.assertFalse(T.in_scope({"state": ""}))

    def test_haversine_matches_a_known_distance(self):
        miles = T.haversine(CHICAGO[0], CHICAGO[1], INDY[0], INDY[1])
        self.assertAlmostEqual(miles, 165, delta=10)       # Chicago–Indianapolis ≈ 165 mi

    def test_null_island_is_5900_miles_from_the_midwest(self):
        """The exact arithmetic that made the bug invisible: (0,0) looks far, not wrong."""
        self.assertGreater(T.haversine(INDY[0], INDY[1], 0, 0), 5000)


# --------------------------------------------------------------------------
# Drivable: state membership, nothing else. The buyer names the states and
# the line sits exactly where they drew it — a Benton Harbor car 90 miles
# away ships, the far corner of Ohio drives.
# --------------------------------------------------------------------------
class TestDrivable(unittest.TestCase):
    def test_listing_entries_carry_the_cars_public_coordinates(self):
        """The dashboard map needs each car's own location — public data the
        committed CSV already carries; never anything home-derived."""
        r = {k: "" for k in T.FIELDS}
        r.update({"target": "bmw-i5-edrive40", "vin": "V", "year": "2024",
                  "trim": "eDrive40", "price": 45000, "state": "WI",
                  "lat": 43.07306, "lon": -89.40123})
        e = T.listing_entry(r, {"series": []})
        self.assertAlmostEqual(e["lat"], 43.07306)
        self.assertAlmostEqual(e["lon"], -89.40123)
        r2 = dict(r, lat="", lon="")
        e2 = T.listing_entry(r2, {"series": []})
        self.assertIsNone(e2["lat"])

    def test_wisconsin_is_a_buyer_state(self):
        self.assertIn("WI", T.STATES)
        self.assertTrue(T.in_scope({"state": "WI"}))

    def test_drivable_is_state_membership_and_nothing_else(self):
        """The drive-hours radius was removed on purpose: the buyer names the
        states, and the line sits exactly where they drew it. A car ninety
        miles away across a state line ships; the far corner of a listed
        state drives."""
        near_mi = {"state": "MI", "distance": 90}       # Benton Harbor-ish
        self.assertFalse(T.in_scope(near_mi))
        self.assertGreater(T.ship_for(near_mi), 0)
        far_oh = {"state": "OH", "distance": 350}       # eastern Ohio
        self.assertTrue(T.in_scope(far_oh))
        self.assertEqual(T.ship_for(far_oh), 0)
        self.assertFalse(hasattr(T, "DRIVE_RADIUS"),
                         "the radius concept should be gone, not just unused")

    def test_beyond_the_states_pays_shipping(self):
        msp = {"state": "MN", "distance": 400}          # Twin Cities-ish
        self.assertFalse(T.in_scope(msp))
        self.assertGreater(T.ship_for(msp), 0)

    def test_the_configured_state_list_is_the_whole_rule(self):
        for st in T.STATES:
            self.assertTrue(T.in_scope({"state": st}))
            self.assertTrue(T.in_scope({"state": st.lower()}))   # case-blind
        self.assertFalse(T.in_scope({"state": ""}))
        self.assertFalse(T.in_scope({}))

    def test_a_car_with_no_location_falls_back_to_the_state_list(self):
        """No coordinates and no stored distance: only the state field decides."""
        self.assertFalse(T.in_scope({"state": "MI"}))
        self.assertEqual(T.ship_for({"state": "MI"}), T.to_int(T.BUYER.get("ship_cost")))

    def test_search_states_widen_the_query_without_duplicates(self):
        for st in T.STATES:
            self.assertIn(st, T.SEARCH_STATES)
        self.assertIn("MI", T.SEARCH_STATES)
        self.assertEqual(len(T.SEARCH_STATES), len(set(T.SEARCH_STATES)))

    def test_published_distances_are_coarse(self):
        """Distances go into public outputs; exact values could be
        triangulated back to the home zip, so they are rounded to 25."""
        old_home = T.HOME
        T.HOME = CHICAGO
        try:
            d = T.dist_home(INDY[0], INDY[1])
            self.assertEqual(d % 25, 0)
            self.assertAlmostEqual(d, 165, delta=25)
            self.assertGreaterEqual(T.dist_home(CHICAGO[0], CHICAGO[1]), 25)
        finally:
            T.HOME = old_home


# --------------------------------------------------------------------------
# Money. "Landed below asking" confused a real reader; it must be impossible.
# --------------------------------------------------------------------------
class TestMoney(unittest.TestCase):
    def test_asking_plus_shipping_is_never_below_asking(self):
        for ship in (0, 350, 1200):
            total = T.adjusted(40000, ship)
            self.assertGreaterEqual(
                total, 40000,
                f"asking 40000 + ship {ship} came to {total}")

    def test_no_mileage_allowance_can_reach_a_displayed_total(self):
        """The knob that could reach one is gone, and this is what it cost:
        with buyer.cents_per_mile on, fmt_row() printed "$36,479 · + $1,031
        shipping = $42,048" while the dashboard, which drops the adjusted value
        on purpose, showed $37,510 for the same VIN. The allowance that ranks
        the picks lives under buyer.picks and never prints."""
        import inspect
        was = {"cents_per_mile": 0.30, "mileage_baseline": 20000}
        with unittest.mock.patch.dict(T.BUYER, was, clear=False):
            self.assertEqual(T.adjusted(40000, 1200), 41200,
                             "a buyer-level mileage allowance must not reach it")
        # Structurally, not by configuration: the function cannot take a
        # mileage at all, so no config can put one into a printed total.
        self.assertEqual(list(inspect.signature(T.adjusted).parameters), ["price", "ship"])
        self.assertNotIn("cents_per_mile", inspect.getsource(T.adjusted).split('"""')[-1])
        self.assertEqual(T.to_float(T.BUYER.get("cents_per_mile")) or 0, 0,
                         "buyer.cents_per_mile must be 0 so displayed totals equal "
                         "asking + shipping")

    def test_in_state_cars_never_pay_shipping(self):
        self.assertEqual(T.ship_for({"state": "IL", "distance": 900}), 0)
        self.assertEqual(T.ship_for({"state": "WI", "distance": 900}), 0)
        self.assertEqual(T.ship_for({"state": "IN", "lat": 39.7, "lon": -86.1}), 0)

    def test_undrivable_shipping_scales_with_distance_and_has_a_floor(self):
        """Both cars sit beyond the drive radius, so both pay shipping."""
        near = T.ship_for({"state": "MN", "distance": 400})
        far = T.ship_for({"state": "CA", "distance": 1800})
        self.assertGreaterEqual(near, T.to_int(T.BUYER.get("ship_min")) or 0)
        self.assertGreater(far, near)
        self.assertLess(far, 3000, "a cross-country estimate should stay plausible")

    def test_unlocatable_cars_fall_back_to_the_flat_rate(self):
        self.assertEqual(T.ship_for({"state": "CA"}),
                         T.to_int(T.BUYER.get("ship_cost")))


# --------------------------------------------------------------------------
# normalize(): the field paths and filters that took several days to get right.
# --------------------------------------------------------------------------
class TestNormalize(unittest.TestCase):
    def setUp(self):
        self.dropped = Counter()
        self._real_zip = T.zip_coords
        T.zip_coords = lambda z, cache=True: (39.9612, -82.9988)   # Columbus, no network

    def tearDown(self):
        T.zip_coords = self._real_zip

    def norm(self, key, tid):
        rec = {k: v for k, v in FIXTURES[key].items() if not k.startswith("_")}
        return T.normalize(rec, target(tid), self.dropped)

    def test_dealer_and_url_come_from_the_real_fields(self):
        row = self.norm("awkward_field_paths", "bmw-i5-edrive40")
        self.assertIsNotNone(row)
        self.assertEqual(row["dealer"], "Rosen Nissan of Madison")
        self.assertTrue(row["url"].startswith("https://rosennissanmadison.com"))

    def test_state_is_upper_cased_for_scope_matching(self):
        row = self.norm("null_island", "bmw-i5-edrive40")
        self.assertEqual(row["state"], "IN")
        self.assertTrue(T.in_scope(row))

    def test_null_island_row_is_geocoded_from_its_zip(self):
        row = self.norm("null_island", "bmw-i5-edrive40")
        self.assertNotEqual(row["lat"], 0, "0,0 must never survive into a row")
        self.assertTrue(T.coords_ok(T.to_float(row["lat"]), T.to_float(row["lon"])))

    def test_below_min_price_is_dropped(self):
        self.assertIsNone(self.norm("too_cheap", "bmw-i5-edrive40"))
        self.assertEqual(self.dropped["below min_price"], 1)

    def test_out_of_range_year_is_dropped(self):
        self.assertIsNone(self.norm("wrong_year", "bmw-i5-edrive40"))
        self.assertEqual(self.dropped["year out of range"], 1)

    def test_trim_match_ignores_dealer_names_and_urls(self):
        """An M60 sold by 'eDrive40 Motors' is not an eDrive40."""
        self.assertIsNone(self.norm("trim_mismatch_decoy", "bmw-i5-edrive40"))
        self.assertEqual(self.dropped["trim mismatch"], 1)

    def test_a_clean_record_keeps_every_column(self):
        row = self.norm("clean", "bmw-i5-m60")
        self.assertIsNotNone(row)
        for field in T.FIELDS:
            self.assertIn(field, row, f"normalize dropped the {field} column")
        self.assertEqual(row["price"], 60999)
        self.assertEqual(row["miles"], 15922)
        self.assertEqual(row["listed_since"], "2026-08-09")

    # ---- the CPO watch's post-fetch filters. They live in normalize, not in
    # the query, because the API's filter surface for cpo and mileage is
    # unverified — a silently-ignored param would track the wrong market.
    # The clean fixture is an M60; the watch shops eDrive40/xDrive40 only,
    # so the helper re-trims it and the M-exclusion gets its own test below.
    def cpo_norm(self, mutate):
        import copy
        rec = copy.deepcopy({k: v for k, v in FIXTURES["clean"].items()
                             if not k.startswith("_")})
        rec["vehicle"]["trim"] = "eDrive40"
        rec["vehicle"]["series"] = "eDrive40 4dr Sedan (electric DD)"
        mutate(rec)
        return T.normalize(rec, target("bmw-i5-cpo"), self.dropped)

    def test_cpo_watch_excludes_the_m_trims(self):
        # 'edrive and xdrive is sufficient': an M60 — the clean fixture as
        # shipped, certified or not — is not in the promo shopping pool
        import copy
        rec = copy.deepcopy({k: v for k, v in FIXTURES["clean"].items()
                             if not k.startswith("_")})
        rec["retailListing"]["cpo"] = True
        self.assertIsNone(T.normalize(rec, target("bmw-i5-cpo"), self.dropped))
        self.assertEqual(self.dropped["trim mismatch"], 1)
        # Read from the config rather than TARGETS: the i7 watch is stood
        # down, and this rule has to survive the day it comes back.
        i7cpo = json.loads(Path("targets.json").read_text())["watchlist"]["bmw"]["models"]["i7"]["trims"]["cpo"]
        self.assertNotIn("m70", i7cpo["trim_query"].lower())
        self.assertEqual(i7cpo["trim_exclude"], "m70",
                         "the i7 M70 is spelled with xDrive, so the query "
                         "alone cannot keep it out — trim_exclude must")

    def test_trim_exclude_keeps_a_target_off_its_sibling(self):
        """bmw-ix-m matches '' — every iX — and relies on excluding 'xdrive'.
        Lose the branch and it quietly absorbs the xDrive's whole market under
        its own name, its median and its picks included. The trim gates run
        before year and price, so a record only has to carry the trim words.

        The Lucid Air used to be the second live example, matching 'touring'
        against a Grand Touring it had to exclude. One EV per brand made the
        Air one trimless target, so the pair went with it; the loop below is
        over whatever the config still separates this way, so the next target
        that needs an exclusion is covered the day it is added rather than
        when somebody remembers this test.
        """
        import copy

        def rec_with(trim):
            r = copy.deepcopy({k: v for k, v in FIXTURES["clean"].items() if not k.startswith("_")})
            r["vehicle"]["trim"] = trim
            r["vehicle"]["series"] = trim
            return r
        excluders = [t for t in T.TARGETS.values() if t.get("trim_exclude")]
        self.assertTrue(excluders, "no live target relies on trim_exclude")
        for n, t in enumerate(excluders, start=1):
            word = t["trim_exclude"]
            self.assertIsNone(T.normalize(rec_with(word), t, self.dropped), t["id"])
            self.assertEqual(self.dropped["trim excluded"], n,
                             f"{t['id']} must refuse a car whose trim reads "
                             f"{word!r}, whatever else it is")
            # …and the same word passes the trim gates of a sibling that does
            # NOT exclude it, so the drop is the exclusion's doing and not a
            # mismatch on the way in.
            sibs = [o for o in T.TARGETS.values()
                    if o["model_key"] == t["model_key"] and o["id"] != t["id"]
                    and not o.get("trim_exclude")
                    and o.get("trim_match", "") in word.lower()]
            self.assertTrue(sibs, f"{t['id']} excludes {word!r} and no sibling claims it")
            kept = Counter()
            T.normalize(rec_with(word), sibs[0], kept)
            self.assertEqual(kept["trim excluded"], 0, sibs[0]["id"])
            self.assertEqual(kept["trim mismatch"], 0, sibs[0]["id"])

    def test_cpo_watch_drops_the_uncertified(self):
        # the clean fixture is cpo: false as shipped
        self.assertIsNone(self.cpo_norm(lambda r: None))
        self.assertEqual(self.dropped["not certified"], 1)

    def test_cpo_watch_drops_at_and_over_the_mileage_cap(self):
        def certify_at(miles):
            def m(r):
                r["retailListing"]["cpo"] = True
                r["retailListing"]["miles"] = miles
            return m
        self.assertIsNone(self.cpo_norm(certify_at(30000)),
                          "'under 30,000' excludes 30,000 itself")
        self.assertIsNone(self.cpo_norm(certify_at(45000)))
        self.assertEqual(self.dropped["at/over max_miles"], 2)

    def test_cpo_watch_drops_unknown_mileage(self):
        def m(r):
            r["retailListing"]["cpo"] = True
            del r["retailListing"]["miles"]
        self.assertIsNone(self.cpo_norm(m),
                          "unknown mileage cannot prove 'under the cap'")

    def test_cpo_watch_keeps_a_certified_low_mile_car(self):
        def m(r):
            r["retailListing"]["cpo"] = True
        row = self.cpo_norm(m)
        self.assertIsNotNone(row)
        self.assertEqual(row["target"], "bmw-i5-cpo")
        self.assertEqual(row["cpo"], "1")
        self.assertEqual(row["miles"], 15922)


# --------------------------------------------------------------------------
# Spicy picks: eligibility, and the within-model scoring that stops a cheap
# model from winning simply for being cheap.
# --------------------------------------------------------------------------
class TestPicks(unittest.TestCase):
    def test_rental_and_fleet_usage_is_recognised(self):
        for usage in ("Rental Use", "Corporate Fleet", "Corporate Use",
                      "Commercial Use", "Taxi Use", "Multiple Use"):
            self.assertTrue(T.is_rental({"usage": usage}), f"{usage} should count as non-personal")
        for usage in ("Personal Use", "Lease", ""):
            self.assertFalse(T.is_rental({"usage": usage}), f"{usage} should not")

    def test_eligibility_excludes_high_miles_accidents_and_rentals(self):
        self.assertTrue(T.pick_eligible(listing()))
        self.assertFalse(T.pick_eligible(listing(miles=90000)))
        self.assertFalse(T.pick_eligible(listing(accidents=1)))
        self.assertFalse(T.pick_eligible(listing(usage="Rental Use")))
        self.assertFalse(T.pick_eligible(listing(price=None)))
        self.assertFalse(T.pick_eligible(listing(miles=None)))

    def test_value_prices_miles_but_display_price_does_not_change(self):
        low = T.pick_value(listing(miles=5000))
        high = T.pick_value(listing(miles=45000))
        self.assertLess(low, high, "more miles must score worse at the same asking price")

    def test_in_state_cars_are_not_charged_shipping_in_the_score(self):
        self.assertLess(T.pick_value(listing(local=True, ship=1200)),
                        T.pick_value(listing(local=False, ship=1200)))

    def test_scoring_is_within_model_not_across_models(self):
        """A cheap model must not sweep the picks; each car is judged against its own.

        The cheap model is tightly priced (its best car is only 3.6% under its own
        median); the dear model has one genuine bargain (29% under). The bargain
        must win despite costing more than twice as much.
        """
        cheap = [listing(price=p, miles=20000)
                 for p in (19000, 19500, 19800, 20000, 20000, 20200, 20500, 20800, 21000)]
        dear = [listing(price=p, miles=20000)
                for p in (50000, 70000, 70500, 71000, 72000, 72500, 73000, 73500, 74000)]
        best_cheap = T.score_picks(cheap, "Cheap Model")[0]
        best_dear = T.score_picks(dear, "Dear Model")[0]
        self.assertLess(best_cheap["pick_pct"], 0.10)
        self.assertGreater(best_dear["pick_pct"], 0.20)
        self.assertGreater(best_dear["pick_pct"], best_cheap["pick_pct"],
                           "the biggest discount relative to its own model should win, "
                           "even though it is the more expensive car")

    def test_a_pick_says_which_cohort_and_how_many(self):
        """"21% under typical" is not a claim until it says what typical, and
        out of how many. The same percentage means one thing against 23 cars of
        the car's own trim and model year and quite another against a whole
        model's blended median — on the iX that median is six M60s and one
        xDrive50, and a car 24% under it sits 18% ABOVE a typical xDrive50."""
        rows = ([listing(price=40000, miles=20000, trim="eDrive40", year=2024) for _ in range(6)]
                + [listing(price=52000, miles=20000, trim="M60", year=2024) for _ in range(6)]
                + [listing(price=60000, miles=20000, trim="M60", year=2023)])
        by_vin = {}
        for i, r in enumerate(rows):
            r["vin"] = f"V{i:02d}"
        scored = {p["vin"]: p for p in T.score_picks(rows, "BMW i5")}
        # a car with five siblings of its own trim AND year is judged on those
        own = scored["V00"]
        self.assertEqual(own["pick_basis"], "trim")
        self.assertEqual(own["pick_n"], 6, "its own trim-and-year cohort, itself included")
        self.assertEqual(own["pick_trim"], "eDrive40")
        self.assertEqual(own["pick_year"], "2024")
        # the lone 2023 M60 has neither a trim-year nor a year cohort of six,
        # so it falls back to the whole model — and says so
        lone = scored["V12"]
        self.assertEqual(lone["pick_basis"], "model")
        self.assertEqual(lone["pick_n"], len(rows))
        self.assertEqual(lone["pick_trim"], "", "no trim cohort to name")

    def test_the_report_prints_the_cohort_size_beside_the_percentage(self):
        rows = [listing(price=40000 + i * 100, miles=20000, trim="eDrive40",
                        year=2024, vin=f"V{i:02d}", local=True) for i in range(9)]
        p = T.score_picks(rows, "BMW i5")[0]
        line = T.fmt_pick(p)
        self.assertIn("% under typical for a 2024 BMW i5 eDrive40", line)
        self.assertIn("from 9 such cars", line)

    def test_a_thin_pool_produces_no_picks(self):
        self.assertEqual(T.score_picks([listing(), listing()], "Two Cars"), [])
        # Five is the largest n at which no distribution-free interval for a
        # median exists, so five cars have a median with no error bar and are
        # not a cohort; six are the first sample that is its own interval.
        self.assertEqual(T.score_picks([listing(price=40000 + i) for i in range(5)], "Five"), [])
        self.assertEqual(len(T.score_picks([listing(price=40000 + i) for i in range(6)], "Six")), 6)

    def test_scoring_uses_the_model_year_cohort_when_it_is_big_enough(self):
        """A 2023 car must be judged against 2023 prices, not a median that
        blends in far dearer 2026 cars."""
        old = [listing(price=p, year="2023") for p in (58000, 59000, 60000, 60000, 61000, 62000)]
        new = [listing(price=p, year="2026") for p in (118000, 119000, 120000, 120000, 121000, 122000)]
        scored = T.score_picks(old + new, "i7")
        mid_old = next(p for p in scored if p["price"] == 60000)
        self.assertLess(abs(mid_old["pick_pct"]), 0.05,
                        "the median 2023 car is typical for 2023, not 50% under")
        self.assertEqual(mid_old["pick_year"], "2023")

    def test_scoring_prefers_the_trim_cohort_when_it_is_big_enough(self):
        """A cheapest-trim car must be judged against its own trim, not a
        median blended with the model's six-figure flagship trim."""
        base = [listing(price=p, year="2023", trim="eDrive50")
                for p in (58000, 59000, 60000, 60000, 61000, 62000)]
        flag = [listing(price=p, year="2023", trim="M70")
                for p in (118000, 119000, 120000, 120000, 121000, 122000)]
        scored = T.score_picks(base + flag, "BMW i7")
        mid = next(p for p in scored if p["price"] == 60000)
        self.assertLess(abs(mid["pick_pct"]), 0.05,
                        "a median eDrive50 is typical for eDrive50s, not 45% under")
        self.assertEqual(mid["pick_year"], "2023")
        self.assertEqual(mid["pick_trim"], "eDrive50")

    def test_a_thin_trim_falls_back_to_the_year_cohort(self):
        pool = ([listing(price=p, year="2023", trim="xDrive60")
                 for p in (60000, 60500, 61000, 61500, 62000)]   # five: no trim cohort
                + [listing(price=61000, year="2023", trim="eDrive50")])
        scored = T.score_picks(pool, "BMW i7")
        self.assertTrue(all(p["pick_trim"] == "" for p in scored))
        self.assertTrue(all(p["pick_year"] == "2023" for p in scored))

    def test_trim_display_drops_model_words(self):
        self.assertEqual(T.trim_disp("BMW i7", "i7 xDrive60"), "xDrive60")
        self.assertEqual(T.trim_disp("BMW i5", "eDrive40"), "eDrive40")

    def test_a_thin_year_falls_back_to_the_model_median(self):
        pool = ([listing(price=p, year="2024") for p in (40000, 41000, 42000, 42000, 43000, 44000)]
                + [listing(price=39000, year="2022")])
        scored = T.score_picks(pool, "M")
        lone = next(p for p in scored if p["price"] == 39000)
        self.assertEqual(lone["pick_year"], "", "one 2022 car is not a cohort")
        self.assertGreater(lone["pick_pct"], 0, "still judged against the model")

    def test_a_pick_must_sit_under_typical(self):
        """A thin drivable pool must not promote above-median cars to picks —
        and "under typical" means under the low edge of the cohort's own
        interval, not under its median. Nine cars: the 95% interval on the
        median is the 2nd and 8th VALUES, $45,000–$48,000 (every fixture car
        carries $1,000 of shipping). The $40,000 car is below it and is a
        pick; the $44,000 car is 3% under the median, and in THIS cohort's
        spread 3% sits inside the sampling error of the median — it depends
        on the spread, not on nine — so the page cannot tell it from typical
        and it is not a pick."""
        scored = T.score_picks([listing(price=p) for p in
                                (40000, 44000, 44500, 45000, 45500, 46000, 46500, 47000, 48000)], "M")
        by = {p["price"]: p for p in scored}
        self.assertEqual((by[44000]["pick_lo"], by[44000]["pick_hi"]), (45000, 48000),
                         "the interval is on the VALUES, which carry $1,000 of shipping")
        self.assertGreater(by[44000]["pick_pct"], 0, "under the median…")
        self.assertEqual(by[44000]["pick_stand"], "typical", "…but not distinguishable from it")
        self.assertEqual(by[40000]["pick_stand"], "under")
        picks = T.choose_picks(scored, 4)
        self.assertEqual([p["price"] for p in picks], [40000],
                         "one pick: the car the interval can defend, and not the 3%-under car")
        self.assertTrue(all(p["pick_pct"] > 0 for p in picks),
                        "no car at or above its model's typical value may be a pick")

    def test_six_to_eight_cars_can_call_nothing_under_or_over(self):
        """At six, seven and eight cars the 95% interval on the median is the
        whole sample — min to max — so the cheapest car of eight sits ON the
        low edge, not below it, and the word "under" has no support. The same
        eight plus one more car is the first sample whose interval leaves the
        extremes outside it."""
        cheap_and_seven = [listing(price=30000)] + [listing(price=p) for p in
                                                    (44000, 44500, 45000, 45500, 46000, 46500, 47000)]
        eight = {p["price"]: p for p in T.score_picks(cheap_and_seven, "M")}
        self.assertEqual((eight[30000]["pick_lo"], eight[30000]["pick_hi"]), (31000, 48000),
                         "min to max, in values")
        self.assertEqual(eight[30000]["pick_stand"], "typical",
                         "a third under the median of eight, and still not a claim")
        self.assertEqual(T.choose_picks(list(eight.values()), 4), [])
        nine = {p["price"]: p for p in T.score_picks(cheap_and_seven + [listing(price=47500)], "M")}
        self.assertEqual(nine[30000]["pick_stand"], "under")
        self.assertEqual([p["price"] for p in T.choose_picks(list(nine.values()), 4)], [30000])

    def test_a_margin_that_rounds_to_nothing_is_not_a_stand(self):
        """Eight cars at $47,000 and one at $46,900: the ninth is below the
        low edge of the interval (which is $47,000 in values), so the WORD
        "under" would hold — and every surface prints the rounded margin
        beside the word, which here is "0% under typical". A number with no
        content is not printed; the car is called typical. At $46,000 the
        same car is 2% under and stands under."""
        tight = [listing(price=47000) for _ in range(8)]
        edge = {p["price"]: p for p in T.score_picks(tight + [listing(price=46900)], "M")}
        self.assertLess(edge[46900]["pick_pct"], 0.005)
        self.assertEqual(edge[46900]["pick_stand"], "typical")
        self.assertEqual(T.choose_picks(list(edge.values()), 4), [], "and not a pick")
        clear = {p["price"]: p for p in T.score_picks(tight + [listing(price=46000)], "M")}
        self.assertEqual(clear[46000]["pick_stand"], "under")
        self.assertIn("2% under typical", T.fmt_pick(clear[46000]))

    def test_and_the_same_floor_holds_on_the_over_side(self):
        """The mirror, which nothing held: the over half of the same ternary
        could be moved anywhere with the suite green. "over" is printed on the
        shortlist row and the decision tile, and 0.2% over is the same number
        with no content that 0.2% under is."""
        tight = [listing(price=47000) for _ in range(8)]
        edge = {p["price"]: p for p in T.score_picks(tight + [listing(price=47100)], "M")}
        self.assertLess(edge[47100]["pick_pct"], 0,
                        "the precondition: this car is dearer than the median, "
                        "so it is the over branch that decides it")
        self.assertLess(abs(edge[47100]["pick_pct"]), 0.005)
        self.assertEqual(edge[47100]["pick_stand"], "typical",
                         "above the high edge, and by less than half a percent")
        clear = {p["price"]: p for p in T.score_picks(tight + [listing(price=49000)], "M")}
        self.assertEqual(clear[49000]["pick_stand"], "over",
                         "…and a real margin over it still stands over")

    def test_the_walk_skips_a_typical_car_rather_than_stopping_at_it(self):
        """Picks are walked in margin order. A car sitting ON a wide interval's
        low edge can carry a bigger margin than a car below a narrow one — two
        $12,000 cars beside six at $50,000 are 76% under that median and still
        "typical", because at nine cars the low edge IS the second value — so a
        walk that stopped at the first non-pick would return nothing while a
        genuine pick waited behind it. The walk skips."""
        wide = ([listing(price=12000, vin="A1"), listing(price=12000, vin="A2")]
                + [listing(price=50000, vin=f"A{i}") for i in range(3, 9)] + [listing(price=60000, vin="A9")])
        narrow = [listing(price=40000, vin="B1")] + [listing(price=44000 + 500 * i, vin=f"B{i + 2}") for i in range(8)]
        scored = T.score_picks(wide, "A") + T.score_picks(narrow, "B")
        by = {p["vin"]: p for p in scored}
        self.assertEqual(by["A1"]["pick_stand"], "typical", "on the edge, not below it")
        self.assertGreater(by["A1"]["pick_pct"], by["B1"]["pick_pct"], "…and ahead of the real pick in margin order")
        self.assertEqual(by["B1"]["pick_stand"], "under")
        self.assertEqual([p["vin"] for p in T.choose_picks(scored, 4)], ["B1"])
        local, ship = T.split_picks(scored, 4)
        self.assertEqual([p["vin"] for p in local + ship], ["B1"])

    def test_a_car_above_its_interval_stands_over(self):
        """Both directions, because the shortlist row and the decision tile
        print "over" as well: a car is over typical only above the high edge,
        and a car just above the median is "typical", not "1% over"."""
        scored = {p["price"]: p for p in T.score_picks([listing(price=p) for p in
                  (40000, 44000, 44500, 45000, 45500, 46000, 46500, 47000, 60000)], "M")}
        self.assertEqual(scored[60000]["pick_stand"], "over")
        self.assertEqual(scored[46000]["pick_stand"], "typical")
        self.assertLess(scored[46000]["pick_pct"], 0, "above the median, and still typical")

    def test_the_report_prints_the_margin_only_where_the_interval_supports_it(self):
        """The page and the report gate on the same word. A new car 3% under a
        median of nine sits inside that median's own interval, so the "New
        today" line prints the car and not a percentage; the same car below
        the low edge prints both."""
        x = listing(price=44200, vin="N" * 17, city="Plano", state="TX", first_seen=T.TODAY)
        inside = {**x, "pick_pct": 0.029, "pick_under": 1300, "pick_stand": "typical"}
        below = {**x, "pick_pct": 0.029, "pick_under": 1300, "pick_stand": "under"}
        self.assertNotIn("under typical", T.fmt_new(x, inside))
        self.assertIn("3% under typical ($1,300 less)", T.fmt_new(x, below))
        # …and the shortlist's tag, which reads the same stand
        old = dict(T.SHORTLIST)
        T.SHORTLIST.clear()
        T.SHORTLIST.update({"N" * 17: ""})
        try:
            live = {"N" * 17: (x, "BMW i5")}
            self.assertNotIn("under typical", "\n".join(T.shortlist_section(live, {}, {"N" * 17: inside}, T.TODAY)))
            self.assertIn("3% under typical", "\n".join(T.shortlist_section(live, {}, {"N" * 17: below}, T.TODAY)))
        finally:
            T.SHORTLIST.clear()
            T.SHORTLIST.update(old)

    def test_the_shortlist_names_the_cohort_its_margin_was_measured_against(self):
        """"25% under typical" is the same claim the picks block prints as
        "25% under typical for a 2024 BMW i5 M60 ($14,926 less, from 24 such
        cars)". The arrivals block was swept for exactly this — "the day's best
        arrival names what it beat" — and the shortlist, which is the cars
        actually being decided on and opens the report, was not. One phrase,
        one function, three places."""
        cars = [listing(price=p, vin=f"S{i:016d}", year=2024, trim="M60")
                for i, p in enumerate((40000, 44000, 44500, 45000, 45500,
                                       46000, 46500, 47000, 47500))]
        scored = {p["vin"]: p for p in T.score_picks(cars, "BMW i5")}
        pick = scored["S" + "0" * 15 + "0"]
        self.assertEqual(pick["pick_stand"], "under", "the precondition: a real margin")
        was = dict(T.SHORTLIST)
        T.SHORTLIST.clear(); T.SHORTLIST.update({pick["vin"]: ""})
        try:
            line = "\n".join(T.shortlist_section({pick["vin"]: (cars[0], "BMW i5")},
                                                 {}, scored, T.TODAY))
        finally:
            T.SHORTLIST.clear(); T.SHORTLIST.update(was)
        cohort = T.cohort_of(pick)
        self.assertTrue(cohort, "the fixture gives this car a cohort to be measured against")
        self.assertIn(f"under typical for a {cohort}", line,
                      "the shortlist must name it, as the picks block does")
        self.assertIn(T.from_n(pick).strip(), line,
                      "…and how many cars are in it")
        # The same words as the block twenty-five lines below it.
        self.assertIn(f"under typical for a {cohort}", T.fmt_pick(pick))

    def test_and_prints_no_cohort_where_there_is_none_to_print(self):
        """cohort_of() falls back trim -> year -> model and stamps only what it
        used; where it can say nothing the clause is dropped rather than
        rendered with a hole in it."""
        x = listing(price=44200, vin="H" * 17)
        bare = {**x, "pick_pct": 0.06, "pick_under": 1300, "pick_stand": "under",
                "pick_n": 0, "pick_basis": None, "pick_year": None, "pick_trim": None}
        was = dict(T.SHORTLIST)
        T.SHORTLIST.clear(); T.SHORTLIST.update({"H" * 17: ""})
        try:
            line = "\n".join(T.shortlist_section({"H" * 17: (x, "BMW i5")}, {},
                                                 {"H" * 17: bare}, T.TODAY))
        finally:
            T.SHORTLIST.clear(); T.SHORTLIST.update(was)
        self.assertIn("6% under typical", line)
        self.assertNotIn("for a  ", line)
        self.assertNotIn("such cars", line)

    def test_the_days_best_new_car_is_measured_against_its_interval(self):
        """End to end: nine eDrive40s, eight seen yesterday and one first seen
        today at $44,200 — 3% under the median of nine and exactly ON the low
        edge of its 95% interval ($44,200–$47,000). On the edge counts as
        inside: the word needs strictly below, and this fixture is one of the
        pins of that strict "<" — re-pin it to $44,300 and the kill is lost.
        The "## Today" line counts the new car and must not call it the day's
        best value; a page saying "best 3% under typical" there would be
        reading noise as a bargain."""
        def row(vin, day, price):
            r = {k: "" for k in T.FIELDS}
            r.update({"target": "bmw-i5-edrive40", "vin": vin, "snapshot_date": day,
                      "price": price, "year": "2024", "trim": "eDrive40", "miles": 20000,
                      "state": "IL", "city": "Chicago"})
            return r
        old_day = date.fromordinal(T.TODAY_ORD - 1).isoformat()
        prices = (44000, 44500, 45000, 45500, 46000, 46500, 47000, 47500)
        rows = [row(f"OLD{i:014d}", d, p) for i, p in enumerate(prices) for d in (old_day, T.TODAY)]
        rows.append(row("N" * 17, T.TODAY, 44200))
        today = [r for r in rows if r["snapshot_date"] == T.TODAY]
        report, _, _ = T.build_outputs(today, rows, T.build_history(rows))
        today_sec = report.split("## Today")[1].split("\n## ")[0]
        self.assertIn("1 new on the shopped models", today_sec)
        self.assertNotIn("under typical", today_sec, "no percentage the interval cannot defend")
        block = report.split("**New today")[1].split("**Spicy picks")[0]
        self.assertIn("N" * 17, block)
        self.assertNotIn("under typical", block)

    def test_per_model_cap_spreads_the_picks(self):
        """Model A is given FIFTEEN cars so THREE of them sit under the low
        edge of its interval.

        With four cars each, only one per model clears the median, so the cap
        was never reached and deleting it left this green — the test asserted
        `max(counts) <= 2` over a set that could not exceed 1. The point of the
        cap is that a model with more good cars than the cap does not take the
        whole page, so the fixture has to contain such a model. Fifteen, because
        the interval's low edge is the 4th value there and the 3rd at anything
        from twelve to fourteen — two under, not three.
        """
        scored = (T.score_picks([listing(price=p) for p in
                                 (20000, 21000, 22000) + tuple(40000 + 500 * i for i in range(12))], "A")
                  + T.score_picks([listing(price=p) for p in
                                   (30000,) + tuple(40000 + 500 * i for i in range(8))], "B"))
        picks = T.choose_picks(scored, 4, per_model=2)
        counts = Counter(p["model_label"] for p in picks)
        self.assertEqual(counts["A"], 2,
                         "A has three cars under typical and the cap is two")
        self.assertTrue(counts["B"], "and the seat the cap freed goes to the other model")

    def test_the_mileage_cap_is_the_configured_one(self):
        """Read from PICKS, and pinned on both sides of its own boundary.

        A test that hard-codes a number is a copy of the config, and passes on
        a build where the code hard-codes a DIFFERENT number; a test that only
        checks a car far over the line passes on any cap between the two.
        """
        mm = T.to_int(T.PICKS.get("max_miles")) or 50000
        self.assertTrue(T.pick_eligible(listing(price=40000, miles=mm)),
                        "a car exactly at the cap is inside it")
        self.assertFalse(T.pick_eligible(listing(price=40000, miles=mm + 1)),
                         "and one mile over is not")

    def test_picks_split_into_drivable_and_worth_the_ship(self):
        """Every car is scored against the whole model, then split: drivable
        picks on one side, everything else on the other, no overlap."""
        pool = ([listing(price=30000, local=True, state="IL"),
                 listing(price=31000, local=False, state="CA")]
                + [listing(price=40000 + 500 * i, local=(i % 2 == 0), state="WI" if i % 2 == 0 else "TX")
                   for i in range(10)])
        local, ship = T.split_picks(T.score_picks(pool, "M"), 4)
        self.assertTrue(local and ship)
        self.assertTrue(all(p["local"] for p in local))
        self.assertTrue(all(not p["local"] for p in ship))
        both = {id(p) for p in local} & {id(p) for p in ship}
        self.assertFalse(both, "a car must not appear in both lists")

    def test_a_drivable_pick_is_scored_against_the_whole_market(self):
        """The drivable list must not get its own median — a merely-average
        local car scores the same whether or not remote cars exist."""
        locals_ = [listing(price=p, local=True) for p in (40000, 41000, 42000, 42500, 43000, 43500)]
        remotes = [listing(price=p, local=False, ship=1200) for p in (30000, 30500, 31000)]
        scored_all = T.score_picks(locals_ + remotes, "M")
        by_price = {p["price"]: p for p in scored_all if p["local"]}
        self.assertLess(by_price[42000]["pick_pct"], 0.05,
                        "an above-median local car is not a bargain just for being local")


# --------------------------------------------------------------------------
# A small cut is a cut. The median of them carries its own count.
# --------------------------------------------------------------------------
class TestASmallCutIsStillACut(unittest.TestCase):
    """A $1 move printed among the i7's price changes suggested a threshold —
    below $100, say, a move is a tick rather than a change. Every version of
    that failed on this record, and the reasons are worth keeping:

    the count it would change ("N ask less than when first seen") is arithmetic
    on two prices the page is DISPLAYING, so a threshold would make the page
    contradict its own two numbers; a Lucid Air that walked down $585 in four
    monotone $85 and $415 steps would vanish from the movement tile on the day
    it moved, with no sawtooth to excuse it; the sizes below $200 run 1, 1, 1,
    1, 1, 1, 10, 40, 44, 49, 49, 50, 58, 59, 76, 80, 80, 80, 80, 85 nine times,
    95, 99, then 100 five times, so $100 sits at the densest point of the tail
    rather than at a gap; a second "not counted" bucket would overlap the
    two-price one (two cars are in both) and double-subtract the denominator;
    a row would draw a falling sparkline under a tag reporting no fall; and the
    $1 line that started it is an UPWARD move, which no cut figure counts at
    all.

    What the record was actually missing is the denominator the house rule
    asks for. The share counts CARS and the median counts downward STEPS, and
    one sentence carried both with only one of them named."""

    def _car(self, vin, prices, **kw):
        d0 = T.TODAY_ORD - len(prices)
        return {**listing(price=prices[-1], vin=vin),
                "series": [[date.fromordinal(d0 + i).isoformat(), p] for i, p in enumerate(prices)],
                "days_tracked": len(prices),
                "cuts": sum(1 for a, b in zip(prices, prices[1:]) if b < a),
                "delta": prices[-1] - prices[0], **kw}

    def test_a_lone_sub_100_cut_is_still_a_cut_everywhere(self):
        """The real WBY33FK06RCR50278 shape: flat at $41,990, then $41,891 —
        one $99 cut. It is in the share's numerator, in the steps behind the
        median, and in the cars asking less than when first seen."""
        cars = [self._car("N" * 17, [41990] * 4 + [41891])]
        cars += [self._car(f"F{i:016d}", [50000, 50000, 50000 - 400 * (i + 1)]) for i in range(4)]
        st = T.market_stats(cars)
        self.assertEqual(st["tracked_2d"], 5)
        self.assertEqual(st["cut_share"], 1.0, "five cars, five cut: the $99 one counts")
        self.assertEqual(st["n_cuts"], 5, "and its step is one of the five behind the median")
        self.assertEqual(st["median_cut"], 800, "median of 99, 400, 800, 1200, 1600")
        self.assertEqual(st["net_down"], 5)
        self.assertEqual(st["two_priced"], 0)
        line = T.market_line(st)
        self.assertIn("median $800 of 5 cuts", line)
        self.assertEqual(line.count("not counted"), 0,
                         "one exclusion bucket exists and it is the two-price one")

    def test_the_median_names_steps_while_the_share_names_cars(self):
        """One car with three cuts makes the two denominators differ, which is
        the whole reason the median needed its own."""
        cars = [self._car("M" * 17, [60000, 59000, 58000, 57000])]
        cars += [self._car(f"F{i:016d}", [50000, 50000]) for i in range(4)]
        st = T.market_stats(cars)
        self.assertEqual((st["tracked_2d"], st["n_cuts"]), (5, 3))
        self.assertIn("20% of 5 cut while tracked, median $1,000 of 3 cuts", T.market_line(st))

    def test_the_price_change_list_leads_with_the_biggest_move(self):
        """The $1 line stays — it is what happened — and lands last, so scale
        is visible without anything being deleted."""
        tl = [{"vin": "A" * 17, "city": "Chicago", "state": "IL", "price": 73373, "local": False,
               "series": [["2026-09-03", 73372], ["2026-09-05", 73373]]},
              {"vin": "B" * 17, "city": "Plano", "state": "TX", "price": 44000, "local": False,
               "series": [["2026-09-03", 51500], ["2026-09-05", 44000]]}]
        rows = {r["vin"]: {**{k: "" for k in T.FIELDS}, "vin": r["vin"], "price": r["price"],
                           "miles": 20000, "year": "2024", "trim": "T", "city": r["city"],
                           "state": r["state"], "target": "t"} for r in tl}
        sec = []
        T.trim_detail(sec, {"id": "t", "label": "T", "note": "", "years": [2024]},
                      tl, rows, {}, [], "2026-09-03", T.TODAY)
        lines = [l for l in sec if l.startswith("- $")]
        self.assertTrue(lines[0].startswith("- $51,500"), f"biggest first, got {lines[0]}")
        self.assertIn("$73,372", lines[1], "and the $1 move is kept, at the bottom")


# --------------------------------------------------------------------------
# A cohort of mixed trims prices the mix, not the car.
# --------------------------------------------------------------------------
class TestCohortMustBeComparable(unittest.TestCase):
    """The interval asks whether a median is stable. It cannot ask whether the
    cars behind it are the same kind of car — and four 2022 iX xDrive50s were
    printed "26-40% under typical for a BMW iX, n=21" against a pool of ten
    M60s and eleven xDrive50s. The M60 is a different car at a different
    price, so the percentage measured the pool's trim mix.

    The first rule tried here pivoted on the cohort's median model YEAR, and a
    skeptic broke it in three ways worth keeping: on the iX pool year and trim
    are collinear, so it fixed those four by accident; it was structurally
    blind to a cohort that is all one year and four trims (the Kia EV9); and
    it left the two cars it promoted to the top of the front page carrying the
    identical defect. Trim is the confound, so trim is the test."""

    def _pool(self, spec):
        """spec: (trim, year, price, n) — n identical cars, so every cohort
        that reaches six does so on purpose."""
        rows = []
        for trim, year, price, n in spec:
            for i in range(n):
                rows.append(listing(price=price + i, miles=20000, trim=trim,
                                    year=year, vin=f"{trim[:3]}{year}{i:012d}"))
        return rows

    def test_a_model_wide_cohort_of_two_trims_says_nothing(self):
        """The shipped iX shape: no trim-and-year cohort reaches six and no
        model year does either, so every car falls to the model pool — which
        holds two trims priced $40,000 apart."""
        pool = self._pool([("xDrive50", 2022, 38000, 4), ("M60", 2023, 78000, 5),
                           ("xDrive50", 2024, 62000, 3), ("M60", 2025, 92000, 3)])
        scored = {p["vin"]: p for p in T.score_picks(pool, "BMW iX")}
        cheap = scored["xDr2022000000000000"]
        self.assertEqual(cheap["pick_basis"], "model", "the fixture must exercise the fallback")
        self.assertEqual(cheap["pick_stand"], "mixed")
        self.assertGreater(cheap["pick_pct"], 0.2, "…and it really is far under that blended median")
        self.assertEqual(T.choose_picks(list(scored.values()), 4), [],
                         "a percentage the page will not print may not choose a pick either")

    def test_one_model_year_and_four_trims_is_still_mixed(self):
        """The Kia EV9 shape, and the case a year-median rule cannot see: every
        car is a 2024, so no car is below its cohort's median year, and the
        year cohort is Light, Land, GT-Line and Light Long Range."""
        pool = self._pool([("Light", 2024, 30000, 3), ("Land", 2024, 42000, 3),
                           ("GT-Line", 2024, 52000, 3), ("Light Long Range", 2024, 35000, 3)])
        scored = {p["vin"]: p for p in T.score_picks(pool, "Kia EV9")}
        car = scored["Lig2024000000000000"]
        self.assertEqual(car["pick_basis"], "year", "one year, twelve cars: the YEAR cohort is the fallback")
        self.assertEqual(car["pick_stand"], "mixed")
        self.assertTrue(all(p["pick_stand"] == "mixed" for p in scored.values()),
                        "no car in a four-trim cohort can be judged by it")

    def test_a_trim_cohort_is_comparable_by_construction(self):
        """Six of one trim and year is the cohort the guard was built to keep:
        the sheet's own i5 and i7 picks, which must be untouched."""
        pool = self._pool([("eDrive40", 2024, 40000, 8), ("M60", 2024, 70000, 8)])
        cheap = min(T.score_picks(pool, "BMW i5"), key=lambda p: p["price"])
        self.assertEqual(cheap["pick_basis"], "trim")
        self.assertNotEqual(cheap["pick_stand"], "mixed")

    def test_a_fallback_cohort_of_one_trim_still_speaks(self):
        """The guard is about comparability, not about the basis. A model that
        sells one trim has a model-wide pool that is trim-clean, and a car
        under its interval is under it."""
        pool = self._pool([("Touring", 2022, 30000, 1), ("Touring", 2023, 44000, 4),
                           ("Touring", 2024, 46000, 3), ("Touring", 2025, 48000, 3)])
        scored = sorted(T.score_picks(pool, "Lucid Air"), key=lambda p: p["price"])
        self.assertEqual(scored[0]["pick_basis"], "model", "no year reaches six")
        self.assertEqual(scored[0]["pick_stand"], "under",
                         "one trim across four years is still one kind of car")
        self.assertEqual([p["price"] for p in T.choose_picks(scored, 4)], [30000],
                         "…and it is still a pick")

    def test_the_report_prints_no_percentage_for_a_mixed_cohort(self):
        """Every surface reads the stand, so the report follows without a
        second rule: no spicy pick, no "under typical" tag."""
        x = {**listing(price=38000, vin="M" * 17), "pick_pct": 0.40, "pick_under": 23986,
             "pick_stand": "mixed", "pick_n": 21, "pick_basis": "model"}
        self.assertNotIn("under typical", T.fmt_new(x, x))
        self.assertEqual(T.choose_picks([x], 4), [])


# --------------------------------------------------------------------------
# A departure from one query is not a departure from the market.
# --------------------------------------------------------------------------
class TestStillListedIsNotGone(unittest.TestCase):
    """A CPO watch's market is "certified cars under the mileage cap", so a car
    that loses its badge leaves THAT watch for real — the builder has always
    said so in delisted()'s own docstring. What was wrong was the words built
    on it: REPORT.md printed "**Gone since 2026-09-03**" over
    WBY13HG00SCU51722 while the same VIN sat live sixty lines below, asking
    $51,476 as an xDrive40, and the page said "GONE — the listing ended" over
    a car in its own listings table. The row now carries what actually
    happened, and leaves every count and every price that means "left the
    market"."""

    def _rows(self, days, target, vin, price, trim, **kw):
        out = []
        for d in days:
            r = {k: "" for k in T.FIELDS}
            r.update({"target": target, "vin": vin, "snapshot_date": d, "price": price,
                      "year": "2025", "trim": trim, "miles": 2590, "state": "PA",
                      "city": "Fort Washington", **kw})
            out.append(r)
        return out

    def _model(self, cpo_days, x_days):
        """One VIN under two targets of one model: a certified watch that stops
        returning it, and an ordinary trim that keeps listing it."""
        vin = "W" * 17
        rows = (self._rows(cpo_days, "bmw-i5-cpo", vin, 54476, "xDrive40", cpo="1")
                + self._rows(x_days, "bmw-i5-xdrive40", vin, 51476, "xDrive40"))
        today = [r for r in rows if r["snapshot_date"] == max(d["snapshot_date"] for d in rows)]
        return vin, rows, today

    def test_a_gone_row_whose_vin_is_live_names_where_it_went(self):
        d1, d2 = date.fromordinal(T.TODAY_ORD - 2).isoformat(), date.fromordinal(T.TODAY_ORD - 1).isoformat()
        vin, rows, today = self._model([d1, d2], [d1, d2, T.TODAY])
        g = T.delisted({"bmw-i5-cpo", "bmw-i5-xdrive40"}, rows, today, T.build_history(rows))
        row = next(r for r in g if r["vin"] == vin)
        self.assertEqual(row["still_listed"], {"trim_id": "bmw-i5-xdrive40", "trim": "xDrive40",
                                               "price": 51476, "cpo": False})
        self.assertFalse(T.departure_is_evidence(row),
                         "a car still listed is not evidence that a car left the market")

    def test_a_real_departure_carries_no_such_key(self):
        """Emitted only when true: 252 of the record's 254 gone rows are real
        departures, and a key repeated to say "no" is 5KB of sheet for nothing."""
        d1, d2 = date.fromordinal(T.TODAY_ORD - 2).isoformat(), date.fromordinal(T.TODAY_ORD - 1).isoformat()
        vin, rows, _ = self._model([d1, d2], [d1, d2])
        other = self._rows([d1, d2, T.TODAY], "bmw-i5-xdrive40", "V" * 17, 44000, "xDrive40")
        rows += other
        today = [r for r in rows if r["snapshot_date"] == T.TODAY]
        g = T.delisted({"bmw-i5-cpo", "bmw-i5-xdrive40"}, rows, today, T.build_history(rows))
        row = next(r for r in g if r["vin"] == vin)
        self.assertNotIn("still_listed", row, "a car that left the market carries no forwarding address")

    def test_it_leaves_the_exit_price_and_the_departure_counts(self):
        """The exit median is "where cars of this trim stopped being
        advertised". A car that is still advertised cannot be in it, whatever
        its label — and one such car among eight would move the median."""
        base = [{"vin": f"D{i:016d}", "likely": "delisted", "exact": True, "trim_id": "t",
                 "trim": "xDrive40", "last_price": 50000 + 500 * i, "last_seen": "2026-09-03",
                 "listed_since": "2026-08-01", "prev_fetch_day": "2026-09-03"} for i in range(8)]
        # …and its trim label differs, because one_cohort() asks whether the
        # cars behind the median are ONE cohort: counting a car that never
        # left would make this pool look mixed and withhold the price
        # altogether, which is the same lie wearing silence.
        live = {"vin": "L" * 17, "likely": "delisted", "exact": True, "trim_id": "t",
                "trim": "M60", "last_price": 90000, "last_seen": "2026-09-03",
                "listed_since": "2026-08-01", "prev_fetch_day": "2026-09-03",
                "still_listed": {"trim_id": "u", "trim": "eDrive40", "price": 88000}}
        old_t = dict(T.TARGETS)
        T.TARGETS["t"] = {"id": "t", "sorts": ["price.asc"], "depth": "light"}
        try:
            with_live = T.exit_stats(base + [live], "t")
            without = T.exit_stats(base, "t")
        finally:
            T.TARGETS.clear(); T.TARGETS.update(old_t)
        self.assertEqual(with_live["exit_n"], without["exit_n"],
                         "the still-listed car is not an exit")
        self.assertEqual(with_live["exit_price"], without["exit_price"])
        # the interval is computed from its own list, not from sale_stats
        self.assertEqual((with_live["exit_lo"], with_live["exit_hi"]),
                         (without["exit_lo"], without["exit_hi"]),
                         "…and not an order statistic of the interval either")
        self.assertIsNotNone(without["exit_lo"], "the fixture must publish an interval to compare")
        self.assertTrue(T.one_cohort(base + [live]),
                        "one still-listed M60 does not make eight xDrive40 exits a mixed pool")
        self.assertEqual(T.sale_stats(base + [live])["n_exits"], T.sale_stats(base)["n_exits"])

    def test_the_surviving_record_must_be_from_the_model_s_latest_fetch_day(self):
        """Eleven of fourteen targets run a slower cadence than their
        siblings, so "live on the sheet" can mean a sighting as old as the
        departure itself. Only a record from the day the model was last
        fetched can say what the car is listed at now."""
        d1, d2 = date.fromordinal(T.TODAY_ORD - 2).isoformat(), date.fromordinal(T.TODAY_ORD - 1).isoformat()
        vin = "W" * 17
        # the certified watch runs again today and does not return the car;
        # the xDrive40 target has not run since d2, so its sighting is stale
        rows = (self._rows([d1, d2, T.TODAY], "bmw-i5-cpo", "C" * 17, 60000, "M60", cpo="1")
                + self._rows([d1, d2], "bmw-i5-cpo", vin, 54476, "xDrive40", cpo="1")
                + self._rows([d1, d2], "bmw-i5-xdrive40", vin, 51476, "xDrive40"))
        tids = {"bmw-i5-cpo", "bmw-i5-xdrive40"}
        today = T.current_rows(rows, tids)
        g = T.delisted(tids, rows, today, T.build_history(rows))
        row = next(r for r in g if r["vin"] == vin)
        self.assertNotIn("still_listed", row,
                         "a two-day-old sighting cannot say what the car asks today")
        # …and the same shape with the xDrive40 target fetching today does
        rows2 = rows + self._rows([T.TODAY], "bmw-i5-xdrive40", vin, 51476, "xDrive40")
        g2 = T.delisted(tids, rows2, T.current_rows(rows2, tids), T.build_history(rows2))
        self.assertEqual(next(r for r in g2 if r["vin"] == vin)["still_listed"]["price"], 51476)

    def test_the_certification_is_the_point_not_a_detail(self):
        """The promo is cpo_only, so a VIN that survives UNCERTIFIED is a car
        whose payment moved — "still listed" alone would report that as good
        news. Both readings are printed."""
        base = {"vin": "W" * 17, "likely": "delisted", "exact": True, "trim_id": "bmw-i5-cpo",
                "trim_label": "CPO under 30k mi", "last_price": 54476, "last_seen": "2026-09-03",
                "year": 2025, "city": "Fort Washington", "state": "PA", "prev_fetch_day": "2026-09-03"}
        lost = {**base, "still_listed": {"trim_id": "t", "trim": "xDrive40", "price": 51476, "cpo": False}}
        kept = {**base, "still_listed": {"trim_id": "t", "trim": "xDrive40", "price": 51476, "cpo": True}}
        t = {"id": "bmw-i5-cpo", "label": "CPO under 30k mi", "note": "", "years": [2025]}
        a, b = [], []
        T.trim_detail(a, t, [], {}, {}, [lost], "2026-09-03", T.TODAY)
        T.trim_detail(b, t, [], {}, {}, [kept], "2026-09-03", T.TODAY)
        self.assertIn("not certified", "\n".join(a))
        self.assertIn("still certified", "\n".join(b))

    def test_the_shortlist_does_not_shout_gone_over_a_car_it_shows_live(self):
        """The sharpest form of the bug: one file, two claims. With the VIN on
        the shortlist the report showed the car live at $51,476 and, fourteen
        lines later, "**Shortlist: GONE**" with the subject line to match."""
        g = {"vin": "W" * 17, "likely": "delisted", "exact": True, "trim_id": "bmw-i5-cpo",
             "trim_label": "CPO under 30k mi", "last_price": 54476, "last_seen": "2026-09-03",
             "prev_fetch_day": "2026-09-03", "year": 2025, "city": "Fort Washington", "state": "PA",
             "still_listed": {"trim_id": "t", "trim": "xDrive40", "price": 51476, "cpo": False}}
        self.assertFalse(T.departure_is_evidence(g))
        old = dict(T.SHORTLIST)
        T.SHORTLIST.clear(); T.SHORTLIST.update({"W" * 17: ""})
        try:
            sec, subject = T.build_today({"cuts": [], "new": [], "gone": []}, T.TODAY)
        finally:
            T.SHORTLIST.clear(); T.SHORTLIST.update(old)
        self.assertNotIn("Shortlist: GONE", "\n".join(sec))
        self.assertNotIn("GONE", subject)

    def test_the_report_names_the_watch_instead_of_announcing_a_departure(self):
        """End to end: the trim block must not head a "Gone since" list with
        this car, and must say where it went."""
        d1, d2 = date.fromordinal(T.TODAY_ORD - 2).isoformat(), date.fromordinal(T.TODAY_ORD - 1).isoformat()
        vin, rows, today = self._model([d1, d2], [d1, d2, T.TODAY])
        gone = T.delisted({"bmw-i5-cpo", "bmw-i5-xdrive40"}, rows, today, T.build_history(rows))
        row = next(g for g in gone if g["vin"] == vin)
        row["still_listed"] = {"trim_id": "bmw-i5-xdrive40", "trim": "xDrive40", "price": 51476}
        sec = []
        T.trim_detail(sec, {"id": "bmw-i5-cpo", "label": "CPO under 30k mi", "note": "", "years": [2025]},
                      [], {}, T.build_history(rows), [row], d2, T.TODAY)
        text = "\n".join(sec)
        self.assertIn("Left this watch, the car still listed (1)", text)
        self.assertIn("the same VIN is listed as xDrive40 at $51,476, not certified", text)
        self.assertNotIn("Gone since", text)
        self.assertNotIn("asking", text, "the departed listing was never cut to that price")


# --------------------------------------------------------------------------
# "New" is new to the tracker. A car listed a fortnight before the tracker
# first saw it entered a fetch window, not the market.
# --------------------------------------------------------------------------
class TestReachNotArrival(unittest.TestCase):
    def test_a_fortnight_on_the_market_before_first_sight_is_reach(self):
        """Fourteen days, on both sides of the line: a car listed thirteen
        days before the tracker saw it may simply have been seen promptly on
        a two-day cadence with a slow index behind it; fourteen is a car that
        sat outside the window."""
        self.assertTrue(T.reach_not_arrival({"listed_since": "2026-08-20", "first_seen": "2026-09-03"}))
        self.assertFalse(T.reach_not_arrival({"listed_since": "2026-08-21", "first_seen": "2026-09-03"}))
        self.assertTrue(T.reach_not_arrival({"listed_since": "2026-03-18", "first_seen": "2026-09-05"}))

    def test_an_undated_car_is_not_called_reach(self):
        """No listing date, no claim — the same silence days_listed keeps."""
        self.assertFalse(T.reach_not_arrival({"first_seen": "2026-09-05"}))
        self.assertFalse(T.reach_not_arrival({"listed_since": "", "first_seen": "2026-09-05"}))
        self.assertFalse(T.reach_not_arrival({"listed_since": "not a date", "first_seen": "2026-09-05"}))

    def test_the_new_lines_count_reach_beside_new(self):
        x_far = {"vin": "F1", "price": 44000, "city": "Plano", "state": "TX", "local": False,
                 "listed_since": "2026-06-01", "first_seen": T.TODAY, "days_listed": 96, "ship": 1000}
        x_now = {"vin": "N1", "price": 45000, "city": "Chicago", "state": "IL", "local": True,
                 "listed_since": T.TODAY, "first_seen": T.TODAY, "days_listed": 0}
        sec, subject = T.build_today({"cuts": [], "gone": [],
                                      "new": [{"x": x_far, "label": "BMW i7", "pct": None, "shopping": True},
                                              {"x": x_now, "label": "BMW i7", "pct": None, "shopping": True}]},
                                     T.TODAY)
        text = "\n".join(sec)
        self.assertIn("2 new on the shopped models (1 listed 14+ days before the tracker saw it — reach, not arrival)", text)
        self.assertIn("2 new", subject)
        self.assertIn("on market 96d, new to the tracker — reach, not arrival", T.fmt_new(x_far))
        self.assertNotIn("reach", T.fmt_new(x_now))


# --------------------------------------------------------------------------
# Seen at two prices is not cut. A VIN surfacing through a group's storefronts
# at two fixed prices on alternate days read as four cuts and four restorations,
# and every downward step of it was counted as a cut a dealer took.
# --------------------------------------------------------------------------
class TestTwoPrices(unittest.TestCase):
    def series(self, prices, start="2026-08-20"):
        d0 = date.fromisoformat(start)
        return [[date.fromordinal(d0.toordinal() + i).isoformat(), p] for i, p in enumerate(prices)]

    def test_a_sawtooth_is_two_prices(self):
        """The shape that started this: 54,999 / 55,849 in turn for days.
        Two distinct prices, each seen at least twice, a step each way."""
        self.assertEqual(T.two_prices(self.series([54999, 55849, 54999, 55849, 54999])), [54999, 55849])

    def test_a_cut_that_held_is_not(self):
        """Down once and staying down is a cut, however long the record."""
        self.assertIsNone(T.two_prices(self.series([55849, 55849, 54999, 54999, 54999])))

    def test_a_single_blip_is_not(self):
        """One day at the other price is a wobble, and the record already has
        a name for a cut that was put back: it stays in the restored count
        rather than being called a second price the car is sold at."""
        self.assertIsNone(T.two_prices(self.series([54000, 54000, 54898, 54000, 54000])))
        self.assertIsNone(T.two_prices(self.series([45416, 44441, 44441, 44441])), "one sighting of the high price")

    def test_three_prices_are_a_history_not_a_pair(self):
        self.assertIsNone(T.two_prices(self.series([46000, 45500, 46000, 45000, 46000])))

    def test_two_price_cars_leave_every_cut_figure_and_are_counted_apart(self):
        """market_stats: the sawtooth car is not a cut car, its steps are not
        in the median cut, it is neither 'ask less' nor 'cut and put back',
        and the denominator of the share excludes it — the sentence says how
        many were set aside so the share is still read against a count."""
        saw = {**listing(), "vin": "SAW", "days_tracked": 5, "cuts": 2, "delta": 0,
               "series": self.series([54999, 55849, 54999, 55849, 54999])}
        cut = {**listing(), "vin": "CUT", "days_tracked": 3, "cuts": 1, "delta": -1000,
               "series": self.series([50000, 50000, 49000])}
        # four held cars, because the sentence prints its cut figures only
        # over five tracked cars — the sawtooth is set aside, the four remain
        held = [{**listing(), "vin": f"HELD{i}", "days_tracked": 3, "cuts": 0, "delta": 0,
                 "series": self.series([48000 + i, 48000 + i, 48000 + i])} for i in range(4)]
        st = T.market_stats([saw, cut] + held)
        self.assertEqual(st["two_priced"], 1)
        self.assertEqual(st["tracked_2d"], 6)
        self.assertEqual(st["cut_share"], 0.2, "one cut car of the five counted, not one of six")
        self.assertEqual(st["median_cut"], 1000, "the sawtooth's $850 steps are not cuts")
        self.assertEqual(st["net_down"], 1)
        self.assertEqual(st["restored"], 0, "a car seen at two prices is not 'cut and put back'")
        line = T.market_line(st)
        self.assertIn("20% of 5 cut while tracked", line)
        self.assertIn("1 seen at two prices, not counted", line)

    def test_the_report_names_the_two_prices_instead_of_counting_cuts(self):
        s = {"cuts": 4, "delta": 0, "days_tracked": 13,
             "series": self.series([54999, 55849] * 6 + [54999])}
        x = {**listing(), "vin": "WBY33FK05RCP99465", "price": 54999, **s}
        old = dict(T.SHORTLIST)
        T.SHORTLIST.clear(); T.SHORTLIST.update({"WBY33FK05RCP99465": ""})
        try:
            text = "\n".join(T.shortlist_section({"WBY33FK05RCP99465": (x, "BMW i5")}, {}, {}, T.TODAY))
        finally:
            T.SHORTLIST.clear(); T.SHORTLIST.update(old)
        self.assertIn("seen at $54,999 and $55,849", text)
        self.assertNotIn("cut 4x", text)
        self.assertNotIn("down 4x", text)

    def test_a_two_price_cars_downward_day_is_not_a_cut_event(self):
        """End to end through the builder: a sawtooth car's down day on the
        newest snapshot must not be announced as a cut today."""
        def row(vin, day, price):
            r = {k: "" for k in T.FIELDS}
            r.update({"target": "bmw-i5-edrive40", "vin": vin, "snapshot_date": day,
                      "price": price, "year": "2024", "trim": "eDrive40", "miles": 20000,
                      "state": "IL", "city": "Chicago"})
            return r
        days = [date.fromordinal(T.TODAY_ORD - 4 + i).isoformat() for i in range(5)]
        saw = [row("S" * 17, d, p) for d, p in zip(days, [54999, 55849, 54999, 55849, 54999])]
        cut = [row("C" * 17, d, p) for d, p in zip(days, [50000, 50000, 50000, 50000, 49000])]
        rows = saw + cut
        today = [r for r in rows if r["snapshot_date"] == T.TODAY]
        report, _, _ = T.build_outputs(today, rows, T.build_history(rows))
        today_sec = report.split("## Today")[1].split("\n## ")[0]
        self.assertIn("$1,000 cut", today_sec)
        self.assertNotIn("$850", today_sec, "the sawtooth's down day is its other price, not a cut")


# --------------------------------------------------------------------------
# The public anchor. Distances must come from a committed city-centre point,
# resolved without any network call — a private home zip would let anyone
# trilaterate the house from the published distances.
# --------------------------------------------------------------------------
class TestAnchor(unittest.TestCase):
    def test_distances_measure_from_the_public_anchor(self):
        self.assertTrue(T.coords_ok(*T.HOME),
                        "buyer.anchor must resolve offline at import")
        self.assertAlmostEqual(T.HOME[0], 41.8781, places=3)   # downtown Chicago
        self.assertEqual(T.HOME_NAME, "Chicago")


# --------------------------------------------------------------------------
# The zip cache is committed: a transient throttle cached as a miss would
# never be retried, so only definitive answers may be written to it.
# --------------------------------------------------------------------------
class TestZipCache(unittest.TestCase):
    @staticmethod
    def _fake(status, body=None):
        class R:
            status_code = status
            def json(self):
                return body or {}
        return lambda *a, **k: R()

    def test_transient_failures_are_not_cached_but_misses_are(self):
        old_get, old_cache = T.requests.get, dict(T.ZIP_CACHE)
        try:
            T.ZIP_CACHE.clear()
            T.requests.get = self._fake(429)
            self.assertEqual(T.zip_coords("60601"), (None, None))
            self.assertNotIn("60601", T.ZIP_CACHE,
                             "a throttle must be retried on the next run")
            T.requests.get = self._fake(404)
            T.zip_coords("00000")
            self.assertIn("00000", T.ZIP_CACHE)
            self.assertIsNone(T.ZIP_CACHE["00000"])
        finally:
            T.requests.get = old_get
            T.ZIP_CACHE.clear()
            T.ZIP_CACHE.update(old_cache)


# --------------------------------------------------------------------------
# fetch(): an error is "unknown", never "the market is empty".
# --------------------------------------------------------------------------
class TestFetch(unittest.TestCase):
    def test_envelope_total_probes_the_common_shapes(self):
        self.assertEqual(T.envelope_total({"total": 82, "data": []}), 82)
        self.assertEqual(T.envelope_total({"totalCount": "82"}), 82)
        self.assertEqual(T.envelope_total({"meta": {"totalItems": 82}}), 82)
        self.assertEqual(T.envelope_total({"pagination": {"total": 82}}), 82)
        self.assertIsNone(T.envelope_total({"data": []}))
        self.assertIsNone(T.envelope_total(None))

    def test_fetch_records_the_market_total(self):
        old_get = T.requests.get
        try:
            class R:
                status_code = 200
                def json(self):
                    return {"totalCount": 82, "data": [{"vin": "X"}] * 3}
            T.requests.get = lambda *a, **k: R()
            batch = T.fetch("National", None, "price.asc", 1,
                            target("bmw-i5-edrive40"))
            self.assertEqual(len(batch), 3)
            self.assertEqual(T.TOTALS[("bmw-i5-edrive40", "National")], 82)
        finally:
            T.requests.get = old_get
            T.TOTALS.clear()

    def test_persistent_failure_returns_none_after_one_retry(self):
        old_get, old_sleep = T.requests.get, T.time.sleep
        try:
            T.time.sleep = lambda s: None
            def boom(*a, **k):
                raise T.requests.RequestException("connection reset")
            T.requests.get = boom
            calls0 = T.CALLS
            self.assertIsNone(
                T.fetch("National", None, "price.asc", 1, target("bmw-i5-edrive40")))
            self.assertEqual(T.CALLS - calls0, 2, "one retry, then give up")
        finally:
            T.requests.get, T.time.sleep = old_get, old_sleep
            T.FAILED_FETCHES = 0


# --------------------------------------------------------------------------
# delisted(): the departure classifier, against a mixed-cadence model. The
# i5's daily eDrive40 must not define "yesterday" for its every-other-day
# siblings, and a query that returned everything proves a delisting.
# --------------------------------------------------------------------------
class TestBrokenEnvelope(unittest.TestCase):
    """HTTP 200 is not the same as an answer.

    `data: []` is a real result — that query found nothing. A body with no data
    list at all is a maintenance page, an HTML error, or a renamed envelope, and
    it used to be turned into `[]`: which the fetch loop reads as a short page,
    which marks the scope EXHAUSTED, which tells delisted() the query saw the
    whole market — so every car it did not return is published as gone. One bad
    deploy upstream would have sold the entire watchlist.
    """

    @staticmethod
    def _resp(body, text="", status=200):
        class R:
            status_code = status
            @staticmethod
            def json():
                if body is _BAD:
                    raise ValueError("not json")
                return body
        R.text = text
        return R

    def _fetch(self, body, text=""):
        old_get, old_failed = T.requests.get, T.FAILED_FETCHES
        old_sleep = T.time.sleep
        T.FAILED_SCOPES.discard(("bmw-i5-m60", "National"))
        try:
            T.requests.get = lambda *a, **k: self._resp(body, text)
            T.time.sleep = lambda *_: None
            return T.fetch("National", None, "price.asc", 1, T.TARGETS["bmw-i5-m60"])
        finally:
            T.requests.get, T.time.sleep = old_get, old_sleep
            T.FAILED_FETCHES = old_failed
            T.FAILED_SCOPES.discard(("bmw-i5-m60", "National"))

    def test_an_empty_data_list_is_a_real_empty_answer(self):
        self.assertEqual(self._fetch({"data": [], "total": 0}), [])

    def test_a_body_with_no_data_list_is_a_failure_not_an_empty_market(self):
        self.assertIsNone(self._fetch({"message": "service unavailable"}),
                          "no data list means unknown, never 'nothing matched'")

    def test_a_body_that_is_not_json_is_a_failure(self):
        self.assertIsNone(self._fetch(_BAD, text="<html>maintenance</html>"))

    def test_a_broken_envelope_records_the_scope_as_failed(self):
        """…so delisted() knows the query never answered, instead of judging
        an absence against a window that was never opened."""
        self._fetch({"nope": 1})
        # _fetch clears it in its own teardown, so re-run and inspect inside
        old_get, old_sleep = T.requests.get, T.time.sleep
        try:
            T.requests.get = lambda *a, **k: self._resp({"nope": 1})
            T.time.sleep = lambda *_: None
            T.FAILED_SCOPES.discard(("bmw-i5-m60", "National"))
            T.fetch("National", None, "price.asc", 1, T.TARGETS["bmw-i5-m60"])
            self.assertIn(("bmw-i5-m60", "National"), T.FAILED_SCOPES)
        finally:
            T.requests.get, T.time.sleep = old_get, old_sleep
            T.FAILED_SCOPES.discard(("bmw-i5-m60", "National"))


class TestLandedAndAdjusted(unittest.TestCase):
    """The shipping term in adjusted(), which every landed price is built on.

    Nothing pinned it: the existing coverage asserts the mileage adjustment is
    OFF, which passes on code that drops the shipping term entirely. That term
    is the one part of this function the shipped config actually exercises —
    the picks are ranked on it and every "asking + shipping" line prints it.
    """

    def test_shipping_is_added(self):
        self.assertEqual(T.adjusted(40000, 1200), 41200)
        self.assertEqual(T.adjusted(40000, 0), 40000)

    def test_no_price_is_no_answer(self):
        self.assertIsNone(T.adjusted(None, 1200))

    def test_the_landed_total_is_the_page_s_own_arithmetic(self):
        """The page's landed(x) is price plus shipping; this is the record's,
        and they are the same sum, which is what stops the two surfaces printing
        two totals for one car."""
        for price, ship in ((40000, 0), (36479, 1031), (99999, 350)):
            self.assertEqual(T.adjusted(price, ship), price + ship)

    def test_landed_reads_the_rows_own_shipping(self):
        r = {k: "" for k in T.FIELDS}
        r.update({"price": 40000, "miles": 20000, "state": "CA", "lat": "", "lon": ""})
        total, ship = T.landed(r)
        self.assertEqual(total, 40000 + ship)
        self.assertGreater(ship, 0, "a car outside the buyer's states pays a hauler")
        r["state"] = list(T.STATES)[0]
        total_local, ship_local = T.landed(r)
        self.assertEqual((total_local, ship_local), (40000, 0),
                         "and one inside them does not")


class TestHistoryRoundTrip(unittest.TestCase):
    """load_history() and write_rows(), which nothing drove.

    Between them they are the whole persistence layer: every guard, every
    reconstruction and every published series is built on what comes back out
    of this file. Three of the normalisations in load_history() exist because
    of specific incidents — a BOM that blanked 3,581 dates, a renamed target,
    a lower-case state that fell out of scope — and none of them was asserted.
    """

    def _write(self, td, rows, header=None):
        import csv as _csv
        p = Path(td) / "s.csv"
        with p.open("w", newline="", encoding="utf-8") as f:
            w = _csv.DictWriter(f, fieldnames=header or T.FIELDS, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        return p

    def test_a_row_survives_the_round_trip_with_its_header_intact(self):
        import csv as _csv, tempfile
        row = {k: "" for k in T.FIELDS}
        row.update({"target": "bmw-i5-edrive40", "vin": "V" * 17,
                    "snapshot_date": "2026-08-01", "price": "40000", "state": "IL"})
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "out.csv"
            was, T.SNAPSHOTS = T.SNAPSHOTS, p
            try:
                T.write_rows([row])
                with p.open(newline="", encoding="utf-8") as f:
                    head = next(_csv.reader(f))
                self.assertEqual(head, list(T.FIELDS),
                                 "the header is the file's contract — order included")
                back = T.load_history()
            finally:
                T.SNAPSHOTS = was
        self.assertEqual(len(back), 1)
        self.assertEqual(back[0]["vin"], "V" * 17)
        self.assertEqual(back[0]["snapshot_date"], "2026-08-01")

    def test_a_byte_order_mark_does_not_blank_every_date(self):
        """The incident this encoding exists for: a BOM turns the first header
        into "\ufeffsnapshot_date", every row then reads its date as "", the
        already-fetched guard sees no TODAY, and write_rows rewrites the whole
        file with blank dates."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s.csv"
            body = ",".join(T.FIELDS) + "\n"
            row = {k: "" for k in T.FIELDS}
            row.update({"target": "bmw-i5-edrive40", "vin": "V" * 17,
                        "snapshot_date": "2026-08-01", "state": "IL"})
            body += ",".join(str(row[k]) for k in T.FIELDS) + "\n"
            p.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
            was, T.SNAPSHOTS = T.SNAPSHOTS, p
            try:
                back = T.load_history()
            finally:
                T.SNAPSHOTS = was
        self.assertEqual(back[0]["snapshot_date"], "2026-08-01",
                         "a BOM must not eat the first column's name")

    def test_a_renamed_target_and_a_lower_case_state_are_normalised(self):
        """Both read from config rather than duplicated here, so the test
        follows a rename instead of pinning one."""
        import tempfile
        if not T.LEGACY_IDS:
            self.skipTest("no legacy target ids in this config")
        old_id, new_id = next(iter(T.LEGACY_IDS.items()))
        with tempfile.TemporaryDirectory() as td:
            row = {k: "" for k in T.FIELDS}
            row.update({"target": old_id, "vin": "V" * 17,
                        "snapshot_date": "2026-08-01", "state": "il"})
            p = self._write(td, [row])
            was, T.SNAPSHOTS = T.SNAPSHOTS, p
            try:
                back = T.load_history()
            finally:
                T.SNAPSHOTS = was
        self.assertEqual(back[0]["target"], new_id,
                         "a row written under the old id still belongs to the target")
        self.assertEqual(back[0]["state"], "IL",
                         "in_scope() compares upper-case codes; a stray 'il' falls out of scope")

    def test_a_missing_file_is_an_empty_record_not_a_crash(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            was, T.SNAPSHOTS = T.SNAPSHOTS, Path(td) / "nope.csv"
            try:
                self.assertEqual(T.load_history(), [])
            finally:
                T.SNAPSHOTS = was


class TestTodaySectionIsRelativeToTheData(unittest.TestCase):
    """The leading section describes the newest snapshot, not the wall clock —
    and says which day that was.

    Its gone detector was already data-relative; the CUT detector was gated on
    TODAY. So a rebuild run on a day the tracker had not fetched —
    tools/rebuild_outputs.py, which is exactly what a dispatch runs — wrote a
    report whose Today section had lost every price-cut bullet while still
    printing "79 new · 31 gone" above it and per-model lines counting 42 price
    changes below. Three surfaces, one day, two different stories.

    The cut detector moved to the data and the heading did not, so the same
    rebuild then published yesterday's cuts under the word "today", four lines
    above that model's own "Not fetched today — showing 2026-09-05". The
    heading names the day whenever it is not today; on a live run it is, and
    the section still reads "## Today".
    """

    @staticmethod
    def row(vin, day, price):
        r = {k: "" for k in T.FIELDS}
        r.update({"target": "bmw-i5-edrive40", "vin": vin, "snapshot_date": day,
                  "price": price, "year": "2024", "trim": "eDrive40", "miles": 20000,
                  "state": "IL", "city": "Chicago"})
        return r

    def test_a_cut_on_the_last_fetch_day_is_reported_after_the_fact(self):
        yesterday = date.fromordinal(T.TODAY_ORD - 1).isoformat()
        before = date.fromordinal(T.TODAY_ORD - 2).isoformat()
        rows = [self.row("V" * 17, before, 45000), self.row("V" * 17, yesterday, 43000),
                self.row("K" * 17, before, 50000), self.row("K" * 17, yesterday, 50000)]
        today_rows = [r for r in rows if r["snapshot_date"] == yesterday]
        report, _, subject = T.build_outputs(today_rows, rows, T.build_history(rows))
        head = f"## The last fetch — {yesterday}"
        self.assertIn(head, report,
                      "the section is about the newest snapshot, whenever it was "
                      "taken — and names the day when it was not today")
        self.assertNotIn("## Today", report,
                         "nothing here happened today; the model's own section "
                         "one line below says so in those words")
        today = report.split(head)[1].split("\n## ")[0]
        self.assertIn("▼", today,
                      "a $2,000 cut at the last fetch is a cut whether or not the "
                      "tracker has run again since")
        self.assertIn("cut", subject.lower(),
                      "and the subject line says so too")

    def test_a_live_run_still_says_today(self):
        """The other side of the same rule: when the newest snapshot IS today
        the heading and every dated line read exactly as they always have."""
        yesterday = date.fromordinal(T.TODAY_ORD - 1).isoformat()
        rows = [self.row("V" * 17, yesterday, 45000), self.row("V" * 17, T.TODAY, 43000),
                self.row("W" * 17, yesterday, 46000), self.row("W" * 17, T.TODAY, 44000),
                self.row("X" * 17, yesterday, 47000), self.row("X" * 17, T.TODAY, 45000),
                self.row("Y" * 17, yesterday, 48000), self.row("Y" * 17, T.TODAY, 46000)]
        today_rows = [r for r in rows if r["snapshot_date"] == T.TODAY]
        report, _, _ = T.build_outputs(today_rows, rows, T.build_history(rows))
        self.assertIn("## Today", report)
        self.assertNotIn("## The last fetch", report)
        self.assertIn("1 more cut today", report,
                      "the overflow line is dated by the same rule as the heading")

    def test_a_model_not_in_the_newest_snapshot_contributes_nothing(self):
        """The gate was the cadence SCHEDULE, so a model due today whose every
        query failed kept passing it — and with its own as_of a day behind, the
        cut detector matched on that older day and headlined yesterday's cuts
        as today's, above its own section reading "Not fetched today"."""
        yesterday = date.fromordinal(T.TODAY_ORD - 1).isoformat()
        before = date.fromordinal(T.TODAY_ORD - 2).isoformat()
        i5 = [self.row("V" * 17, before, 45000), self.row("V" * 17, yesterday, 43000),
              self.row("N" * 17, yesterday, 44000)]      # an arrival at ITS last fetch
        i7 = []
        for vin, p0, p1 in (("A" * 17, 90000, 90000), ("B" * 17, 91000, 91000)):
            for day, price in ((before, p0), (yesterday, p1), (T.TODAY, p1)):
                r = self.row(vin, day, price)
                r.update({"target": "bmw-i7-edrive50", "trim": "eDrive50"})
                i7.append(r)
        rows = i5 + i7
        today_rows = [r for r in rows if r["snapshot_date"] == T.TODAY]
        report, _, _ = T.build_outputs(today_rows, rows, T.build_history(rows))
        head = report.split("\n## ")[1] if "\n## " in report else ""
        self.assertNotIn("▼ $2,000", report.split("## Shopping")[0],
                         "the i5's cut happened at ITS last fetch, which is not "
                         "the day this record is newest at")
        self.assertIn("_Not fetched today — showing " + yesterday + "._", report,
                      "and the i5's own section says exactly that")
        # …and the section still carries everything that fetch found. Gating
        # this block on the model being IN the newest snapshot would empty it
        # while the brief line above it counts one, and while the price-change
        # and departure blocks below it go on describing the same fetch.
        self.assertIn(f"**New on {yesterday} (1)**", report,
                      "the i5's own section reports its own last fetch in full")


class TestEveryDatedSentenceNamesItsOwnDay(unittest.TestCase):
    """One record, built on a day it was not fetched, read four ways.

    The report dated its changes by three different clocks: the cut bullets by
    the model's own last fetch, "N new" and the NEW tag by the wall clock, the
    shortlist's cut tag by the wall clock again — and the dashboard by the
    record's newest day, under a comment in its own source claiming to mirror
    the first. On the committed sheet rebuilt one day after its last fetch the
    page called seven i5s and nine i7s new and the report said "0 new" for
    both, out of one file.

    Every sentence below is dated by the day its own subject was last seen, so
    they can only be wrong together. On a live run that day is today and every
    one of them reads exactly as it always has — which is what
    TestTodaySectionIsRelativeToTheData.test_a_live_run_still_says_today pins.
    """

    @staticmethod
    def row(vin, day, price, target="bmw-i5-edrive40", trim="eDrive40", **kw):
        r = {k: "" for k in T.FIELDS}
        r.update({"target": target, "vin": vin, "snapshot_date": day,
                  "price": price, "year": "2024", "trim": trim, "miles": 20000,
                  "state": "IL", "city": "Chicago"})
        r.update(kw)
        return r

    def setUp(self):
        self.yesterday = date.fromordinal(T.TODAY_ORD - 1).isoformat()
        before = date.fromordinal(T.TODAY_ORD - 2).isoformat()
        self.cut_vin, self.new_vin = "V" * 17, "N" * 17
        self.rows = [self.row(self.cut_vin, before, 45000),
                     self.row(self.cut_vin, self.yesterday, 43000),
                     self.row("K" * 17, before, 50000),
                     self.row("K" * 17, self.yesterday, 50000),
                     self.row(self.new_vin, self.yesterday, 44000)]
        old = dict(T.SHORTLIST)
        T.SHORTLIST.clear(); T.SHORTLIST.update({self.cut_vin: ""})
        try:
            self.report, self.site, _ = T.build_outputs(*self._record())
        finally:
            T.SHORTLIST.clear(); T.SHORTLIST.update(old)
        self.i5 = self.site["brands"]["bmw"]["models"]["i5"]

    def _record(self):
        latest = [r for r in self.rows if r["snapshot_date"] == self.yesterday]
        return latest, self.rows, T.build_history(self.rows)

    def test_the_record_is_not_being_read_on_the_day_it_was_written(self):
        """The precondition every assertion here rests on. Without it each one
        passes on a record whose newest day IS today, where the wall clock and
        the data agree and no anchor can be told from another."""
        self.assertEqual(self.i5["as_of"], self.yesterday)
        self.assertNotEqual(self.yesterday, T.TODAY)

    def test_the_model_line_counts_the_arrivals_of_its_own_last_fetch(self):
        line = next(l for l in self.report.splitlines() if " on the market · " in l)
        self.assertIn("· 1 new ·", line,
                      "one car arrived at this model's last fetch; dated by the "
                      "wall clock instead, every model not fetched today counts "
                      "0 new by construction")

    def _tags(self, vin):
        """The tag line the record prints under one car's row, or ""."""
        block = self.report.split("**Illinois")[1]
        rows = [r for r in block.split("\n- ") if vin in r]
        if not rows:
            self.fail(f"{vin} has no row in the record:\n{block[:800]}")
        return next((l.strip() for l in rows[0].splitlines()
                     if l.strip().startswith("_")), "")

    def test_the_arrivals_block_is_there_and_names_its_day(self):
        """It was gated on the cadence schedule as well as on having a previous
        day, which was harmless while the arrivals were found by the wall clock
        (an off-cadence model had none) and is not now: the block would vanish
        from a section whose own line above it counts one."""
        self.assertIn(f"**New on {self.yesterday} (1)** — first seen on "
                      f"{self.yesterday},", self.report)
        self.assertNotIn("**New today", self.report,
                         "nothing arrived today; the section says which day it "
                         "is describing, as its header three lines up does")

    def test_a_shortlisted_departure_is_dated_by_its_own_models_fetch(self):
        """The last dated sentence in the record: "missing today" is a claim
        about a fetch that looked and did not find the car. Dated by the record
        instead of by the model, it names a day on which nothing looked."""
        d2 = date.fromordinal(T.TODAY_ORD - 2).isoformat()
        d1 = self.yesterday
        m60 = "bmw-i5-m60"                      # single-sort, so the window
        self.assertTrue(T.window_reconstructable(T.TARGETS[m60]),
                        "the premise: this target's window can be rebuilt from "
                        "rows alone, which is what makes 'out of window' reachable "
                        "with no live fetch signals")
        rows = [self.row(f"W{i:02d}", d, 30000 + i * 500, target=m60, trim="M60")
                for d in (d2, d1) for i in range(T.PER_PAGE)]
        rows.append(self.row("H" * 17, d2, 41000, target=m60, trim="M60"))
        for vin in ("A" * 17, "B" * 17):        # an i7 fetched today, so the
            for d in (d2, d1, T.TODAY):         # record's newest day is today
                rows.append(self.row(vin, d, 90000, target="bmw-i7-edrive50",
                                     trim="eDrive50"))
        latest = [r for r in rows if r["snapshot_date"] == T.TODAY]
        was, log = dict(T.SHORTLIST), T.FETCH_LOG
        pw, ex, fs = dict(T.PRICE_WINDOW), set(T.EXHAUSTED), set(T.FAILED_SCOPES)
        T.SHORTLIST.clear(); T.SHORTLIST.update({"H" * 17: ""})
        # No live signals and no committed fetch log: the window is rebuilt from
        # the rows alone, which is the offline-rebuild case and the only one that
        # reaches "out of window" without a fetch this run.
        T.PRICE_WINDOW.clear(); T.EXHAUSTED.clear(); T.FAILED_SCOPES.clear()
        T.FETCH_LOG = Path("data/__no_such_fetch_log__.json")
        try:
            report, site, _ = T.build_outputs(latest, rows, T.build_history(rows))
        finally:
            T.SHORTLIST.clear(); T.SHORTLIST.update(was)
            T.FETCH_LOG = log
            T.PRICE_WINDOW.clear(); T.PRICE_WINDOW.update(pw)
            T.EXHAUSTED.clear(); T.EXHAUSTED.update(ex)
            T.FAILED_SCOPES.clear(); T.FAILED_SCOPES.update(fs)
        self.assertEqual(site["brands"]["bmw"]["models"]["i5"]["as_of"], d1)
        self.assertEqual(site["data_through"], T.TODAY,
                         "the precondition: the record is newer than this "
                         "model's last fetch, which is the only place the two "
                         "candidate days differ")
        line = next((l for l in report.splitlines() if "H" * 17 in l), "")
        self.assertIn(f"missing on {d1} — beyond that fetch's cut-off", line,
                      f"the i5 was last fetched {d1}; nothing looked for this "
                      f"car on {T.TODAY} — the shortlist printed {line!r}")

    def test_and_it_is_there_on_a_model_the_calendar_says_is_not_due(self):
        """The case that tells the two gates apart. Every i5 trim runs daily,
        so `due_on` is true for it on any day and the schedule gate cannot be
        distinguished from the data one — until the trims are put on a cadence
        that deterministically excludes today."""
        i5 = [t for t in T.TARGETS.values() if t["model_key"] == "i5"]
        was = [(t["cadence"], t["offset"]) for t in i5]
        for t in i5:
            t["cadence"], t["offset"] = 2, (1 - T.TODAY_ORD) % 2
        try:
            self.assertFalse(any(T.due_on(t, T.TODAY_ORD) for t in i5),
                             "the precondition: the calendar says none of these "
                             "trims runs today")
            report, _, _ = T.build_outputs(*self._record())
        finally:
            for t, (c, o) in zip(i5, was):
                t["cadence"], t["offset"] = c, o
        self.assertIn(f"**New on {self.yesterday} (1)**", report,
                      "the section describes this model's own last fetch, and "
                      "the arrival at it is part of that fetch whatever the "
                      "calendar says about today")

    def test_and_that_arrival_wears_the_tag_in_the_rows_below(self):
        self.assertIn("NEW", self._tags(self.new_vin),
                      "the car that arrived at this model's last fetch is the "
                      "one the rows mark")

    def test_and_a_car_seen_on_both_days_does_not(self):
        """The inverse, on the same record: the tag cannot simply be 'seen'."""
        self.assertNotIn("NEW", self._tags("K" * 17))
        self.assertFalse(T.is_new_on({"first_seen": "2026-01-01", "days_tracked": 1},
                                     self.yesterday))

    def test_the_shortlist_dates_its_cut_instead_of_calling_it_todays(self):
        line = next((l for l in self.report.splitlines() if "CUT $" in l), "")
        self.assertIn(f"▼ CUT $2,000 on {self.yesterday}", line,
                      "the cut happened at the last fetch, which was not today "
                      f"— the shortlist printed {line!r}")
        self.assertNotIn("today", line)


class TestATrimSectionIsAboutItsOwnQuery(unittest.TestCase):
    """The table is one row per VEHICLE; a trim section is about LISTINGS.

    The certified watch matches cars the ordinary trim targets match too, so a
    car can be two records on one day — the record's own departure note says so
    ("the same VIN is listed as xDrive40 at $51,476, not certified"). The
    sections were split on the table's chosen copy, which is the cheapest with
    ties broken by list order, so the watch reported whatever survived that:
    on every one of the seven days it has run it said the wrong thing, and on
    four of them "none found" while it was returning cars.

    Membership alone would not have fixed it. Of the nine VIN-days this record
    holds in two targets, four carry two different prices, and in one of those
    the cheaper copy is NOT certified — so a section that borrowed the table's
    price would publish an uncertified $56,000 listing as the cheapest
    certified car.
    """

    @staticmethod
    def row(target, vin, day, price, cpo="", trim="eDrive40", dealer="a dealer"):
        r = {k: "" for k in T.FIELDS}
        r.update({"target": target, "vin": vin, "snapshot_date": day, "price": price,
                  "year": "2025", "trim": trim, "miles": 9000, "state": "CA",
                  "city": "Carlsbad", "cpo": cpo, "dealer": dealer})
        return r

    def setUp(self):
        d = T.TODAY
        self.both = "B" * 17          # in the watch AND in a sibling, two prices
        self.tie = "T" * 17           # in both at the same price
        rows = [
            # the watch's own records
            self.row("bmw-i5-cpo", self.both, d, 58085, cpo="1", trim="xDrive40"),
            self.row("bmw-i5-cpo", self.tie, d, 48084, cpo="1"),
            # the siblings', of the same two cars — cheaper, and one not certified
            self.row("bmw-i5-xdrive40", self.both, d, 56000, trim="xDrive40"),
            self.row("bmw-i5-edrive40", self.tie, d, 48084, cpo="1"),
            # …and one car only the ordinary trim has
            self.row("bmw-i5-edrive40", "O" * 17, d, 39000),
        ]
        self.report, self.site, _ = T.build_outputs(rows, rows, T.build_history(rows))
        self.sections = {}
        for block in self.report.split("\n### ")[1:]:
            self.sections[block.split(" — ")[0]] = "### " + block

    def test_the_watch_counts_what_it_returned_not_what_won_a_tie_break(self):
        head = self.sections["CPO under 30k mi"].splitlines()[0]
        self.assertIn("2 vehicles", head,
                      "the watch returned two cars; the table filed one of them "
                      "under a sibling because that copy was cheaper and the "
                      "other on a tie")
        self.assertIn("lowest asking $48,084", head)

    def test_and_prints_each_car_at_the_price_its_own_query_returned(self):
        cpo = self.sections["CPO under 30k mi"]
        self.assertIn("$58,085", cpo,
                      "the certified listing of this car, not its cheaper "
                      "uncertified sibling record")
        self.assertNotIn("$56,000", cpo)
        x40 = self.sections["xDrive40"]
        self.assertIn("$56,000", x40, "and the sibling section prints its own")
        self.assertNotIn("$58,085", x40)

    def test_and_the_certification_follows_the_listing_it_belongs_to(self):
        row = next(b for b in self.sections["CPO under 30k mi"].split("\n- ")
                   if self.both in b)
        self.assertIn("CPO", row)
        row2 = next(b for b in self.sections["xDrive40"].split("\n- ")
                    if self.both in b)
        self.assertNotIn("CPO", row2,
                         "the sibling's record of this car is not certified, and "
                         "the flag is a fact about the listing")

    def test_the_model_line_says_how_many_cars_are_in_two_sections(self):
        line = next(l for l in self.report.splitlines() if "vehicles across" in l)
        self.assertIn("3 vehicles across 4 trims (2 listed under two of them)", line,
                      "the table holds three cars and the sections below add up "
                      "to five; the reader doing that subtraction is owed the "
                      "reason")

    def test_the_row_carries_the_other_query_s_listing_for_the_page(self):
        """The dashboard has one row per vehicle and cannot reach the sibling
        record without this, so its trim chip could only answer from the chosen
        copy — it counted 2 where the watch returned 4, and a comment in the
        page recorded that as a consequence of one row per VIN. It is not one."""
        i5 = self.site["brands"]["bmw"]["models"]["i5"]["listings"]
        both = next(x for x in i5 if x["vin"] == self.both)
        self.assertEqual(both["trim_id"], "bmw-i5-xdrive40", "the cheapest copy")
        self.assertEqual([(a["trim_id"], a["price"], a["cpo"]) for a in both["also"]],
                         [("bmw-i5-cpo", 58085, True)],
                         "and what the OTHER query returned it as, price and "
                         "certification together — membership alone would put an "
                         "uncertified $56,000 listing in the certified watch")
        alone = next(x for x in i5 if x["vin"] == "O" * 17)
        self.assertNotIn("also", alone,
                         "absent for a car one query alone returned, which is "
                         "almost all of them")

    def test_a_departure_from_a_watch_is_printed_under_that_watch(self):
        """The count and the rows behind it are one total split in two.
        brief_lines() counts a model's departures across every trim; the rows
        are printed only by trim_detail(), which build_outputs() skips entirely
        for a trim with no display rows. Split on the table's chosen copy, the
        certified watch could be empty on a day it had both cars and a
        departure — so the record said "4 gone" and showed two, and the two it
        swallowed were the watch's, on the most newsworthy day a watch can
        have. The sections are built from each query's own rows now; this is
        the arithmetic that says so."""
        import re
        from pathlib import Path
        d1 = date.fromordinal(T.TODAY_ORD - 1).isoformat()
        def r(target, vin, day, price, miles):
            row = {k: "" for k in T.FIELDS}
            row.update({"target": target, "vin": vin, "snapshot_date": day, "price": price,
                        "year": "2025", "trim": "eDrive40", "miles": miles, "state": "CA",
                        "city": "Irvine", "cpo": "1"})
            return row
        # The sibling's copy comes first, so the tie-break files the live car
        # under it and the watch's own section is the empty one — which is the
        # state the defect needed.
        rows = [r("bmw-i5-edrive40", "A" * 17, d1, 48000, 9000),
                r("bmw-i5-cpo", "A" * 17, d1, 48000, 9000),
                r("bmw-i5-edrive40", "A" * 17, T.TODAY, 48000, 9000),
                r("bmw-i5-cpo", "A" * 17, T.TODAY, 48000, 9000),
                r("bmw-i5-cpo", "B" * 17, d1, 52000, 5000)]
        today = [x for x in rows if x["snapshot_date"] == T.TODAY]
        was, log = dict(T.SHORTLIST), T.FETCH_LOG
        pw, ex, fs = dict(T.PRICE_WINDOW), set(T.EXHAUSTED), set(T.FAILED_SCOPES)
        T.SHORTLIST.clear()
        T.PRICE_WINDOW.clear(); T.EXHAUSTED.clear(); T.FAILED_SCOPES.clear()
        T.FETCH_LOG = Path("data/__no_such_fetch_log__.json")
        try:
            report, site, _ = T.build_outputs(today, rows, T.build_history(rows))
        finally:
            T.SHORTLIST.update(was); T.FETCH_LOG = log
            T.PRICE_WINDOW.clear(); T.PRICE_WINDOW.update(pw)
            T.EXHAUSTED.clear(); T.EXHAUSTED.update(ex)
            T.FAILED_SCOPES.clear(); T.FAILED_SCOPES.update(fs)
        brief = next(l for l in report.splitlines() if " on the market · " in l)
        said = int(re.search(r"· (\d+) gone", brief).group(1))
        self.assertEqual(said, 1, f"the precondition: one real departure. {brief}")
        printed = 0
        for m in re.finditer(r"^\*\*Gone since [^*]*\*\*$", report, re.M):
            block = report[m.end():]
            block = block[:block.index("\n\n")] if "\n\n" in block else block
            printed += len(re.findall(r"^- ", block, re.M))
        self.assertEqual(printed, said,
                         f"{said} gone in the model's own line and {printed} rows "
                         f"under the sections that are supposed to hold them\n{report}")
        self.assertIn("B" * 17, report,
                      "and it is the watch's departure, under the watch")

    def test_the_table_is_still_one_row_per_vehicle_at_the_cheapest_price(self):
        i5 = self.site["brands"]["bmw"]["models"]["i5"]["listings"]
        self.assertEqual(len(i5), 3)
        both = next(x for x in i5 if x["vin"] == self.both)
        self.assertEqual(both["price"], 56000,
                         "the table's rule is unchanged: one row per vehicle, "
                         "the cheapest copy")


class TestWindowArithmetic(unittest.TestCase):
    """Three lines of delisted() that decide every departure, and no test ran
    any of them: which cut-off applies when a car is reachable through two
    queries, and whether a car sitting exactly ON the cut-off is inside it.
    """

    def setUp(self):
        self._pw, self._ex = dict(T.PRICE_WINDOW), set(T.EXHAUSTED)
        self._fs, self._log = set(T.FAILED_SCOPES), T.FETCH_LOG
        T.PRICE_WINDOW.clear(); T.EXHAUSTED.clear(); T.FAILED_SCOPES.clear()
        T.FETCH_LOG = Path("data/__no_such_fetch_log__.json")

    def tearDown(self):
        T.PRICE_WINDOW.clear(); T.PRICE_WINDOW.update(self._pw)
        T.EXHAUSTED.clear(); T.EXHAUSTED.update(self._ex)
        T.FAILED_SCOPES.clear(); T.FAILED_SCOPES.update(self._fs)
        T.FETCH_LOG = self._log

    @staticmethod
    def row(tid, vin, day, price, state="IL"):
        r = {k: "" for k in T.FIELDS}
        r.update({"target": tid, "vin": vin, "snapshot_date": day, "price": price,
                  "year": "2024", "miles": 10000, "state": state, "city": "Chicago"})
        return r

    def _verdict(self, tid, price, state="IL"):
        d1, d2 = "2026-08-01", T.TODAY
        rows = [self.row(tid, "G" * 17, d1, price, state),
                self.row(tid, "K" * 17, d1, 90000, state),
                self.row(tid, "K" * 17, d2, 90000, state)]
        today = [r for r in rows if r["snapshot_date"] == d2]
        got = T.delisted({tid}, rows, today, T.build_history(rows))
        return {g["vin"]: g for g in got}["G" * 17]["likely"]

    def test_the_wider_of_two_windows_is_the_one_that_applies(self):
        """A car in a searched state comes back through EITHER query, so it is
        only out of window when it is above BOTH cut-offs — the max, not the
        min. Taking the min calls a car gone that the National query would
        have returned; taking the States one alone is the same mistake with a
        different name.
        """
        tid = next(t["id"] for t in T.TARGETS.values()
                   if not t.get("national_only") and t["id"] in T.TARGETS)
        T.PRICE_WINDOW[(tid, "States")] = 45000
        T.PRICE_WINDOW[(tid, "National")] = 70000
        self.assertEqual(self._verdict(tid, 60000, "IL"), "delisted",
                         "$60,000 is above the States cut-off and below the National "
                         "one, and National could have returned it — so its absence "
                         "is a real departure")
        self.assertEqual(self._verdict(tid, 80000, "IL"), "out of window",
                         "above BOTH cut-offs, neither query could have returned it")

    def test_a_car_exactly_on_the_cut_off_is_inside_it(self):
        """A genuinely arguable boundary, pinned deliberately.

        The cut-off IS the value of a row the fetch returned, so the query
        demonstrably reached that far — a car at the same value was in view and
        did not come back. Equality therefore counts as in-view and its absence
        is a departure. Written down because the alternative reads like a
        harmless tightening: a later `>=` would silently reclassify every car
        that ties the cheapest-N boundary as merely unfetched.
        """
        tid = next(t["id"] for t in T.TARGETS.values() if not t.get("national_only"))
        T.PRICE_WINDOW[(tid, "States")] = 50000
        T.PRICE_WINDOW[(tid, "National")] = 50000
        self.assertEqual(self._verdict(tid, 50000, "IL"), "delisted")
        self.assertEqual(self._verdict(tid, 50001, "IL"), "out of window")

    def test_a_car_outside_the_searched_states_is_judged_on_national_alone(self):
        """The States query can only return cars in those states, so it says
        nothing about a car in Arizona — judging one against the States cut-off
        tests it against a query that never had a chance of returning it."""
        tid = next(t["id"] for t in T.TARGETS.values() if not t.get("national_only"))
        T.PRICE_WINDOW[(tid, "States")] = 90000
        T.PRICE_WINDOW[(tid, "National")] = 45000
        self.assertEqual(self._verdict(tid, 60000, "AZ"), "out of window",
                         "above National's cut-off, and States could never have "
                         "returned an Arizona car whatever its window was")


class TestSummarize(unittest.TestCase):
    """The per-car price history every surface reads, and nothing tested it.

    cuts, delta, days_tracked and first_seen come from here and are printed on
    the report line, the row's movement chip, the sparkline's direction and the
    "tracked Nd" tag. The only coverage was incidental — assertions about rows
    that happened to have passed through it — so both the strict-inequality in
    the cut counter and the sign of delta were free to flip.
    """

    def test_it_counts_only_the_steps_that_went_down(self):
        hist = {("t", "V"): [("d1", 50000), ("d2", 50000), ("d3", 48000)]}
        got = T.summarize(("t", "V"), hist)
        self.assertEqual(got["cuts"], 1, "a flat day is not a cut")
        self.assertEqual(got["delta"], -2000, "delta is last minus first, and it went down")
        self.assertEqual(got["days_tracked"], 3)
        self.assertEqual(got["first_seen"], "d1")

    def test_a_flat_series_has_no_cuts_and_no_delta(self):
        hist = {("t", "V"): [("d1", 50000), ("d2", 50000), ("d3", 50000)]}
        got = T.summarize(("t", "V"), hist)
        self.assertEqual((got["cuts"], got["delta"]), (0, 0))

    def test_a_rise_is_a_positive_delta_and_not_a_cut(self):
        hist = {("t", "V"): [("d1", 48000), ("d2", 50000)]}
        got = T.summarize(("t", "V"), hist)
        self.assertEqual((got["cuts"], got["delta"]), (0, 2000))

    def test_a_car_with_no_history_answers_with_an_empty_series_only(self):
        """The early return, which nothing asserted: callers spread this dict,
        so an extra key here becomes a listing field nobody meant to export."""
        self.assertEqual(T.summarize(("t", "MISSING"), {}), {"series": []})


class TestDelisted(unittest.TestCase):
    def setUp(self):
        self._pw, self._ex = dict(T.PRICE_WINDOW), set(T.EXHAUSTED)
        self._fs, self._log = set(T.FAILED_SCOPES), T.FETCH_LOG
        T.PRICE_WINDOW.clear()
        T.EXHAUSTED.clear()
        T.FAILED_SCOPES.clear()
        # delisted() reads the committed fetch log; point it at nothing so a
        # test says what it means rather than what today's log happens to hold
        T.FETCH_LOG = Path("data/__no_such_fetch_log__.json")

    def tearDown(self):
        T.PRICE_WINDOW.clear()
        T.PRICE_WINDOW.update(self._pw)
        T.EXHAUSTED.clear()
        T.EXHAUSTED.update(self._ex)
        T.FAILED_SCOPES.clear()
        T.FAILED_SCOPES.update(self._fs)
        T.FETCH_LOG = self._log

    @contextlib.contextmanager
    def fetch_log(self, day, facts):
        """A fetch log on disk for one day, as save_fetch_log writes it."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "fetch_log.json"
            p.write_text(json.dumps({day: facts}))
            was, T.FETCH_LOG = T.FETCH_LOG, p
            try:
                yield
            finally:
                T.FETCH_LOG = was

    @staticmethod
    def row(tid, vin, day, price, state="IL", miles=10000):
        r = {k: "" for k in T.FIELDS}
        r.update({"target": tid, "vin": vin, "snapshot_date": day,
                  "price": price, "year": "2024", "miles": miles,
                  "state": state, "city": "Chicago"})
        return r

    def test_a_departure_carries_how_its_label_was_reached(self):
        """`exact` is the row saying whether a query actually looked.

        Downstream, sale_stats() counts a departure as a car that left only
        where this is true — so it has to distinguish the three ways delisted()
        arrives at a label, and it cannot be re-derived later: after the fact,
        an offline guess and a logged certainty look identical on the row.
        """
        two = next(t for t in T.TARGETS.values() if not T.window_reconstructable(t))
        tid, d1, d2 = two["id"], "2026-08-01", "2026-08-02"
        # a cheap car vanishes; the day's other rows reach far past its price,
        # so on a ONE-window target this would be a confident delisting
        rows = [self.row(tid, "G" * 17, d1, 40000),
                self.row(tid, "K" * 17, d1, 90000),
                self.row(tid, "K" * 17, d2, 90000)]
        got = {g["vin"]: g for g in T.delisted({tid}, rows,
                                               [r for r in rows if r["snapshot_date"] == d2],
                                               T.build_history(rows))}
        g = got["G" * 17]
        self.assertFalse(g["exact"],
                         "two windows pooled into one day is a guess, and the row must say so")
        self.assertFalse(T.departure_is_evidence(g))
        self.assertEqual(T.sale_stats([g])["n_exits"], 0,
                         "and nothing downstream may count it as a car that left")

    def test_the_offline_path_never_calls_a_two_window_absence_a_delisting(self):
        """The invariant that makes `exact` False unreachable in that branch.

        Offline — no live windows, no fetch log — a two-window target's day is
        one pooled maximum over two different cut-offs. A car ABOVE it was
        above both, so "out of window" is provable; at or below it the record
        cannot say, so the answer is "not checked". Neither is a delisting.
        If that ever loosens, `exact = window_reconstructable(t)` is what stops
        the loosened label from being counted as a car that left — and this is
        what will tell you the day it starts mattering.
        """
        two = next(t for t in T.TARGETS.values() if not T.window_reconstructable(t))
        tid, d1, d2 = two["id"], "2026-08-01", "2026-08-02"
        for price in (10000, 40000, 95000):     # below, inside, above the pooled max
            rows = [self.row(tid, "G" * 17, d1, price),
                    self.row(tid, "K" * 17, d1, 90000),
                    self.row(tid, "K" * 17, d2, 90000)]
            got = {g["vin"]: g for g in T.delisted({tid}, rows,
                                                   [r for r in rows if r["snapshot_date"] == d2],
                                                   T.build_history(rows))}
            g = got["G" * 17]
            self.assertNotEqual(g["likely"], "delisted",
                                f"a ${price:,} absence on {tid} is not a departure the rows can prove")
            self.assertEqual(T.sale_stats([g])["n_exits"], 0)

    def test_a_reconstructed_departure_on_a_one_window_target_is_exact(self):
        """…and the other side of the same line: a single-window target's
        offline reconstruction IS defensible, and must be counted."""
        one = next(t for t in T.TARGETS.values()
                   if T.window_reconstructable(t) and not t.get("national_only"))
        tid, d1, d2 = one["id"], "2026-08-01", "2026-08-02"
        rows = [self.row(tid, "G" * 17, d1, 40000),
                self.row(tid, "K" * 17, d1, 90000),
                self.row(tid, "K" * 17, d2, 90000)]
        got = {g["vin"]: g for g in T.delisted({tid}, rows,
                                               [r for r in rows if r["snapshot_date"] == d2],
                                               T.build_history(rows))}
        g = got["G" * 17]
        self.assertEqual(g["likely"], "delisted")
        self.assertTrue(g["exact"])
        self.assertEqual(T.sale_stats([g])["n_exits"], 1)

    def test_a_logged_departure_is_exact_even_on_a_two_window_target(self):
        """The reason the gate reads the row and not the target's shape: the
        run wrote down what each query reached, so a rebuild can be certain
        about a target whose config alone could never be."""
        two = next(t for t in T.TARGETS.values() if not T.window_reconstructable(t))
        tid, d1, d2 = two["id"], "2026-08-01", "2026-08-02"
        rows = [self.row(tid, "G" * 17, d1, 40000),
                self.row(tid, "K" * 17, d1, 90000),
                self.row(tid, "K" * 17, d2, 90000)]
        facts = {tid: {k: {"window": 95000, "exhausted": False, "failed": False, "raw": 20}
                       for k in ("States", "National")}}
        with self.fetch_log(d2, facts):
            got = {g["vin"]: g for g in T.delisted({tid}, rows,
                                                   [r for r in rows if r["snapshot_date"] == d2],
                                                   T.build_history(rows))}
        g = got["G" * 17]
        self.assertEqual(g["likely"], "delisted")
        self.assertTrue(g["exact"], "the log says both queries reached past it and neither returned it")
        self.assertTrue(T.departure_is_evidence(g))

    def test_an_unanswerable_day_is_never_exact(self):
        """"Not checked" is the absence of an answer, and an absence must not
        be counted as one — whatever the target's shape."""
        one = next(t for t in T.TARGETS.values() if T.window_reconstructable(t))
        tid, d1, d2 = one["id"], "2026-08-01", "2026-08-02"
        facts = {tid: {k: {"window": None, "exhausted": False, "failed": True, "raw": 0}
                       for k in ("States", "National")}}
        rows = [self.row(tid, "G" * 17, d1, 40000),
                self.row(tid, "K" * 17, d1, 90000),
                self.row(tid, "K" * 17, d2, 90000)]
        with self.fetch_log(d2, facts):
            got = {g["vin"]: g for g in T.delisted({tid}, rows,
                                                   [r for r in rows if r["snapshot_date"] == d2],
                                                   T.build_history(rows))}
        g = got["G" * 17]
        self.assertEqual(g["likely"], "not checked")
        self.assertFalse(g["exact"])

    @staticmethod
    def days_ago(n):
        return date.fromordinal(T.TODAY_ORD - n).isoformat()

    def test_slow_trim_departures_carry_their_own_prev_fetch_day(self):
        d2, d1 = self.days_ago(2), self.days_ago(1)
        fast, slow = "bmw-i5-edrive40", "bmw-i5-xdrive40"
        all_rows = ([self.row(fast, "F1", d, 45000) for d in (d2, d1, T.TODAY)]
                    + [self.row(slow, "S1", d2, 48000),
                       self.row(slow, "S2", d2, 50000),
                       self.row(slow, "S2", T.TODAY, 50000)])
        today = [r for r in all_rows if r["snapshot_date"] == T.TODAY]
        T.PRICE_WINDOW[(slow, "National")] = 60000       # window above S1's price
        gone = T.delisted({fast, slow}, all_rows, today,
                          T.build_history(all_rows))
        g = next(x for x in gone if x["vin"] == "S1")
        self.assertEqual(g["likely"], "delisted")
        self.assertEqual(g["last_seen"], d2)
        self.assertEqual(g["prev_fetch_day"], d2,
                         "the slow trim's previous fetch is two days ago — "
                         "compared against the model's yesterday it would "
                         "never be reported gone")

    def test_exhaustive_query_turns_out_of_window_into_delisted(self):
        d1, tid = self.days_ago(1), "bmw-i5-m60"
        all_rows = [self.row(tid, "V1", d1, 55000),
                    self.row(tid, "V2", T.TODAY, 40000)]
        today = [r for r in all_rows if r["snapshot_date"] == T.TODAY]
        T.PRICE_WINDOW[(tid, "National")] = 40000        # V1 sits above the window
        hist = T.build_history(all_rows)
        self.assertEqual(T.delisted({tid}, all_rows, today, hist)[0]["likely"],
                         "out of window")
        T.EXHAUSTED.add((tid, "States"))                 # the States query saw everything
        self.assertEqual(T.delisted({tid}, all_rows, today, hist)[0]["likely"],
                         "delisted")

    def test_rebuild_reconstructs_the_window_from_history(self):
        # No live fetch signals at all — the offline-rebuild situation that
        # used to mark every departure 'unknown'. The snapshot history keeps
        # every kept row per fetch day, so the vanish day's max kept price
        # IS that day's cheapest-N cut-off — but ONLY on a target that opens
        # one window on that axis. This one is `light`, so it fetches
        # price.asc alone and every kept row is inside the price window.
        # (It used to be written against bmw-i5-edrive40, which also fetches
        # miles.asc and a newest-first page: rows from those sit ABOVE the
        # price cut-off, so the premise this test states was false for the
        # very target it was asserting it on. The two-sort case is now its
        # own test, one line down, and it refuses to claim.)
        d2, d1, tid = self.days_ago(2), self.days_ago(1), "bmw-i5-m60"
        self.assertTrue(T.window_reconstructable(T.TARGETS[tid]))
        fill = [self.row(tid, f"W{i:02d}", d, 30000 + i * 500)
                for d in (d2, d1) for i in range(T.PER_PAGE)]
        all_rows = fill + [self.row(tid, "HIGH", d2, 41000),
                           self.row(tid, "LOW", d2, 31250)]
        today = [r for r in all_rows if r["snapshot_date"] == d1]
        gone = {g["vin"]: g for g in T.delisted({tid}, all_rows, today,
                                                T.build_history(all_rows))}
        # d1 kept a full page (20 rows, max 39,500): above it is an artifact,
        # below it is a car the fetch should have seen — a real departure
        self.assertEqual(gone["HIGH"]["likely"], "out of window")
        self.assertEqual(gone["LOW"]["likely"], "delisted")

    def test_miles_window_target_judges_departures_in_miles(self):
        # The CPO watches fetch miles.asc only, so their window is bounded
        # in MILES: a departed car with more miles than the vanish day's
        # deepest kept row may simply sit beyond the pages fetched, while
        # one with fewer was definitely in view — its absence is real.
        # Judging these by a price cut-off would compare against a number
        # that never gated anything.
        d2, d1, tid = self.days_ago(2), self.days_ago(1), "bmw-i5-cpo"
        self.assertEqual(T.window_dim(T.TARGETS[tid]), "miles")
        # d1's kept rows reach 17,700 miles and fill a page, so the window
        # neither exhausted nor reached the two departed cars' mileages…
        fill = [self.row(tid, f"W{i:02d}", d, 45000, miles=12000 + i * 300)
                for d in (d2, d1) for i in range(T.PER_PAGE)]
        all_rows = fill + [
            self.row(tid, "LOWMI", d2, 47000, miles=9000),    # below the window: was in view
            self.row(tid, "HIGHMI", d2, 39000, miles=25000),  # beyond it: maybe just unfetched
        ]
        today = [r for r in all_rows if r["snapshot_date"] == d1]
        gone = {g["vin"]: g for g in T.delisted({tid}, all_rows, today,
                                                T.build_history(all_rows))}
        self.assertEqual(gone["LOWMI"]["likely"], "delisted")
        self.assertEqual(gone["HIGHMI"]["likely"], "out of window")
        # note the price ordering would have said the OPPOSITE: HIGHMI was
        # the cheaper car, LOWMI the pricier one
        self.assertLess(gone["HIGHMI"]["last_price"], gone["LOWMI"]["last_price"])

    def test_departures_carry_the_history_the_scoped_chart_needs(self):
        # The dashboard rebuilds "lowest asking per day" over whatever scope
        # the reader has filtered to, and it can only do that honestly if a
        # departed car still carries the days it was on the market. Drop the
        # series and the past gets rebuilt from survivors alone: the cheap car
        # that sold on Tuesday vanishes from Monday too, so every old floor
        # reads higher than it was. accidents and usage ride along so the
        # clean and no-rental filters judge a departure by the same rule as a
        # live listing instead of silently keeping it.
        d2, d1, tid = self.days_ago(2), self.days_ago(1), "bmw-i5-edrive40"
        # the facts come off the car's LAST snapshot row, not its first
        last = self.row(tid, "GONE", d1, 30500)
        last.update({"accidents": "2", "usage": "Rental Use"})
        all_rows = [self.row(tid, "GONE", d2, 31000), last,
                    self.row(tid, "STAY", d1, 44000),
                    self.row(tid, "STAY", T.TODAY, 44000)]
        today = [x for x in all_rows if x["snapshot_date"] == T.TODAY]
        g = T.delisted({tid}, all_rows, today,
                       T.build_history(all_rows))[0]
        self.assertEqual(g["vin"], "GONE")
        self.assertEqual(g["series"], [(d2, 31000), (d1, 30500)],
                         "a departure without its series makes every day it "
                         "was on the market look more expensive than it was")
        self.assertEqual(g["accidents"], 2)
        self.assertEqual(g["usage"], "Rental Use")

    def test_a_short_day_is_only_exhaustive_if_the_fetch_said_so(self):
        """Five kept rows is not a short page.

        EXHAUSTED is set from the RAW length of a page, and every target
        filters after the fetch — trim_match, years, min_price, and on the CPO
        watches cpo_only and max_miles. bmw-i5-cpo keeps 6 records out of 40
        raw ones on a normal day, so "fewer than PER_PAGE rows survived" says
        nothing whatever about whether the query saw its whole scope. Reading
        it as exhaustion is what turned every one of that target's days into a
        day on which every absence was a confirmed departure — on a
        single-sort target, which then publishes an exit price from them.

        So the count no longer decides it. The run writes down what each query
        actually did (save_fetch_log), and only that record can call a day
        exhaustive.
        """
        d2, d1, tid = self.days_ago(2), self.days_ago(1), "bmw-i5-m60"
        fill = [self.row(tid, f"W{i}", d, 40000 + i * 1000)
                for d in (d2, d1) for i in range(5)]
        all_rows = fill + [self.row(tid, "HIGH", d2, 90000)]
        today = [r for r in all_rows if r["snapshot_date"] == d1]
        gone = T.delisted({tid}, all_rows, today, T.build_history(all_rows))
        self.assertEqual(gone[0]["likely"], "out of window",
                         "five kept rows must not be read as a short page")
        # …and with the run's own record saying the query WAS exhaustive, the
        # same absence is a real departure whatever the car was asking.
        facts = {tid: {"States": {"window": 44000, "exhausted": True, "failed": False},
                       "National": {"window": 44000, "exhausted": True, "failed": False}}}
        with self.fetch_log(d1, facts):
            gone = T.delisted({tid}, all_rows, today, T.build_history(all_rows))
        self.assertEqual(gone[0]["likely"], "delisted")

    def test_departure_is_judged_at_its_own_vanish_day_not_today(self):
        d2, d1, tid = self.days_ago(2), self.days_ago(1), "bmw-i7-edrive50"
        fill = [self.row(tid, f"W{i:02d}", d, 30000 + i * 100)
                for d in (d2, d1, T.TODAY) for i in range(T.PER_PAGE)]
        all_rows = fill + [self.row(tid, "V1", d2, 35000)]
        today = [r for r in all_rows if r["snapshot_date"] == T.TODAY]
        T.PRICE_WINDOW[(tid, "National")] = 99999    # today's window is huge
        gone = T.delisted({tid}, all_rows, today, T.build_history(all_rows))
        self.assertEqual(gone[0]["likely"], "out of window",
                         "V1 vanished at the d1 fetch, whose cut-off was "
                         "$31,900 — today's wider window must not turn an "
                         "old artifact into a confirmed sale")

    def test_what_makes_a_window_reconstructable(self):
        """Both halves of the predicate, including the one no shipped target
        exercises today.

        A window can be rebuilt from the snapshot rows only when every kept row
        of that day came through one query shape on the window's own axis. Two
        things break it and each is checked here on a target built for the
        purpose, because the watchlist happens to carry no single-sort target
        that also runs a newest probe — and an unexercised clause is one a
        later edit deletes without anything going red.
        """
        base = dict(T.TARGETS["bmw-i5-m60"])
        light = {**base, "depth": "light", "sorts": ["price.asc", "miles.asc"], "newest": 0}
        self.assertEqual(T.sorts_pages(light)[0], ["price.asc"],
                         "light depth fetches the FIRST configured sort only")
        self.assertTrue(T.window_reconstructable(light),
                        "one sort actually fetched, no newest probe")
        deep = {**light, "depth": "full", "pages": 2}
        self.assertFalse(T.window_reconstructable(deep),
                         "a second sort puts rows above this axis's cut-off "
                         "into the same day")
        probing = {**light, "newest": 1}
        self.assertFalse(T.window_reconstructable(probing),
                         "a newest-first page returns cars at any price, so "
                         "the widest kept row is not the cut-off")

    def test_a_two_sort_target_cannot_reconstruct_its_window(self):
        """The 24 departures a rebuild invented.

        bmw-i7-xdrive60 and friends keep rows from price.asc AND miles.asc, and
        the shopped trims add a newest-first page on top. All three land in the
        same snapshot day, so the widest kept price is a delivery-mileage 2026
        car or a car that listed this morning — not the price cut-off. Judging
        an absence against it says "inside the window, so it is gone" about
        cars that were never inside anything.

        Measured, not supposed: rebuilding the committed 2026-09-01 outputs
        flipped 24 departures from 'out of window' to 'delisted' against the
        live run of the same day, and took the report's headline from "9 gone
        since the last fetch on the shopped models" to "31". Every dispatch
        rebuilds (daily.yml), so those were the published numbers.
        """
        d2, d1, tid = self.days_ago(2), self.days_ago(1), "bmw-i5-edrive40"
        self.assertFalse(T.window_reconstructable(T.TARGETS[tid]))
        fill = [self.row(tid, f"W{i:02d}", d, 30000 + i * 500)
                for d in (d2, d1) for i in range(T.PER_PAGE)]
        all_rows = fill + [self.row(tid, "HIGH", d2, 41000),
                           self.row(tid, "LOW", d2, 31250)]
        today = [r for r in all_rows if r["snapshot_date"] == d1]
        gone = {g["vin"]: g for g in T.delisted({tid}, all_rows, today,
                                                T.build_history(all_rows))}
        self.assertEqual(gone["HIGH"]["likely"], "out of window",
                         "above the widest kept row is above every window — "
                         "that much the rows still prove")
        self.assertEqual(gone["LOW"]["likely"], "not checked",
                         "below it the record cannot tell a departure from a "
                         "car the price query never reached")
        # and with the day's own fetch record, the same car is judged exactly
        facts = {tid: {"States": {"window": 39500, "exhausted": False, "failed": False},
                       "National": {"window": 39500, "exhausted": False, "failed": False}}}
        with self.fetch_log(d1, facts):
            gone = {g["vin"]: g for g in T.delisted({tid}, all_rows, today,
                                                    T.build_history(all_rows))}
        self.assertEqual(gone["LOW"]["likely"], "delisted")
        self.assertEqual(gone["HIGH"]["likely"], "out of window")

    def test_a_car_beyond_the_queried_states_is_not_judged_by_the_states_window(self):
        """A California car never had a States query to come back through.

        The States query asks for buyer.states plus search_states and nothing
        else, so a car outside them can only return through National — whose
        cut-off is the N-th cheapest in the country and runs thousands below
        the States one, which only has to reach the N-th cheapest in eight
        states. Pooling the two judged the California car against the Illinois
        cut-off and called it sold.

        What the rows still prove is bounded on both sides: a kept row from
        outside the queried states came back through National, so National
        reached at least that far; and no window is wider than the widest kept
        row of the day. Between those two the record is silent.
        """
        d2, d1, tid = self.days_ago(2), self.days_ago(1), "bmw-i5-m60"
        # an in-state page reaching $60k, and out-of-state cars only to $45k
        rows = [self.row(tid, f"IL{i}", d, 40000 + i * 2000, state="IL")
                for d in (d2, d1) for i in range(11)]
        rows += [self.row(tid, f"CA{i}", d, 41000 + i * 2000, state="CA")
                 for d in (d2, d1) for i in range(3)]
        # three departed California cars: below National's proven reach,
        # inside the uncertain band, and above every window
        rows += [self.row(tid, "CALOW", d2, 42000, state="CA"),
                 self.row(tid, "CAMID", d2, 52000, state="CA"),
                 self.row(tid, "CAHIGH", d2, 99000, state="CA")]
        today = [r for r in rows if r["snapshot_date"] == d1]
        gone = {g["vin"]: g for g in T.delisted({tid}, rows, today,
                                                T.build_history(rows))}
        self.assertEqual(gone["CALOW"]["likely"], "delisted",
                         "National kept a $45,000 California car that day, so "
                         "it reached past $42,000")
        self.assertEqual(gone["CAMID"]["likely"], "not checked",
                         "between National's proven reach and the widest kept "
                         "row, the record cannot say — and the Illinois "
                         "cut-off is not evidence about a California car")
        self.assertEqual(gone["CAHIGH"]["likely"], "out of window")

    def test_a_query_that_failed_is_not_an_empty_market(self):
        """fetch() returning None means unknown; it used to mean gone.

        When one source fails after its retry the loop keeps what it has, and
        delisted() had no way to know a scope had gone silent: it judged the
        absence against whatever the OTHER query returned. Driven live with
        National dead, bmw-i7-edrive50 published 93 departures where the real
        run had 9.
        """
        d1, tid = self.days_ago(1), "bmw-i5-m60"
        rows = [self.row(tid, f"IL{i}", d, 40000 + i * 500, state="IL")
                for d in (d1, T.TODAY) for i in range(5)]
        # two Illinois departures: one inside the window the surviving query
        # reached, one above it
        rows += [self.row(tid, "INSIDE", d1, 41000, state="IL"),
                 self.row(tid, "ABOVE", d1, 50000, state="IL")]
        today = [r for r in rows if r["snapshot_date"] == T.TODAY]
        T.PRICE_WINDOW[(tid, "States")] = 42000        # the States query answered
        T.FAILED_SCOPES.add((tid, "National"))         # the National one did not
        gone = {g["vin"]: g for g in
                T.delisted({tid}, rows, today, T.build_history(rows))}
        self.assertEqual(gone["ABOVE"]["likely"], "not checked",
                         "the National query might have been the one that "
                         "reached this car, and it never answered")
        # …but a failure elsewhere does not un-see what the surviving query saw
        self.assertEqual(gone["INSIDE"]["likely"], "delisted",
                         "the States query reached past $41,000 and did not "
                         "return it — National failing changes nothing there")

    def test_never_fetched_again_is_not_checked(self):
        d1, tid = self.days_ago(1), "bmw-i5-edrive40"
        all_rows = [self.row(tid, "V1", d1, 45000)]
        gone = T.delisted({tid}, all_rows, [], T.build_history(all_rows))
        self.assertEqual(gone[0]["likely"], "not checked")


# --------------------------------------------------------------------------
# Market stats: the negotiation context — how long cars sit, how often and
# how much they get cut, and each car's staleness within its own model.
# --------------------------------------------------------------------------
class TestNarrowingTheWatchlistIsNotAMarketEvent(unittest.TestCase):
    """A car the config stopped asking for did not leave the market.

    `years` is sent to the API and also filtered client-side, so narrowing it
    makes every stored car outside the new range stop coming back — and every
    test in delisted() then reads that absence as a query having looked and
    not found the car. Measured on this repo's own record before the rule
    existed: restricting the watchlist to 2024 and newer retires 35 of the 37
    cars bmw-i7-xdrive60 was holding, 26 of bmw-ix-m's 36 and 23 of
    bmw-ix-xdrive's 30, and all 84 would have been published as "GONE — the
    listing ended", dated to the night of a config edit, counted by
    sale_stats() as cars that left the market and priced by exit_stats().

    The word is "out of scope" and it is decided before any window test,
    because after the edit the queries were asking a different question.
    """

    def setUp(self):
        self._pw, self._ex = dict(T.PRICE_WINDOW), set(T.EXHAUSTED)
        self._fs, self._log = set(T.FAILED_SCOPES), T.FETCH_LOG
        T.PRICE_WINDOW.clear(); T.EXHAUSTED.clear(); T.FAILED_SCOPES.clear()
        T.FETCH_LOG = Path("data/__no_such_fetch_log__.json")
        self.tid = "bmw-i5-edrive40"
        self._years = list(T.TARGETS[self.tid]["years"])

    def tearDown(self):
        T.PRICE_WINDOW.clear(); T.PRICE_WINDOW.update(self._pw)
        T.EXHAUSTED.clear(); T.EXHAUSTED.update(self._ex)
        T.FAILED_SCOPES.clear(); T.FAILED_SCOPES.update(self._fs)
        T.FETCH_LOG = self._log
        T.TARGETS[self.tid]["years"] = self._years

    def row(self, vin, day, year, price=45000, miles=10000):
        r = {k: "" for k in T.FIELDS}
        r.update({"target": self.tid, "vin": vin, "snapshot_date": day,
                  "price": price, "year": str(year), "miles": miles,
                  "state": "IL", "city": "Chicago"})
        return r

    def _verdict(self, year, years, price=45000, miles=10000):
        """One car seen on an earlier day and not on the latest, judged by a
        query that saw its whole scope — the strongest case there is for
        calling an absence a departure."""
        T.TARGETS[self.tid]["years"] = years
        d1, d2 = "2026-08-01", T.TODAY
        gone = self.row("G" * 17, d1, year, price, miles)
        gone["listed_since"] = "2026-07-01"
        rows = [gone,
                self.row("K" * 17, d1, 2025), self.row("K" * 17, d2, 2025)]
        today = [r for r in rows if r["snapshot_date"] == d2]
        T.EXHAUSTED.add((self.tid, "States"))
        T.EXHAUSTED.add((self.tid, "National"))
        out = T.delisted({self.tid}, rows, today, T.build_history(rows))
        return {g["vin"]: g for g in out}["G" * 17]

    def test_a_model_year_the_watchlist_dropped_is_not_a_departure(self):
        self.assertEqual(self._verdict(2023, ["2024", "2025", "2026"])["likely"],
                         "out of scope",
                         "a 2023 car on a watch that now asks for 2024+ did not "
                         "leave the market — the query left it")

    def test_the_same_car_inside_the_years_is_still_a_departure(self):
        """The half that makes the test above load-bearing.

        Without it, `likely = "out of scope"` unconditionally would pass — and
        so would a rule that never fires, since both would be checked only on
        the side they were written for. This is the same setup, one field
        different, and it must reach the opposite word.
        """
        self.assertEqual(self._verdict(2024, ["2024", "2025", "2026"])["likely"],
                         "delisted",
                         "an exhaustive query looked for a 2024 car on a 2024+ "
                         "watch and did not find it: that is a real departure")

    def test_a_watch_naming_no_years_still_judges_every_car(self):
        """`years: []` means "every year", and an empty list is falsy — so a
        predicate written as `if year not in t["years"]` would call every car
        on an unrestricted watch out of scope. Six of this repo's targets
        inherit no years at all."""
        self.assertEqual(self._verdict(2019, [])["likely"], "delisted")

    def test_a_row_with_no_year_is_judged_by_the_window_not_by_the_config(self):
        """A blank year cannot be shown to be outside anything. Guessing
        "out of scope" there would hide a real departure behind a missing
        field — the same silence-into-a-verdict move fetch_log_row() exists
        to stop."""
        T.TARGETS[self.tid]["years"] = ["2024", "2025", "2026"]
        d1, d2 = "2026-08-01", T.TODAY
        blank = self.row("G" * 17, d1, "")
        rows = [blank, self.row("K" * 17, d1, 2025), self.row("K" * 17, d2, 2025)]
        T.EXHAUSTED.add((self.tid, "States"))
        T.EXHAUSTED.add((self.tid, "National"))
        out = T.delisted({self.tid}, rows,
                         [r for r in rows if r["snapshot_date"] == d2],
                         T.build_history(rows))
        self.assertEqual({g["vin"]: g for g in out}["G" * 17]["likely"], "delisted")

    def test_the_narrowness_is_deliberate_price_and_mileage_are_not_this(self):
        """min_price, max_miles and cpo_only are NOT watchlist moves.

        A car's asking price, odometer and certification all change while it
        sits, so a stored row outside those may be a car that genuinely left
        the tracked market — which is exactly the reading delisted()'s own
        docstring argues for the CPO watches, and it has to survive this. Only
        the model year is fixed at the factory and stored verbatim, so only
        the model year can prove the config moved rather than the car.
        """
        T.TARGETS[self.tid]["years"] = ["2024", "2025", "2026"]
        floor = T.TARGETS[self.tid].get("min_price", 0)
        self.assertEqual(
            self._verdict(2024, ["2024", "2025", "2026"], price=max(1, floor - 1))["likely"],
            "delisted",
            "a car below today's min_price is still judged on the market, not "
            "on the config")
        self.assertEqual(
            self._verdict(2024, ["2024", "2025", "2026"], miles=400000)["likely"],
            "delisted",
            "and so is one past any mileage cap")

    def test_the_word_reaches_no_number_the_record_publishes(self):
        """Every consumer of a departure requires the word "delisted", so a
        new word is excluded from the sale spans, the exit prices, the cohort
        test and the events feed by construction. This is what says so — and
        the delisted control beside it is what stops it passing on an empty
        list, which is how a rule that counted nothing at all would look.
        """
        moved = self._verdict(2023, ["2024", "2025", "2026"])
        kept = self._verdict(2024, ["2024", "2025", "2026"])
        self.assertEqual(T.sale_stats([moved]).get("n_departures", 0), 0)
        self.assertEqual(T.sale_stats([kept]).get("n_departures", 0), 1,
                         "the control: an identical row inside the years IS "
                         "counted, so the assertion above is about the word "
                         "and not about an empty list")
        self.assertFalse([g for g in [moved] if g["likely"] == "delisted"])

    def test_the_report_names_the_watchlist_rather_than_the_market(self):
        """The shortlist line for such a car must not read "missing".

        The verdict table is a dict with a fallback, so an unknown word does
        not crash — it prints "missing <day>", which is a claim about the
        market on a car the market never lost.
        """
        src = Path("Tracking.py").read_text()
        i = src.index('"out of scope": ')
        sentence = src[i:src.index("}.get(", i)]
        self.assertIn("no longer watched", sentence)
        self.assertNotIn("missing", sentence)


class TestEveryVersionThePagesCiteIsTheOneTheyLoad(unittest.TestCase):
    """The pin is data now, so the prose about it can be held to it.

    `docs/design-system/provenance.json` names the exact commit and version of
    the vendored sheet, and moving it is one file edit — which is precisely how
    four comments in the two pages came to describe a sheet that was no longer
    there. The pin went v2.4.0 -> v2.7.0 in one merge; `index.html` went on
    saying "everything generic now lives in sc.css v2.4.0" and both pages went
    on crediting "sc.css v2.4.0's own 44px block" for rules the loaded sheet
    still does not carry.

    This is the fourth number in this repo's prose to rot and the second to do
    it while a mechanism existed that could have caught it. The rule is the
    cheap one: any version a page NAMES must be the version it LOADS.

    The forward-looking form is refused outright. "Promotion candidate for
    design-system v2.5.x" is a citation that cannot be checked when written and
    is silently wrong the moment v2.5.0 ships without the rule — which is what
    happened. A promotion candidate is a claim about the pinned sheet, so it
    says so and names no release.
    """

    PAGES = ("docs/index.html", "docs/how.html")

    @staticmethod
    def pinned():
        return json.loads(Path("docs/design-system/provenance.json").read_text())

    def test_the_snapshot_names_a_version_and_the_sheet_it_ships_is_that_one(self):
        """The anchor everything below reads. A provenance file whose version
        does not match the CSS beside it would make every check here agree with
        a number that is not on the page."""
        pin = self.pinned()
        self.assertRegex(pin["version"], r"^\d+\.\d+\.\d+$")
        css = Path("docs/design-system/sc.css").read_text(errors="replace")
        self.assertIn(f"v{pin['version']}", css[:4000],
                      "the vendored sc.css does not announce the version "
                      "provenance.json claims for it")

    def test_no_page_cites_a_design_system_version_it_does_not_load(self):
        want = "v" + self.pinned()["version"]
        pat = re.compile(r"(?:sc\.css|design[- ]system)\s+(v\d+\.\d+(?:\.\d+|\.x)?)")
        found = []
        for name in self.PAGES:
            text = Path(name).read_text()
            for m in pat.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                found.append((name, line, m.group(1),
                              text[max(0, m.start() - 60):m.end() + 20].replace("\n", " ")))
        self.assertTrue(found, "no page cites a version at all — this check has "
                               "lost its subject and would pass over anything")
        wrong = [f for f in found if f[2] != want]
        self.assertEqual([], wrong,
                         f"the pages load {want}; these name something else:\n"
                         + "\n".join(f"  {n}:{ln} says {v} — …{ctx}…" for n, ln, v, ctx in wrong))

    def test_no_page_names_a_release_it_is_waiting_for(self):
        """A version that has not shipped cannot be checked against anything,
        and reads as decided once it does. The pages had two of these, both
        saying v2.5.x, both written under a v2.4.0 pin, and both still saying
        it at v2.7.0."""
        bad = []
        for name in self.PAGES:
            text = Path(name).read_text()
            for m in re.finditer(r"[Pp]romotion candidates?[^*]{0,120}", text):
                if re.search(r"v\d+\.\d+", m.group(0)):
                    line = text.count("\n", 0, m.start()) + 1
                    bad.append(f"  {name}:{line} — {m.group(0)[:100].strip()}")
        self.assertEqual([], bad,
                         "a promotion candidate is a claim about the pinned sheet, "
                         "not a booking against a future release:\n" + "\n".join(bad))

    def test_the_bridges_the_pages_keep_are_ones_the_pinned_sheet_does_not_carry(self):
        """The substantive half, and the one a version number was standing in
        for. A bridge whose selector upstream now covers is dead CSS the page
        is still maintaining; the comment says "delete when the pinned sheet
        carries it", so that condition is executed rather than remembered.

        Both sides are parsed with brace matching, not line grepping: the
        sheet's coarse blocks are three of about a thousand rules, and a
        regex range that stops at the first `}` reports selectors from four
        blocks further down as covered.
        """
        def coarse_selectors(css):
            out = set()
            for m in re.finditer(r"@media\s*\(([a-z-]*pointer:\s*coarse)\)\s*\{", css):
                depth, end = 0, None
                for j in range(m.end() - 1, len(css)):
                    if css[j] == "{":
                        depth += 1
                    elif css[j] == "}":
                        depth -= 1
                        if depth == 0:
                            end = j
                            break
                if end is None:
                    continue
                for rule in re.finditer(r"([^{}]+)\{[^{}]*\}", css[m.end():end]):
                    for sel in rule.group(1).split(","):
                        out.add(sel.strip())
            return out

        upstream = coarse_selectors(Path("docs/design-system/sc.css").read_text(errors="replace"))
        self.assertTrue(upstream, "no coarse-pointer rule found in the pinned sheet — "
                                  "the parser has lost its subject")
        dead = []
        for name in self.PAGES:
            for sel in coarse_selectors(Path(name).read_text()):
                # a page bridge is dead only if the SAME selector is upstream;
                # ::before halos and :not() narrowings are this page's own shape
                base = sel.split("::")[0].strip()
                if base and base in upstream:
                    dead.append(f"  {name} bridges {sel!r}, which the pinned sheet already carries")
        self.assertEqual([], dead,
                         "these bridges are dead CSS under the current pin and their "
                         "own comments say to delete them:\n" + "\n".join(dead))


class TestWhatTheReadmeSaysAboutRanking(unittest.TestCase):
    """The prose about how the front page is ordered, held to the code.

    An earlier draft of README said a cheap model "wins any cheapest-first
    ranking". Nothing on the front page is ranked by price, so the sentence
    named a mechanism that does not exist — and a wrong mechanism argues for
    the wrong fix. These pin the three facts the corrected paragraph rests on,
    because a paragraph about ordering is exactly the kind of prose that rots
    when the ordering changes.
    """

    @staticmethod
    def car(vin, pct, model, local=True, shopping=False):
        return {"vin": vin, "pick_pct": pct, "pick_stand": "under",
                "model_label": model, "local": local, "shopping": shopping}

    def test_a_cohort_never_crosses_a_model(self):
        """score_picks takes ONE model's listings and one label, so a $27,000
        EV9 can never be measured against a $65,000 i7. The claim that the
        margins are within-model is a fact about the signature."""
        import inspect
        sig = list(inspect.signature(T.score_picks).parameters)
        self.assertEqual(sig, ["listings", "model_label"])
        pool = [{"price": 40000 + 500 * i, "miles": 20000, "year": "2024",
                 "trim": "eDrive40", "accidents": 0, "usage": "Personal Use"}
                for i in range(12)]
        scored = T.score_picks(pool, "BMW i5")
        self.assertTrue(scored)
        self.assertEqual({p["model_label"] for p in scored}, {"BMW i5"},
                         "every score carries the one label it was given")

    def test_the_drivable_list_reserves_seats_and_the_shipped_list_does_not(self):
        """The asymmetry README names. `reserve_shopping` holds the first N
        drivable seats for the models being shopped; the worth-the-ship list
        is ranked by margin alone, so a cheap model can take all of it."""
        scored = ([self.car(f"F{i}", 0.40 - i / 100, f"Cheap {i}", local=False)
                   for i in range(6)]
                  + [self.car(f"S{i}", 0.05 - i / 1000, f"Shopped {i}",
                              local=False, shopping=True) for i in range(3)]
                  + [self.car(f"L{i}", 0.40 - i / 100, f"Cheap {i}") for i in range(6)]
                  + [self.car(f"P{i}", 0.05 - i / 1000, f"Shopped {i}",
                              shopping=True) for i in range(3)])
        local, far = T.split_picks(scored, 4, per_model=2, reserve=2)
        self.assertEqual(sum(1 for p in local if p["shopping"]), 2,
                         "two drivable seats are held for the shopped models")
        self.assertEqual(sum(1 for p in far if p["shopping"]), 0,
                         "and the shipped list holds none — the margins alone "
                         "decide it, which is what README says")

    def test_a_bigger_margin_on_a_cheaper_car_outranks_a_smaller_one(self):
        """The defect itself, stated as a test rather than as an adjective: a
        margin is a ratio and a ratio does not know what a car costs. If a
        later change makes price class part of the order, this fails and the
        README paragraph beside it has to be rewritten — which is the point."""
        cheap = self.car("A", 0.35, "Kia EV9", local=False)
        dear = self.car("B", 0.04, "BMW i7", local=False)
        _, far = T.split_picks([dear, cheap], 2, per_model=2, reserve=2)
        self.assertEqual([p["vin"] for p in far], ["A", "B"])

    def test_the_readme_paragraph_names_the_rule_the_config_carries(self):
        readme = " ".join(Path("README.md").read_text().split())
        reserve = T.PICKS.get("reserve_shopping")
        per_model = T.PICKS.get("per_model")
        self.assertTrue(reserve and per_model, "the config carries both knobs")
        self.assertIn("`picks.per_model` caps each model at two", readme)
        self.assertEqual(per_model, 2, "…and two is what it is set to")
        self.assertIn("holds the first two drivable seats", readme)
        self.assertEqual(reserve, 2, "…and two is what it is set to")


class TestTheOfflineRebuildSurvivesTheConfigItDescribes(unittest.TestCase):
    """`tools/rebuild_outputs.py` is what a human runs after editing
    targets.json, and it is the only place a config change is described
    before the next fetch. Its summary used to end with

        print("bmw models in site:", list(site["brands"]["bmw"]["models"]...))

    which is a KeyError the moment BMW is not on the watchlist. The files are
    written BEFORE that line, so standing BMW down produced a correct
    REPORT.md, a correct docs/data.json, a traceback and exit 1 — a rebuild
    that succeeded and reported failure. Driven here as a subprocess against a
    real copy of the tree, because reading the source proves nothing about
    what the interpreter does with it.
    """

    def _run(self, edit):
        """A copy of the WORKING TREE, not of HEAD.

        The first version of this archived HEAD, which means it tested the
        last commit rather than the change under test — it reproduced the bug
        beautifully and would have gone on passing after the fix, one commit
        behind forever. That is this project's "a test that cannot fail" shape
        wearing a subprocess.
        """
        import subprocess, shutil, tempfile, os, json as _json
        root = Path(__file__).parent.parent
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "repo"
            shutil.copytree(root, dst, ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "node_modules", "*.pyc"))
            cfg = _json.loads((dst / "targets.json").read_text())
            edit(cfg)
            (dst / "targets.json").write_text(_json.dumps(cfg, indent=1))
            env = {**os.environ, "AUTODEV_API_KEY": "offline"}
            r = subprocess.run([sys.executable, "tools/rebuild_outputs.py"],
                               cwd=dst, env=env, capture_output=True, text=True)
            return r, (dst / "REPORT.md").read_text(), (dst / "docs" / "data.json").read_text()

    def test_a_rebuild_with_the_shipped_config_succeeds_and_says_what_is_empty(self):
        r, report, sheet = self._run(lambda cfg: None)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        self.assertIn("no listings yet (first fetch)", r.stdout)
        self.assertIn("call plan:", r.stdout)
        self.assertTrue(report.startswith("# "))
        self.assertIn('"brands"', sheet)

    def test_standing_the_only_hard_coded_brand_down_is_not_a_failure(self):
        def drop_bmw(cfg):
            cfg["watchlist"]["bmw"]["active"] = False
            # buyer.shopping names BMW targets that no longer exist; the tool
            # must survive that too, since it is what a real edit looks like
            cfg["buyer"]["shopping"] = []
        r, report, sheet = self._run(drop_bmw)
        self.assertEqual(r.returncode, 0,
                         "the rebuild wrote both files and then died on a "
                         "hard-coded brand key:\n" + r.stderr[-800:])
        self.assertNotIn("Traceback", r.stderr)
        self.assertNotIn("bmw", json.loads(sheet)["brands"])
        self.assertTrue(report.startswith("# "))


class TestNamingNothingMeansNothingIsBeingShopped(unittest.TestCase):
    """An empty `buyer.shopping` used to mean every model was being shopped.

    `shopping = any(...) or not SHOPPING` was a fair default for a
    seven-model watchlist: with nobody named, give the reader full sections
    rather than an empty report. On thirty-six models it is a false claim
    about a person. Reproduced by emptying the list and rebuilding: every
    model was headed "## Shopping: Lucid Air" about a car nobody named, the
    report ran to 1,373 lines with only six models carrying cars — roughly
    eight thousand once they all do — and the page's meta row read "36
    shopping · 0 comparison" for a reader shopping nothing.

    A buyer who has not chosen wants the market on one line per model, which
    is what the compact section already is. 1,373 lines became 85.
    """

    def _build(self, shopping):
        was = (list(T.SHOPPING), {tid: t["shopping"] for tid, t in T.TARGETS.items()})
        T.SHOPPING[:] = list(shopping)
        for tid, t in T.TARGETS.items():
            t["shopping"] = tid in T.SHOPPING
        try:
            rows = T.load_history()
            days = sorted({r["snapshot_date"] for r in rows})
            latest = [r for r in rows if r["snapshot_date"] == days[-1]]
            return T.build_outputs(latest, rows, T.build_history(rows))
        finally:
            T.SHOPPING[:] = was[0]
            for tid, t in T.TARGETS.items():
                t["shopping"] = was[1][tid]

    def test_nobody_named_means_nobody_shopped(self):
        report, site, _ = self._build([])
        flagged = [f"{bk}/{mk}" for bk, b in site["brands"].items()
                   for mk, m in b["models"].items() if m["shopping"]]
        self.assertEqual(flagged, [], "no model is being shopped")
        self.assertNotIn("## Shopping:", report,
                         "and none of them is headed as though it were")
        self.assertEqual(site["buyer"]["shopping"], [])

    def test_and_the_report_becomes_the_market_rather_than_a_car(self):
        """The size is the point, not a detail: the compact section is what a
        buyer who has not chosen actually wants, and the full sections are
        what made the file unreadable."""
        short, _, _ = self._build([])
        long, _, _ = self._build(["bmw-i5-edrive40", "bmw-i7-edrive50"])
        self.assertLess(len(short.splitlines()), len(long.splitlines()) // 2,
                        "an unshopped report is a fraction of a shopped one")
        self.assertIn("## Comparison", short)

    def test_the_shipped_config_still_leads_with_the_cars_it_names(self):
        """The control. Without it the assertions above pass on a build that
        flags nothing as shopped under ANY config, which is the same defect
        pointing the other way."""
        report, site, _ = self._build(["bmw-i5-edrive40", "bmw-i7-edrive50"])
        flagged = sorted(f"{bk}/{mk}" for bk, b in site["brands"].items()
                         for mk, m in b["models"].items() if m["shopping"])
        self.assertEqual(flagged, ["bmw/i5", "bmw/i7"])
        self.assertIn("## Shopping: BMW i5", report)
        self.assertNotIn("## Shopping: Lucid Air", report)


class TestAnEvScreenerChecksThatACarIsAnEv(unittest.TestCase):
    """The watchlist is thirty-six battery EVs and nothing checked for one.

    The listings API has no fuel parameter — the query is make plus model — so
    every row on a nameplate shared with a combustion car rests on one string
    being exact. Porsche is the Taycan rather than the better-selling Macan
    Electric for exactly that reason, and Dodge, MINI, Ford, Genesis and Acura
    each ride on one string being right. A string wrong the OTHER way, one
    that matches too much, puts petrol cars in an EV screener, priced and
    ranked beside the rest, with nothing anywhere to say so.

    The record carried the answer all along and threw it away: the sample
    record's `vehicle.fuel` reads "Electric", with `vehicle.type` and
    `vehicle.engine` agreeing.
    """

    def setUp(self):
        self.dropped = Counter()
        self.t = T.TARGETS["bmw-i5-m60"]     # the sample record is an M60

    def rec(self, **over):
        r = copy.deepcopy({k: v for k, v in FIXTURES["clean"].items()
                           if not k.startswith("_")})
        for k, v in over.items():
            if v is None:
                r["vehicle"].pop(k, None)
            else:
                r["vehicle"][k] = v
        return r

    def test_a_petrol_car_on_a_shared_nameplate_is_refused(self):
        for fuel in ("Gasoline", "Gas", "Diesel", "Flex Fuel"):
            d = Counter()
            self.assertIsNone(T.normalize(self.rec(fuel=fuel, type=fuel, engine="3.0L I6"), self.t, d),
                              f"{fuel!r} is not a battery EV")
            self.assertEqual(d["not a battery EV"], 1, fuel)

    def test_a_plug_in_hybrid_is_refused_though_it_says_electric(self):
        """"Plug-in Hybrid Electric" contains the word. Hybrid is checked
        first, because a PHEV is not what any of these targets watches."""
        d = Counter()
        self.assertIsNone(T.normalize(self.rec(fuel="Plug-in Hybrid Electric"), self.t, d))
        self.assertEqual(d["not a battery EV"], 1)

    def test_the_electric_car_the_sample_record_is_still_gets_through(self):
        """The control. Without it a guard that refused everything would pass
        every assertion above.

        And it is only a control if the fixture SAYS electric. It did not: the
        `clean` record carried 9 of the 21 vehicle fields the API returns, with
        no fuel among them, so this passed on a record that says nothing rather
        than on one that says Electric — a control that could not tell the two
        apart. The fixture mirrors the real record field for field now, and
        test_the_clean_fixture_is_shaped_like_a_real_record keeps it that way.
        """
        self.assertIs(T.is_battery_electric(self.rec()), True,
                      "the fixture has to SAY electric for this to control "
                      "anything")
        d = Counter()
        self.assertIsNotNone(T.normalize(self.rec(), self.t, d),
                             "the shipped sample record is an electric M60")
        self.assertEqual(d["not a battery EV"], 0)

    def test_the_clean_fixture_is_shaped_like_a_real_record(self):
        """A fixture missing a field the API always sends is a fixture
        describing a response the API does not produce, and every test built
        on it is asking a question the real feed never asks. `clean` had
        drifted to 9 of 21 vehicle fields; this is what stops it drifting
        again, and it names data/sample_record.json — a genuine API response
        the run itself wrote — as the standard."""
        real = json.loads(Path("data/sample_record.json").read_text())
        missing = sorted(set(real["vehicle"]) - set(FIXTURES["clean"]["vehicle"]))
        self.assertEqual(missing, [],
                         "the clean fixture is missing vehicle fields the API "
                         f"really returns: {missing}")

    def test_a_feed_that_says_nothing_is_not_evidence_of_petrol(self):
        """None is not False. Refusing on an absent field would empty a whole
        target the day a feed stopped populating it — a silent, total outage
        dressed as a quiet market."""
        self.assertIsNone(T.is_battery_electric(self.rec(fuel=None, type=None, engine=None)))
        d = Counter()
        self.assertIsNotNone(T.normalize(self.rec(fuel=None, type=None, engine=None), self.t, d))
        self.assertEqual(d["not a battery EV"], 0)

    def test_it_reads_whichever_of_the_three_fields_the_feed_filled(self):
        self.assertIs(T.is_battery_electric(self.rec(fuel=None, type="Electric", engine=None)), True)
        self.assertIs(T.is_battery_electric(self.rec(fuel=None, type=None, engine="Electric")), True)
        self.assertIs(T.is_battery_electric(self.rec(fuel="Gasoline", type="Electric")), False,
                      "the first field the feed filled is the one that answers")


class TestTheTwoFactsThatNarrowAThirtySixModelMarket(unittest.TestCase):
    """`seats` and `drivetrain` — kept, and honest about not being read yet.

    "Three rows" and "all-wheel drive" are how a person goes from every EV on
    sale to the six worth looking at, and neither was recoverable from
    anything the record kept: seats appears nowhere else, and drivetrain only
    inside the trim string, only for the brands whose trim encodes it — an i5
    eDrive40 against an xDrive40, but a Model Y Long Range against a Model Y
    Long Range AWD, and nothing at all on most of the rest.

    They are recorded before they are read on purpose. The filters they are
    for are worth building once the record shows the feed FILLS them, and this
    repo has one sample listing to judge that from, which is not evidence. The
    run log counts the coverage so one real night decides it.
    """

    def setUp(self):
        self.dropped = Counter()
        self.t = T.TARGETS["bmw-i5-m60"]

    def rec(self, **over):
        r = copy.deepcopy({k: v for k, v in FIXTURES["clean"].items()
                           if not k.startswith("_")})
        for k, v in over.items():
            if v is None:
                r["vehicle"].pop(k, None)
            else:
                r["vehicle"][k] = v
        return r

    def test_both_are_kept_from_the_record_the_api_really_returns(self):
        n = T.normalize(self.rec(), self.t, self.dropped)
        self.assertEqual(n["seats"], 5)
        self.assertEqual(n["drivetrain"], "AWD")

    def test_the_drivetrain_column_holds_a_vocabulary_not_a_dealer_string(self):
        """Folded to three words, because that is the question a buyer asks
        and because 4WD and AWD are the same answer to it on a car with no
        transfer case. An unrecognised string is dropped rather than passed
        through, or the column is whatever a dealer typed."""
        for raw, want in (("AWD", "AWD"), ("4WD", "AWD"), ("4x4", "AWD"),
                          ("All Wheel Drive", "AWD"), ("all-wheel drive", "AWD"),
                          ("RWD", "RWD"), ("Rear Wheel Drive", "RWD"),
                          ("FWD", "FWD"), ("Front-Wheel Drive", "FWD"),
                          ("Direct Drive", ""), ("", ""), ("wat", "")):
            self.assertEqual(T.drivetrain_of(self.rec(drivetrain=raw)), want, raw)

    def test_a_feed_that_says_nothing_leaves_them_blank_not_wrong(self):
        n = T.normalize(self.rec(seats=None, drivetrain=None), self.t, self.dropped)
        self.assertEqual(n["seats"], "")
        self.assertEqual(n["drivetrain"], "")

    def test_the_columns_are_in_the_file_and_old_rows_read_blank(self):
        """load_history() normalises every row to FIELDS, so a column added
        today is "" on every row written before it rather than a KeyError in
        whatever reads it next."""
        self.assertIn("seats", T.FIELDS)
        self.assertIn("drivetrain", T.FIELDS)
        rows = T.load_history()
        self.assertTrue(rows)
        self.assertTrue(all("seats" in r and "drivetrain" in r for r in rows))
        self.assertEqual({r["drivetrain"] for r in rows}, {""},
                         "every committed row predates the column")

    def test_the_run_says_how_often_the_feed_filled_them(self):
        """The coverage line is the whole justification for keeping a field
        nothing reads. Without it the columns are a guess that never resolves.
        """
        rows = [{"seats": 5, "drivetrain": "AWD"}, {"seats": "", "drivetrain": "RWD"},
                {"seats": 7, "drivetrain": ""}, {"seats": "", "drivetrain": ""}]
        self.assertEqual(T.field_coverage(rows, ("seats", "drivetrain")),
                         {"seats": 2, "drivetrain": 2})
        self.assertEqual(T.field_coverage([], ("seats",)), {"seats": 0},
                         "and it does not divide by an empty night")


class TestAQueryThatRanAndFoundNothingSaysSo(unittest.TestCase):
    """"Not fetched yet" is a claim about the QUERY, not about the cars.

    A query that runs and comes back empty writes no row, so `as_of` stays
    None — and every surface said "not fetched yet · first run <ten days from
    now>", which is the opposite of what happened, for ever, because
    next_due() always rolls forward. Thirty of the thirty-six models have
    never run and their model strings are unverified guesses ("Countryman
    Electric,Countryman SE,Countryman SE ALL4", "3,Polestar 3", "VF 8,VF8"):
    a string naming something the API does not know bills a call every
    cadence and is indistinguishable from a target that has not come round.

    The record already knew. fetch_log_row() writes {raw: 0, exhausted: true,
    failed: false} for exactly this, and nothing read it. `last_asked` is that
    day, carried BESIDE as_of and not instead of it — as_of means "the day
    this model's cars were last seen" and is_new_on(), the cut detector and
    the page's data-through line all depend on that.
    """

    def entry(self, **over):
        e = {"label": "Tesla Model Y", "listings": [], "as_of": None,
             "last_asked": None, "next_due": "2026-09-11", "cadence": 10}
        e.update(over)
        return e

    def test_a_model_no_query_has_reached_still_says_not_fetched_yet(self):
        line = T.compact_line(self.entry(), "Tesla Model Y")
        self.assertIn("not fetched yet", line)
        self.assertIn("first run 2026-09-11", line)

    def test_a_query_that_ran_and_found_nothing_says_that_instead(self):
        line = T.compact_line(self.entry(last_asked="2026-09-01"), "Tesla Model Y")
        self.assertNotIn("not fetched yet", line,
                         "the query ran; saying it has not is false")
        self.assertIn("nothing found", line)
        self.assertIn("asked 2026-09-01", line,
                      "…and when, or the reader cannot tell a string broken "
                      "today from one broken a month ago")

    def test_a_model_with_cars_is_untouched_by_either(self):
        """The control: last_asked must not leak into the ordinary line."""
        e = self.entry(as_of="2026-09-05", last_asked="2026-09-05",
                       listings=[{"price": 40000, "local": True, "ship": 0,
                                  "state": "IL", "city": "Chicago"}])
        line = T.compact_line(e, "Tesla Model Y")
        self.assertIn("1 cars", line)
        self.assertNotIn("nothing found", line)
        self.assertNotIn("asked", line)

    def test_the_sheet_carries_the_day_the_targets_were_asked(self):
        rows = T.load_history()
        days = sorted({r["snapshot_date"] for r in rows})
        latest = [r for r in rows if r["snapshot_date"] == days[-1]]
        _, site, _ = T.build_outputs(latest, rows, T.build_history(rows))
        entries = [m for b in site["brands"].values() for m in b["models"].values()]
        self.assertTrue(all("last_asked" in m for m in entries),
                        "every model block carries it, or the page's own "
                        "predicate reads undefined")
        asked = [m for m in entries if m["last_asked"]]
        self.assertTrue(asked, "the committed fetch log names targets that ran")
        for m in asked:
            self.assertGreaterEqual(m["last_asked"], "2026-01-01")

    def test_a_target_that_billed_a_call_for_nothing_is_reported(self):
        """`silent_targets` cannot catch it — a call WAS billed, so the target
        is not silent. It is the count that tells the owner a model string is
        wrong, and it had no name."""
        was = (dict(T.SPENT), dict(T.RAW_N))
        T.SPENT.clear(); T.RAW_N.clear()
        try:
            due = [t for t in T.TARGETS.values() if T.due_on(t, T.TODAY_ORD)]
            self.assertTrue(due)
            broken, working = due[0], due[1]
            T.SPENT[broken["id"]] = 1                       # billed
            T.SPENT[working["id"]] = 2
            T.RAW_N[(working["id"], "National")] = 20       # …and got rows
            row = T.spend_report(T.planned_calls()[0])
            self.assertIn(broken["id"], row["empty_targets"])
            self.assertNotIn(working["id"], row["empty_targets"])
            self.assertNotIn(broken["id"], row["silent_targets"],
                             "it billed a call, so it is not silent — which is "
                             "exactly why the existing field cannot catch it")
        finally:
            T.SPENT.clear(); T.SPENT.update(was[0])
            T.RAW_N.clear(); T.RAW_N.update(was[1])


class TestEveryTargetInTheRecordIsAccountedFor(unittest.TestCase):
    """A target id in the CSV that no current target claims is either a
    rename nobody carried over, or a deliberate orphan. It must be one of
    them on purpose, and this is what says which.

    The bug it exists for: when the Lucid Air's two trim targets merged into
    one trimless `lucid-air` and `chevrolet-equinox-ev-rs` became
    `chevrolet-equinox-ev`, `legacy_ids` was not touched. Both models then
    read "not fetched yet" on every surface while the committed CSV held 257
    Lucid rows over 4 fetch days and 63 Equinox rows over 2 — indistinguishable
    from the twenty-eight brands that genuinely have never run. 463 green tests
    said nothing, because nothing was checking.

    The two are NOT the same case, and the difference is measured rather than
    argued. Chevrolet is mapped: 0 of its 31 live rows are pre-2024, and its
    old window (cheapest-20 of the model, then filtered to RS) was a SUBSET of
    the new target's, so a cut-off reconstructed from its kept rows sits BELOW
    the truth — conservative, and the opposite of the defect that made
    delisted() manufacture departures. Lucid is not: 53 of its 68 live rows
    are model year 2022 or 2023, which a 2024+ watchlist can never return
    again, so remapping would publish 53 cars as current inventory that no
    query on this sheet could produce. And merging two trim-sliced cheapest-20
    windows gives max(window A, window B), which is WIDER than either — the
    manufacturing case exactly.
    """

    def test_every_target_in_the_record_is_accounted_for(self):
        import csv as _csv
        cfg = json.loads(Path("targets.json").read_text())
        legacy = cfg.get("legacy_ids", {})
        inactive = set()
        for bk, b in cfg["watchlist"].items():
            for mk, m in b["models"].items():
                dead = (not b.get("active", True)) or (not m.get("active", True))
                trims = m.get("trims") or {None: {}}
                for tk, tr in trims.items():
                    tid = f"{bk}-{mk}" + (f"-{tk}" if tk else "")
                    if dead or not tr.get("active", True):
                        inactive.add(tid)
        with open("data/snapshots.csv", newline="", encoding="utf-8-sig") as f:
            seen = {r["target"] for r in _csv.DictReader(f)}
        stray = sorted(t for t in seen
                       if T.LEGACY_IDS.get(t, t) not in T.TARGETS
                       and t not in inactive and t not in legacy)
        self.assertEqual(stray, [], "\n".join([
            "target ids in data/snapshots.csv that no current target claims,",
            "that belong to no model marked inactive, and that legacy_ids does",
            "not mention. Either map them, or list them in legacy_ids with a",
            "null value to say the orphaning is deliberate:", *stray]))

    def test_the_chevrolet_rename_carries_its_history_over(self):
        rows = T.load_history()
        self.assertTrue([r for r in rows if r["target"] == "chevrolet-equinox-ev"],
                        "the RS rows are the Equinox EV's history")
        self.assertFalse([r for r in rows if r["target"] == "chevrolet-equinox-ev-rs"],
                         "…under the new id, not the old one")

    def test_the_lucid_rows_are_deliberately_left_where_they_are(self):
        """Pinned here rather than in a commit message, which is where this
        decision lived and where nothing could check it."""
        self.assertIn("lucid-air-touring", T.LEGACY_IDS)
        self.assertIsNone(T.LEGACY_IDS["lucid-air-touring"])
        rows = T.load_history()
        kept = [r for r in rows if r["target"] == "lucid-air"]
        self.assertFalse(kept, "the old rows must not surface under the new id")
        orphan = [r for r in rows if r["target"].startswith("lucid-air-")]
        self.assertTrue(orphan, "…and they must still be in the file, untouched")
        # the measured reason, so a later session cannot 'fix' this by mapping
        last = max(r["snapshot_date"] for r in orphan)
        live = [r for r in orphan if r["snapshot_date"] == last]
        pre = [r for r in live if r["year"] and int(r["year"]) < 2024]
        self.assertGreater(len(pre) / len(live), 0.5,
                           "most of these cars are outside the 2024+ rule the "
                           "whole watchlist is built on, which is why mapping "
                           "them would publish inventory no query can return")


class TestDailySeries(unittest.TestCase):
    """A day row holds what the record knew on that day.

    Trims of one model run on their own cadences — the i5's eDrive40 daily, its
    xDrive40 and M60 every second day — and counting only the rows FETCHED on a
    day made the model's own series halve on every off day: 127, 119, 71, 130,
    73, 140, 79, 136, 80, 137, with the median swinging $5,371 every other day
    while the listings table beside it showed 137 cars throughout. The chart
    draws that series under the words "among the cars in view".
    """

    @staticmethod
    def row(tid, vin, day, price):
        r = {k: "" for k in T.FIELDS}
        r.update({"target": tid, "vin": vin, "snapshot_date": day, "price": price,
                  "year": "2024", "miles": 20000, "state": "IL", "city": "Chicago"})
        return r

    def test_a_slow_trim_is_carried_to_its_own_next_fetch(self):
        fast, slow = "bmw-i5-edrive40", "bmw-i5-xdrive40"
        d1, d2, d3 = "2026-08-01", "2026-08-02", "2026-08-03"
        rows = [self.row(fast, "F1", d, 40000) for d in (d1, d2, d3)]
        rows += [self.row(slow, "S1", d, 60000) for d in (d1, d3)]   # every other day
        by_day = {x["date"]: x for x in T.daily_stats(rows)}
        self.assertEqual([by_day[d]["n"] for d in (d1, d2, d3)], [2, 2, 2],
                         "the slow trim's car did not leave the market on the "
                         "day its trim was not fetched")
        self.assertEqual(by_day[d2]["median_price"], 50000,
                         "and the median must not halve to the fast trim's own")

    def test_a_car_that_really_left_is_not_carried_past_its_trims_next_fetch(self):
        """Carrying forward must stop at the next fetch of that trim, or a
        departure would be invisible for as long as its cadence."""
        fast, slow = "bmw-i5-edrive40", "bmw-i5-xdrive40"
        d1, d2, d3 = "2026-08-01", "2026-08-02", "2026-08-03"
        # a daily trim, so d2 is a snapshot day at all
        rows = [self.row(fast, "F1", d, 30000) for d in (d1, d2, d3)]
        rows += [self.row(slow, "KEEP", d, 40000) for d in (d1, d3)]
        rows += [self.row(slow, "GONE", d1, 60000)]        # absent at the d3 fetch
        by_day = {x["date"]: x for x in T.daily_stats(rows)}
        self.assertEqual(by_day[d1]["n"], 3)
        self.assertEqual(by_day[d2]["n"], 3, "d2 still reads the slow trim's d1 fetch")
        self.assertEqual(by_day[d3]["n"], 2, "the d3 fetch is what says it went")

    def test_a_target_contributes_nothing_before_its_first_fetch(self):
        """Each trim's own series is reported over the MODEL's day list, so
        that the trim rows decompose the model row. A trim that had not been
        fetched yet on an early day holds nothing there, and nothing is not a
        market of zero cars — the day is absent, not zeroed, or the trim
        comparison would draw a line down to the axis and back."""
        fast, slow = "bmw-i5-edrive40", "bmw-i5-m60"
        d1, d2 = "2026-08-01", "2026-08-02"
        rows = [self.row(fast, "F1", d, 40000) for d in (d1, d2)]
        rows += [self.row(slow, "S1", d2, 60000)]
        model_days = [d1, d2]
        by_day = {x["date"]: x for x in T.daily_stats(rows, model_days)}
        self.assertEqual(by_day[d1]["n"], 1,
                         "a trim with no fetch yet is not a car on the market")
        self.assertEqual(by_day[d2]["n"], 2)
        # …and the slow trim's OWN series simply has no row for that first day
        slow_only = T.daily_stats([r for r in rows if r["target"] == slow], model_days)
        self.assertEqual([x["date"] for x in slow_only], [d2])


class TestNewToday(unittest.TestCase):
    """"New" means first seen on the snapshot the sentence is about.

    days_tracked is the length of a car's price series and a series only grows
    on days its target was fetched, so a car seen once on Monday still reads
    days_tracked == 1 on Thursday. On any day when some OTHER trim of its model
    was due, the whole "New today" block re-announced it — three cars first seen
    on 2026-09-01 were headlined as "first seen this run" on a quiet 09-04 —
    and the report's per-model "N new", the dashboard tile and the `new` chip
    all counted it again with them.

    The day is a parameter, and was the wall clock: see
    TestEveryDatedSentenceNamesItsOwnDay below for what that cost.
    """

    def test_a_car_first_seen_on_that_day_is_new(self):
        self.assertTrue(T.is_new_on({"first_seen": T.TODAY, "days_tracked": 1}, T.TODAY))

    def test_a_car_carried_forward_from_an_earlier_fetch_is_not(self):
        """The exact shape: seen once, days ago, its trim not fetched since."""
        self.assertFalse(T.is_new_on({"first_seen": "2026-01-01", "days_tracked": 1}, T.TODAY),
                         "one sighting is not one DAY when the trim runs on a cadence")

    def test_a_car_seen_every_day_since_is_not_new_either(self):
        self.assertFalse(T.is_new_on({"first_seen": "2026-01-01", "days_tracked": 40}, T.TODAY))

    def test_the_day_is_the_one_the_caller_names_not_the_wall_clock(self):
        """The whole change: the same car, two days, two answers — and the day
        that decides is the one passed in. Anchored on TODAY this was False on
        every build made after the fetch it describes."""
        car = {"first_seen": "2026-09-05", "days_tracked": 1}
        self.assertTrue(T.is_new_on(car, "2026-09-05"))
        self.assertFalse(T.is_new_on(car, "2026-09-06"))
        self.assertNotEqual(T.TODAY, "2026-09-05",
                            "the point of the first assertion is that it does not "
                            "depend on the day this test runs")

    def test_a_day_the_record_does_not_have_makes_nothing_new(self):
        """A model with no rows at all has no day; nothing is new on it."""
        self.assertFalse(T.is_new_on({"first_seen": "2026-09-05", "days_tracked": 1}, None))

    def test_a_row_with_no_first_seen_falls_back(self):
        """An older sheet: better the old test than no answer at all."""
        self.assertTrue(T.is_new_on({"days_tracked": 1}, T.TODAY))
        self.assertFalse(T.is_new_on({"days_tracked": 3}, T.TODAY))

    def test_the_report_does_not_re_announce_an_old_car(self):
        """End to end, through the block that prints the headline."""
        def row(vin, day, price):
            r = {k: "" for k in T.FIELDS}
            r.update({"target": "bmw-i5-edrive40", "vin": vin, "snapshot_date": day,
                      "price": price, "year": "2024", "trim": "eDrive40", "miles": 20000,
                      "state": "IL", "city": "Chicago"})
            return r
        old_day = "2026-01-02"
        rows = [row("O" * 17, old_day, 40000),          # seen once, long ago
                row("N" * 17, T.TODAY, 41000),          # first seen today
                row("K" * 17, old_day, 42000), row("K" * 17, T.TODAY, 42000)]
        today = [r for r in rows if r["snapshot_date"] == T.TODAY]
        report, _, _ = T.build_outputs(today, rows, T.build_history(rows))
        block = report.split("**New today")[1].split("**Spicy picks")[0] if "**New today" in report else ""
        self.assertIn("N" * 17, block, "the car first seen today is the one that is new")
        self.assertNotIn("O" * 17, block,
                         "a car last seen in January is not first seen this run")


class TestOneCarTwoTargets(unittest.TestCase):
    """A car two targets both return has two records and one row.

    The listings table is one row per VIN (the cheapest copy), the record is
    one series per (target, vin), and the row used to carry only the chosen
    copy's series. The nationwide CPO watch and the ordinary eDrive40 target
    match the same certified cars, so the day the watch first returned a car
    the ordinary target had listed for ten days, the row read days_tracked 1,
    first_seen today, no cuts and no delta — and "New today" announced it.
    Four VINs sat under two targets on 2026-09-01 alone.
    """

    def setUp(self):
        self.days = [date.fromordinal(T.TODAY_ORD - 9 + i).isoformat() for i in range(10)]
        self.vin = "WBY" + "1" * 14

        def row(target, day, price):
            r = {k: "" for k in T.FIELDS}
            r.update({"target": target, "vin": self.vin, "snapshot_date": day, "price": price,
                      "year": "2024", "trim": "eDrive40", "miles": 12000, "state": "IL", "city": "Chicago"})
            return r
        self.rows = [row("bmw-i5-edrive40", d, 45998) for d in self.days] + [row("bmw-i5-cpo", self.days[-1], 45000)]
        today = [r for r in self.rows if r["snapshot_date"] == T.TODAY]
        _, site, _ = T.build_outputs(today, self.rows, T.build_history(self.rows))
        self.entry = next(x for x in site["brands"]["bmw"]["models"]["i5"]["listings"] if x["vin"] == self.vin)

    def test_the_history_follows_the_car_not_the_copy_that_won(self):
        e = self.entry
        self.assertEqual(e["price"], 45000, "the cheapest copy is still the row")
        self.assertEqual(e["trim_id"], "bmw-i5-cpo", "…and it keeps that copy's target")
        self.assertEqual(e["first_seen"], self.days[0], "first seen the day the CAR was, not the day this copy was")
        self.assertEqual(e["days_tracked"], 10)
        self.assertEqual(len(e["series"]), 10)
        self.assertEqual(e["series"][-1][1], 45000, "each day at the lowest of its copies")
        self.assertEqual(e["delta"], 45000 - 45998)
        self.assertEqual(e["cuts"], 1)
        self.assertFalse(T.is_new_on(e, T.TODAY),
                         "a car listed for ten days is not new because a second query found it")


class TestCutsThatStuck(unittest.TestCase):
    """cut_share counts any car with a downward step, and a step that bounced
    back counts the same as one that held. The buyer can act on the cars that
    ask less than when first seen, and on how many "cuts" were put back."""

    def _car(self, series):
        prices = [p for _, p in series]
        return {"days_tracked": len(series), "series": series,
                "cuts": sum(1 for a, b in zip(prices, prices[1:]) if b < a),
                "delta": prices[-1] - prices[0]}

    def test_a_cut_that_bounced_back_is_not_a_car_asking_less(self):
        stuck = self._car([["d1", 50000], ["d2", 49000]])                 # down, held
        bounced = self._car([["d1", 50000], ["d2", 49000], ["d3", 50000]])   # down, put back
        flat = self._car([["d1", 50000], ["d2", 50000]])
        st = T.market_stats([stuck, bounced, flat])
        self.assertEqual(st["tracked_2d"], 3)
        self.assertAlmostEqual(st["cut_share"], 2 / 3, places=2, msg="both downward steps still count as cuts")
        self.assertEqual(st["net_down"], 1, "only the car that still asks less")
        self.assertEqual(st["restored"], 1, "the bounced one is named as put back")
        self.assertEqual(st["median_net_drop"], 1000)

    def test_the_line_leads_with_the_count_and_its_denominator(self):
        line = T.market_line({"median_days_listed": None, "tracked_2d": 131, "cut_share": 0.66, "median_cut": 600,
                              "net_down": 69, "restored": 17, "median_net_drop": 900})
        self.assertIn("69 of 131 ask less than when first seen, median $900 less · 17 cut and put back", line)
        self.assertIn("66% of 131 cut while tracked", line, "the share of cars with any downward step still follows")
        self.assertLess(line.index("69 of 131"), line.index("66% of 131 cut"))


class TestSeenLabel(unittest.TestCase):
    """"tracked 21d" read as three weeks; it was twenty-one sightings on a
    target fetched every second day. The count was fixed then and the
    DENOMINATOR was not: it stayed calendar days between the first and last
    sighting, which is the same thing only at a daily cadence.

    Twenty-eight of the thirty-six models run every fifteenth day now. A car
    present at every single fetch of one read "seen 3 of 31 days" beside
    another car's "seen 31 of 31 days", so a buyer reads a perfect record as
    a car that keeps disappearing — a relisted car, a flaky dealer, something
    to ask about. And 3-of-31 against 2-of-31 is a distinction no reader
    makes, so it could not tell perfect attendance from a real gap either.
    """

    def setUp(self):
        self._was = dict(T.FETCH_DAYS)
        T.FETCH_DAYS.clear()

    def tearDown(self):
        T.FETCH_DAYS.clear()
        T.FETCH_DAYS.update(self._was)

    def test_the_denominator_is_the_fetches_not_the_days(self):
        T.FETCH_DAYS["slow"] = [f"2026-08-{d:02d}" for d in (1, 11, 21, 31)]
        every = {"days_tracked": 4, "trim_id": "slow",
                 "series": [[f"2026-08-{d:02d}", 40000] for d in (1, 11, 21, 31)]}
        self.assertEqual(T.seen_label(every), "seen 4 of 4 fetches",
                         "a car there every time the query ran has a perfect "
                         "record, and used to read 'seen 3 of 31 days'")

    def test_and_a_real_gap_is_visible_beside_it(self):
        """The half that makes the one above load-bearing: perfect attendance
        and a missed fetch must not render the same."""
        T.FETCH_DAYS["slow"] = [f"2026-08-{d:02d}" for d in (1, 11, 21, 31)]
        missed = {"days_tracked": 3, "trim_id": "slow",
                  "series": [[f"2026-08-{d:02d}", 40000] for d in (1, 11, 31)]}
        self.assertEqual(T.seen_label(missed), "seen 3 of 4 fetches")

    def test_a_daily_target_reads_exactly_as_it_always_did(self):
        T.FETCH_DAYS["fast"] = [f"2026-08-{d:02d}" for d in range(1, 12)]
        series = [[f"2026-08-{d:02d}", 40000] for d in (1, 3, 5, 7, 9, 11)]
        self.assertEqual(T.seen_label({"days_tracked": 6, "trim_id": "fast",
                                       "series": series}), "seen 6 of 11 fetches")

    def test_only_the_fetches_inside_the_car_s_own_span_are_counted(self):
        """The target has been fetched for months; this car was on the market
        for four of them. Counting every fetch ever would make a car that
        arrived yesterday read "seen 1 of 40 fetches" — the same slander the
        calendar-day denominator committed, one level up."""
        T.FETCH_DAYS["slow"] = ([f"2026-06-{d:02d}" for d in (1, 11, 21)]
                                + [f"2026-08-{d:02d}" for d in (1, 11, 21, 31)]
                                + [f"2026-10-{d:02d}" for d in (1, 11)])
        car = {"days_tracked": 4, "trim_id": "slow",
               "series": [[f"2026-08-{d:02d}", 40000] for d in (1, 11, 21, 31)]}
        self.assertEqual(T.seen_label(car), "seen 4 of 4 fetches",
                         "five fetches lie outside this car's span and belong "
                         "to no claim about it")

    def test_build_outputs_is_what_fills_the_denominator(self):
        """FETCH_DAYS is a global, so a caller that never builds one gets the
        fallback on every row — which is what the record looked like before
        this, and reads as a bare count rather than as a wrong one. Driven
        through the real build rather than filled by hand, because the hand-
        filled tests above cannot tell a populated global from an empty one.
        """
        T.FETCH_DAYS.clear()
        rows = T.load_history()
        days = sorted({r["snapshot_date"] for r in rows})
        latest = [r for r in rows if r["snapshot_date"] == days[-1]]
        report, _, _ = T.build_outputs(latest, rows, T.build_history(rows))
        self.assertTrue(T.FETCH_DAYS, "build_outputs() populates it")
        self.assertIn("bmw-i5-edrive40", T.FETCH_DAYS)
        self.assertRegex(report, r"seen \d+ of \d+ fetches",
                         "and the record it builds says fetches")

    def test_no_fetch_record_says_what_is_known_and_no_more(self):
        """An older sheet, or a caller that never built one. Dividing by the
        wrong denominator is worse than not dividing."""
        self.assertEqual(T.seen_label({"days_tracked": 4, "trim_id": "nope",
                                       "series": [["2026-08-01", 1], ["2026-08-31", 1]]}),
                         "seen 4 times")

    def test_one_sighting_is_once_and_no_series_is_just_the_count(self):
        self.assertEqual(T.seen_label({"days_tracked": 1, "series": [["2026-08-01", 1]]}), "seen once")
        self.assertEqual(T.seen_label({"days_tracked": 3}), "seen 3 times")

    def test_the_report_row_carries_it(self):
        T.FETCH_DAYS["bmw-i5-edrive40"] = [f"2026-07-{d:02d}" for d in range(1, 22)]
        r = {k: "" for k in T.FIELDS}
        r.update({"target": "bmw-i5-edrive40", "vin": "V", "year": "2024", "trim": "eDrive40",
                  "price": 45000, "miles": 20000, "state": "IL", "city": "Chicago", "snapshot_date": T.TODAY})
        series = [[f"2026-07-{d:02d}", 45000] for d in range(1, 22)]     # 21 sightings, 21 fetches
        line = T.fmt_row(r, {"series": series, "days_tracked": 21, "trim_id": "bmw-i5-edrive40",
                             "first_seen": series[0][0]}, T.TODAY)
        self.assertIn("seen 21 of 21 fetches", line)
        self.assertNotIn("tracked", line)

    def test_the_committed_record_names_fetches_and_not_days(self):
        """The rule reaches the file, not only the function: FETCH_DAYS is a
        global that build_outputs() populates, so a caller that never built
        one silently gets the fallback on every row."""
        report = Path("REPORT.md").read_text()
        self.assertNotIn(" days`", report.replace("seen ", "seen "))
        self.assertNotRegex(report, r"seen \d+ of \d+ days")
        self.assertRegex(report, r"seen \d+ of \d+ fetches")


class TestPickUnderRoundsLikeThePage(unittest.TestCase):
    """The page recomputes "$X less" with Math.round, which rounds half up;
    Python's round() rounds half to even, so an exact .5 residual printed
    $1,936 in the report and $1,937 on the page for the same car."""

    def test_an_exact_half_rounds_up(self):
        rows = [listing(price=p, vin=f"V{p}") for p in range(40000, 40006)]   # median 40002.5
        by = {p["vin"]: p for p in T.score_picks(rows, "Six Cars")}
        self.assertEqual(by["V40002"]["pick_under"], 1, "40002.5 - 40002 = .5 rounds up, as Math.round does")
        self.assertEqual(by["V40001"]["pick_under"], 2)
        self.assertEqual(by["V40003"]["pick_under"], 0, "and -.5 rounds to 0, as Math.round(-0.5) does")


class TestUnreadValueIsNotExported(unittest.TestCase):
    """The mileage-adjusted value is the one figure that includes
    buyer.cents_per_mile while the page's landed() is asking plus shipping —
    a copy nothing reads, 19KB of data.json, that would silently disagree with
    every number on screen the day that knob is turned on."""

    def test_listing_daily_and_departure_rows_carry_no_adj(self):
        r = {k: "" for k in T.FIELDS}
        r.update({"target": "bmw-i5-edrive40", "vin": "V", "year": "2024", "trim": "eDrive40",
                  "price": 45000, "miles": 20000, "state": "IL", "city": "Chicago", "snapshot_date": T.TODAY})
        self.assertNotIn("adj", T.listing_entry(r, {"series": []}))
        day = T.daily_stats([r])[0]
        self.assertNotIn("min_adj", day)
        self.assertNotIn("median_adj", day)
        self.assertIn("median_price", day, "the price median stays; only the adjusted copies go")
        gone = dict(r, vin="G", snapshot_date=date.fromordinal(T.TODAY_ORD - 3).isoformat())
        _, site, _ = T.build_outputs([r], [r, gone], T.build_history([r, gone]))
        departed = site["brands"]["bmw"]["models"]["i5"]["gone"]
        self.assertTrue(departed, "the fixture's second car must come out as a departure")
        self.assertTrue(all("adj" not in g for g in departed))


class TestDaysListedAnchor(unittest.TestCase):
    """Days on market is measured from the day the row was OBSERVED.

    It used to be measured from the day the file was built, and since 9f1ff6a
    every dispatch rebuilds — so a rebuild run a week after the fetch aged every
    listing by a week over identical rows. The i5's published median moved 23 ->
    30 and a car's "21d listed" became "28d listed", while `data through`
    correctly stayed put beside them. stale_pct is that same field's percentile
    and the report's ">= 30d on market" tag is its threshold, so both walked
    with it.
    """

    @staticmethod
    def row(day, since):
        r = {k: "" for k in T.FIELDS}
        r.update({"target": "bmw-i5-edrive40", "vin": "V" * 17, "snapshot_date": day,
                  "price": 40000, "year": "2024", "trim": "eDrive40", "miles": 20000,
                  "state": "IL", "city": "Chicago", "listed_since": since})
        return r

    def test_a_rebuild_a_week_later_does_not_age_the_listing(self):
        T.INDEX_DATES.clear()
        r = self.row("2026-08-15", "2026-08-01")
        was = T.TODAY
        try:
            T.TODAY = "2026-08-15"
            same_day = T.days_listed(r)
            T.TODAY = "2026-08-22"          # rebuilt a week later, same row
            later = T.days_listed(r)
        finally:
            T.TODAY = was
        self.assertEqual(same_day, 14)
        self.assertEqual(later, 14, "the row did not sit on the market for another "
                                    "week because we rebuilt the file")

    def test_a_row_with_no_snapshot_day_still_answers(self):
        T.INDEX_DATES.clear()
        self.assertIsNotNone(T.days_listed({"listed_since": "2026-08-01"}))

    def test_an_index_date_still_answers_nothing(self):
        T.INDEX_DATES.clear()
        T.INDEX_DATES.add("2026-08-09")
        try:
            self.assertIsNone(T.days_listed(self.row("2026-08-15", "2026-08-09")))
        finally:
            T.INDEX_DATES.clear()


# Every shape a hand-edited or half-written data/fetch_log.json can take at the
# three levels delisted() and save_fetch_log() index into. Ten of them crashed
# the run inside build_outputs(), which main() reaches only after the whole API
# budget has been spent; one loaded clean and answered wrongly.
MALFORMED_LOGS = [
    ("the day is a list", lambda d, t, g: {d: []}),
    ("…with something in it", lambda d, t, g: {d: ["x"]}),
    ("the day is a string", lambda d, t, g: {d: "s"}),
    ("the day is a number", lambda d, t, g: {d: 3}),
    ("the day is true", lambda d, t, g: {d: True}),
    ("the day is null", lambda d, t, g: {d: None}),
    ("the target is a string", lambda d, t, g: {d: {t: "s"}}),
    ("the target is a list", lambda d, t, g: {d: {t: ["x"]}}),
    ("the target is a number", lambda d, t, g: {d: {t: 3}}),
    ("the source is a string", lambda d, t, g: {d: {t: {"National": "s", "States": "s"}}}),
    ("the source is a number", lambda d, t, g: {d: {t: {"National": 3, "States": 3}}}),
    ("the source is a list", lambda d, t, g: {d: {t: {"National": [], "States": []}}}),
    ("the window is a string", lambda d, t, g: {d: {t: {k: {**g, "window": "sixty"} for k in ("National", "States")}}}),
    ("the window is a list", lambda d, t, g: {d: {t: {k: {**g, "window": [1]} for k in ("National", "States")}}}),
    ("the window is true", lambda d, t, g: {d: {t: {k: {**g, "window": True} for k in ("National", "States")}}}),
    ("the window is not finite", lambda d, t, g: {d: {t: {k: {**g, "window": float("inf")} for k in ("National", "States")}}}),
    ("exhausted is a word", lambda d, t, g: {d: {t: {k: {**g, "exhausted": "yes"} for k in ("National", "States")}}}),
    ("failed is a number", lambda d, t, g: {d: {t: {k: {**g, "failed": 1} for k in ("National", "States")}}}),
    ("the whole file is a list", lambda d, t, g: []),
    ("the whole file is a string", lambda d, t, g: "nope"),
]


class TestANumberThatIsNotOneIsNone(unittest.TestCase):
    """to_int() is the file's universal "give me a number or None", and every
    caller uses it as a function that never raises.

    int(float(...)) raises OverflowError, which was not in the caught list, and
    json.loads accepts `Infinity`, `-Infinity` and any literal above ~1e308 by
    default — so one record whose price, miles, ownerCount, accidentCount or
    baseMsrp came back non-finite took normalize() down INSIDE the fetch loop:
    after the calls made so far were billed and before write_rows(),
    save_fetch_log() or save_spend_history() had run, leaving the day with no
    snapshot row, no spend record and no fetch log for calls that were paid for.
    """

    NOT_NUMBERS = [float("inf"), float("-inf"), float("nan"), 1e400, "1e400",
                   "-1e400", "Infinity", "-Infinity", "NaN", "nan",
                   "$1e400", "1,0e400"]

    def test_a_non_finite_value_is_no_number_at_all(self):
        for v in self.NOT_NUMBERS:
            self.assertIsNone(T.to_int(v), f"to_int({v!r})")
            self.assertIsNone(T.to_float(v), f"to_float({v!r})")

    def test_and_the_numbers_it_always_read_still_read(self):
        for v, want in (("42", 42), (7, 7), ("$60,999", 60999), ("40000.9", 40000),
                        (0, 0), ("0", 0), (-3, -3)):
            self.assertEqual(T.to_int(v), want, f"to_int({v!r})")
        self.assertIsNone(T.to_int(None))
        self.assertIsNone(T.to_int("n/a"))
        self.assertEqual(T.int_or_blank(float("inf")), "")

    def test_a_listing_priced_at_infinity_is_dropped_not_fatal(self):
        """Through the real normalize(), which is where it landed."""
        rec = json.loads(Path("data/sample_record.json").read_text())
        t = T.TARGETS["bmw-i5-m60"]      # the sample record is an M60
        base = T.normalize(json.loads(json.dumps(rec)), t, Counter())
        self.assertTrue(base and base.get("price"), "the sample record is a car")
        for path in (("retailListing", "price"), ("retailListing", "miles"),
                     ("history", "ownerCount"), ("history", "accidentCount"),
                     ("vehicle", "baseMsrp")):
            bad = json.loads(json.dumps(rec))
            node = bad
            for k in path[:-1]:
                node = node.setdefault(k, {})
            node[path[-1]] = 1e400
            try:
                T.normalize(bad, t, Counter())
            except Exception as e:                        # noqa: BLE001 — the point
                self.fail(f"{'.'.join(path)} = 1e400 raised {type(e).__name__}: {e}")
        # …and the coordinates, which took the other road out: to_float handed
        # infinity to haversine(), whose asin() then raised "math domain error"
        # in the same fetch loop. The coordinate is refused now, so the car
        # falls back to its zip — which is what the geocoding rescue is for —
        # and comes out with a real distance instead of a crash.
        import math as _math
        for i in (0, 1):
            bad = json.loads(json.dumps(rec))
            bad["location"] = list(bad["location"])
            bad["location"][i] = 1e400
            got = T.normalize(bad, t, Counter())
            self.assertTrue(got, f"location[{i}] = 1e400 dropped the whole car")
            d = got["distance"]
            self.assertTrue(d in ("", None) or (isinstance(d, (int, float)) and _math.isfinite(d)),
                            f"location[{i}] = 1e400 produced a distance of {d!r}")


class TestTheDaysToSaleClauseNamesItsOwnDenominator(unittest.TestCase):
    """"listings ran at least ~6d (30 gone)" — where 38 cars had gone.

    sale_stats() builds a span only for a departure that also carries a usable
    listing date (missing dates, and dates find_index_dates() withheld, are
    skipped), and returned that count as `n_sold`. market_line() printed it
    under the word this report uses for departures. Three words to its left the
    same sentence already says "(85 of 134 dated)" for exactly this reason.
    """

    @staticmethod
    def gone(n, dated, day="2026-08-20"):
        out = []
        for i in range(n):
            g = {"vin": f"V{i:016d}", "likely": "delisted", "exact": True,
                 "last_seen": "2026-09-05", "last_price": 40000 + i,
                 "series": [], "trim_id": "bmw-i5-m60"}
            if i < dated:
                g["listed_since"] = day
            out.append(g)
        return out

    def test_the_count_is_the_spans_over_the_departures(self):
        st = T.sale_stats(self.gone(38, 30))
        self.assertEqual((st["n_sold"], st["n_departures"]), (30, 38))
        line = T.market_line({**st, "n": 100, "dated": 0})
        self.assertIn("(30 of 38 dated)", line)
        self.assertNotIn("(30 gone)", line,
                         "the median's n is not the number of cars that left")

    def test_a_record_where_every_departure_is_dated_says_so_plainly(self):
        st = T.sale_stats(self.gone(20, 20))
        self.assertIn("(20 of 20 dated)", T.market_line({**st, "n": 100, "dated": 0}))

    def test_a_departure_that_is_not_evidence_is_in_neither(self):
        """The count sits inside the same gate the spans do, so a car that
        merely fell out of a window does not swell the denominator either."""
        rows = self.gone(14, 14) + [{"vin": "W" * 17, "likely": "out of window",
                                     "exact": True, "last_seen": "2026-09-05",
                                     "last_price": 1, "listed_since": "2026-08-01",
                                     "series": [], "trim_id": "bmw-i5-m60"}]
        st = T.sale_stats(rows)
        self.assertEqual((st["n_sold"], st["n_departures"]), (14, 14))


class TestTheWindowAxisIsTheOneTheRunOpened(unittest.TestCase):
    """window_dim() read the sorts a config LISTS, not the ones a run fetches.

    Eleven of the fourteen targets name both price.asc and miles.asc and are
    `light` depth, which opens only the first — the same config-versus-fetch gap
    departures_are_separable() and window_reconstructable() already close by
    asking sorts_pages(). It gave the right answer only because price.asc
    happens to be written first everywhere.
    """

    @staticmethod
    def target(**kw):
        t = {"id": "t", "sorts": ["price.asc", "miles.asc"], "pages": 2,
             "depth": "light"}
        t.update(kw)
        return t

    def test_a_light_target_is_judged_on_the_sort_it_actually_opens(self):
        self.assertEqual(T.window_dim(self.target(sorts=["miles.asc", "price.asc"])),
                         "miles",
                         "light depth opens the first sort only, and that one is "
                         "bounded in miles")
        self.assertEqual(T.window_dim(self.target(sorts=["price.asc", "miles.asc"])),
                         "price")

    def test_a_full_target_is_judged_on_all_of_them(self):
        self.assertEqual(T.window_dim(self.target(depth="full",
                                                  sorts=["miles.asc", "price.asc"])),
                         "price", "a full target really opens both")
        self.assertEqual(T.window_dim(self.target(depth="full", sorts=["miles.asc"])),
                         "miles")

    def test_the_shipped_watchlist_is_unchanged_by_the_fix(self):
        """It was right on every target, and by accident: price.asc is written
        first in each of the eleven. This is the check that says the fix moved
        nothing today."""
        for t in T.TARGETS.values():
            want = "price" if "price.asc" in T.sorts_pages(t)[0] else "miles"
            self.assertEqual(T.window_dim(t), want, t["id"])
        self.assertEqual(T.window_dim(T.TARGETS["bmw-i5-cpo"]), "miles",
                         "the certified watch sorts by mileage and always did")


class TestThePlanCoversAWholeCycle(unittest.TestCase):
    """The budget guard looked fourteen days ahead at a schedule that repeats
    on the LCM of the cadences.

    Today's 1/2/3 have an LCM of 6, so fourteen happens to cover it. Add a
    cadence of 4 and 5 — ordinary values for a documented knob — and the cycle
    is 60 days: the guard would see a strict subset of it, its answer would
    depend on the day it ran, and check.yml's promise that "a config edit that
    would overspend the free API plan fails here, not on the invoice" would be
    false. Worse than the missed overspend is the other direction: main() runs
    the same check, so as the window slid forward it would meet the day CI never
    looked at and the scheduled run would start exiting 1 with "Plan too big",
    days after CI approved the config.
    """

    def test_the_horizon_covers_the_cadence_cycle(self):
        import math as _math
        cycle = 1
        for t in T.TARGETS.values():
            cycle = _math.lcm(cycle, max(1, int(t["cadence"])))
        self.assertGreaterEqual(T.plan_horizon(), cycle,
                                f"the cadences repeat every {cycle} days")

    def test_and_never_less_than_the_fortnight_the_prose_promises(self):
        self.assertGreaterEqual(T.plan_horizon(), 14)

    def test_a_cadence_the_fortnight_would_miss_widens_it(self):
        """No longer hypothetical: the shipped watchlist is the case.

        This used to mutate one target to cadence 5 and check the horizon
        moved to 30, with a docstring saying today's config could not reach
        it. One EV per brand put the reference watches on cadence 10 beside
        the shopped trims' 1/2/3 and the kept models' 4, so the cycle is 60
        days and a flat fortnight would see less than a quarter of it — which
        is exactly the failure this widening exists to prevent, since main()
        runs the same guard and would start refusing a config CI approved,
        weeks later, on the day the window finally met the peak.
        """
        self.assertEqual(T.plan_horizon(), 60,
                         "1/2/3/4/10 is an LCM of 60")
        # …and the fortnight is still the floor when the cycle is short.
        was = {tid: t["cadence"] for tid, t in T.TARGETS.items()}
        try:
            for i, t in enumerate(T.TARGETS.values()):
                t["cadence"] = 1 + (i % 2)
            self.assertEqual(T.plan_horizon(), 14,
                             "1 and 2 repeat every other day; the floor is "
                             "what applies, because one cycle of an all-daily "
                             "watchlist is one day and the printed plan is a "
                             "forecast a human reads")
        finally:
            for tid, c in was.items():
                T.TARGETS[tid]["cadence"] = c
        self.assertEqual(T.plan_horizon(), 60, "and the shipped config is back")

    def test_the_worst_day_is_the_worst_of_that_horizon(self):
        """Not of an arbitrary fortnight: the number main() refuses to run on."""
        _, worst, _ = T.planned_calls()
        days = [sum(T.calls_for(t) for t in T.TARGETS.values()
                    if T.due_on(t, T.TODAY_ORD + k)) for k in range(T.plan_horizon())]
        self.assertEqual(worst, max(days))
        self.assertLessEqual(worst, T.BUDGET, "and the shipped config fits under the cap")


class TestTheCutOffProseNamesBothAxes(unittest.TestCase):
    """README: "a car can vanish from the data by being priced *above* the
    day's cut-off … labelled 'priced above today's cut-off' on the dashboard".

    Both halves were wrong for the nationwide certified watches, which sort by
    mileage: window_dim() says so, the committed feed holds such a row, and the
    label the two surfaces actually render says "fetch cut-off", not "priced
    above". how.html spells the two-axis rule out in full, so README was the one
    surface carrying half of it.
    """

    def test_the_axis_is_not_always_a_price(self):
        dims = {t["id"]: T.window_dim(t) for t in T.TARGETS.values()}
        self.assertIn("miles", dims.values(),
                      "the watchlist should still carry a miles-sorted watch "
                      "for this to be about")
        self.assertIn("price", dims.values())

    def test_and_the_readme_says_so(self):
        readme = Path("README.md").read_text()
        self.assertNotIn("priced above today's cut-off", readme,
                         "that label is on neither surface")
        self.assertIn("beyond that day's fetch cut-off", readme,
                      "the label the dashboard really renders")
        self.assertIn("sorted by", readme.lower())
        self.assertIn("mileage", readme.split("beyond that day's fetch cut-off")[1][:600],
                      "…and that one of the two axes is a mileage")

    def test_the_label_readme_quotes_is_the_one_the_page_renders(self):
        # The page holds it inside a single-quoted JS string, so the apostrophe
        # is backslash-escaped in the source and not on the screen.
        page = Path("docs/index.html").read_text().replace("\\'", "'")
        self.assertIn("beyond that day's fetch cut-off", page,
                      "README quotes the page; the page has to say it")
        self.assertIn("beyond that day's fetch cut-off (price or miles)",
                      Path("Tracking.py").read_text(),
                      "…and the record says which axis it was")


# The slowest tier — one EV from each brand outside BMW and the five with a
# record — is DERIVED, not typed. It was 10 and is 15, and every place that
# named the number also named the ordinal word for it, so a literal here would
# be the same rot in a new file. `max` is sound because the prose's own claim is
# that this tier is the slowest one, and
# test_the_config_has_no_tier_the_prose_does_not_name fails if a cadence the
# prose does not name appears above or below it.
TAIL_CADENCE = max(t["cadence"] for t in T.TARGETS.values())
# The word the sentences use. A number with no word raises rather than
# defaulting, because a silent wrong word is exactly what this derivation is
# for — "every 15th day" is not what either surface says.
_ORDINAL_WORD = {2: "other", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth",
                 7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth",
                 12: "twelfth", 14: "fourteenth", 15: "fifteenth",
                 16: "sixteenth", 18: "eighteenth", 20: "twentieth",
                 21: "twenty-first", 28: "twenty-eighth", 30: "thirtieth"}
TAIL_CADENCE_WORD = _ORDINAL_WORD[TAIL_CADENCE]


class TestHowOldTheseCarsAreIsSaidRatherThanImplied(unittest.TestCase):
    """Both surfaces printed a schedule beside an absolute date.

    "**Hyundai Ioniq 9** … _(every 4 days · as of 2026-08-25)_" in the report,
    "Data through Tue, August 25, 2026 · Fetched every 4 days" on the page —
    over cars twelve days old, three due days past that cadence. A reader does
    the only arithmetic offered and concludes the prices are four days old.
    That is the surface whose entire job is saying what a price is worth, and
    it matters more from this round on: the long tail runs fortnightly now, so
    a model being a week and a half behind is the ordinary case rather than a
    fault.

    The age is one rule in Tracking.py and both surfaces render it, because a
    number formatted twice is how this project has grown two vocabularies for
    one mechanism before.
    """

    def test_the_age_is_measured_against_the_record_not_the_clock(self):
        """An offline rebuild a week later must not age every model by a week.
        `TODAY` is the day the file was BUILT; the anchor is the newest day
        anywhere in the record, which is what the masthead shows and what a
        reader subtracts from."""
        self.assertEqual(12, T.fetch_age("2026-08-25", "2026-09-06"))
        self.assertEqual(0, T.fetch_age("2026-09-06", "2026-09-06"))
        # the record moving is what changes the answer; the wall clock is not
        # consulted at all, which this asserts by passing a record day that is
        # nowhere near TODAY and getting the arithmetic of the two arguments
        self.assertEqual(365, T.fetch_age("2025-01-01", "2026-01-01"))

    def test_an_age_it_cannot_compute_is_none_and_never_zero(self):
        """A confident 0 on an unreadable date is the worst answer available:
        it reads as "fetched today"."""
        for as_of, day in ((None, "2026-09-06"), ("2026-08-25", None),
                           ("", "2026-09-06"), ("not-a-date", "2026-09-06"),
                           ("2026-13-45", "2026-09-06"), (["2026-08-25"], "2026-09-06")):
            self.assertIsNone(T.fetch_age(as_of, day), f"{as_of!r} vs {day!r}")
        # …and a record day BEHIND the model's own is clamped rather than
        # reported as a negative age, which no surface has words for
        self.assertEqual(0, T.fetch_age("2026-09-06", "2026-08-25"))

    def test_overdue_is_exact_at_the_cadence_and_not_a_tolerance(self):
        """A target on cadence N is due every Nth day, so the widest gap the
        schedule can account for is N-1 — the day before it next comes round.
        N is one due day missed. Both sides of every boundary, because a
        tolerance quietly added here would make the clause stop appearing on
        the model it was written for."""
        for cad in (2, 3, 4, 15):
            self.assertFalse(T.fetch_overdue(cad - 1, cad), f"cadence {cad}: N-1 is the widest honest gap")
            self.assertTrue(T.fetch_overdue(cad, cad), f"cadence {cad}: N is one due day missed")
        # daily is any gap at all
        self.assertFalse(T.fetch_overdue(0, 1))
        self.assertTrue(T.fetch_overdue(1, 1))
        # a cadence of 0 or a nonsense one cannot make every model overdue
        self.assertFalse(T.fetch_overdue(0, 0))
        self.assertFalse(T.fetch_overdue(None, 4))
        self.assertFalse(T.fetch_overdue(9, "every four days"))

    def test_the_model_entry_carries_both_so_neither_surface_recomputes(self):
        site = json.loads(Path("docs/data.json").read_text())
        day = site["data_through"]
        checked = 0
        for b in site["brands"].values():
            for m in b["models"].values():
                if not m.get("as_of"):
                    continue
                checked += 1
                self.assertEqual(T.fetch_age(m["as_of"], day), m.get("age_days"),
                                 f'{m["label"]}: age_days disagrees with the rule')
                self.assertEqual(T.fetch_overdue(m.get("age_days"), m["cadence"]),
                                 m.get("overdue"),
                                 f'{m["label"]}: overdue disagrees with the rule')
        self.assertGreater(checked, 0, "no model on the sheet has an as_of to check")

    def _entry(self, **over):
        """A model entry shaped the way build_outputs() writes one, for
        compact_line() to render. Only the keys that function reads."""
        e = {"listings": [], "as_of": "2026-08-25", "last_asked": None,
             "cadence": 4, "age_days": 12, "overdue": True,
             "next_due": "2026-09-09", "gone": [], "shopping": False}
        e.update(over)
        return e

    def test_the_report_builds_the_age_into_the_line_it_prints(self):
        """compact_line() EXECUTED, not a committed artifact read back.

        This test used to load REPORT.md and docs/data.json and compare them to
        each other. Nothing in compact_line() ran, so every mutation of the
        tail it is named for left it green — dropping the age clause, dropping
        the overdue clause, printing the schedule beside its own contradiction.
        The rule was really held by the rebuild-and-diff test, which is a
        different test with a different name, and this one reported a safety it
        was not providing. That is the third shape this project's notes
        describe, and the cheapest to write.
        """
        over = T.compact_line(self._entry(), "Test Model")
        self.assertIn("as of 2026-08-25, 12 days ago", over,
                      f"the line gives a date and no age: {over}")
        self.assertIn("past its 4-day cadence", over, over)
        self.assertNotIn("every 4 days", over,
                         "the line states the cadence as a promise and as a "
                         f"promise not kept, in one bracket: {over}")

        keeping = T.compact_line(self._entry(age_days=2, overdue=False,
                                             as_of="2026-09-04"), "Test Model")
        self.assertIn("every 4 days", keeping, keeping)
        self.assertIn("as of 2026-09-04, 2 days ago", keeping, keeping)
        self.assertNotIn("cadence", keeping,
                         f"a model keeping its schedule has nothing to say about it: {keeping}")

        # A daily model that has fallen behind. This branch said "no fetch
        # since" — a claim about whether a run HAPPENED, which fetch_overdue's
        # contract forbids any surface from making, and a phrase the browser
        # checks did not look for, so the page rendered correctly and the suite
        # went red.
        daily = T.compact_line(self._entry(cadence=1, age_days=1,
                                           as_of="2026-09-05"), "Test Model")
        self.assertIn("past its daily cadence", daily, daily)
        self.assertNotIn("no fetch", daily.lower(),
                         "the line claims a fetch did not happen, which the record "
                         f"cannot know: {daily}")

    def test_the_committed_report_agrees_with_the_committed_sheet(self):
        """The artifact comparison the test above used to be, kept for what it
        is: a check that the two files on disk tell one story, not a check of
        the rule that built them."""
        site = json.loads(Path("docs/data.json").read_text())
        report = Path("REPORT.md").read_text()
        aged = [m for b in site["brands"].values() for m in b["models"].values()
                if m.get("age_days") and m.get("listings")]
        if not aged:
            self.skipTest("every model carrying cars was fetched on the record's newest day")
        for m in aged:
            line = next((l for l in report.splitlines()
                         if l.startswith(f'- **{m["label"]}**')), None)
            if line is None:
                continue
            self.assertIn(T.days_ago(m["age_days"]), line,
                          f'{m["label"]}: the report gives a date and no age')
            if m.get("overdue"):
                self.assertIn(T.schedule_phrase(m["cadence"], True), line,
                              f'{m["label"]}: {m["age_days"]} days on a '
                              f'{m["cadence"]}-day cadence, said flatly')

    def test_the_page_and_the_report_share_one_schedule_phrase(self):
        """The other half of the formatter pair, executed the same way.

        Four surfaces render this now — the report's line, the page's model
        card, its two price tiles and its model index — and the round that
        added the first three left the index on the old spelling, so one page
        said "as of Aug 27, 10 days ago" in a tile and "Aug 27 · every 4 days"
        in the row below it. One rule each side, and this holds them equal.
        """
        if not shutil.which("node"):
            self.skipTest("no node on this machine to run the page's formatter through")
        import subprocess
        page = Path("docs/index.html").read_text()
        m = re.search(r"const schedulePhrase = (\([^)]*\) => \{.*?\n  \});", page, re.S)
        self.assertIsNotNone(m, "the page no longer defines schedulePhrase — this "
                                "check has lost its subject")
        cases = [(1, False), (1, True), (2, False), (2, True), (4, True),
                 (4, False), (15, True), (15, False)]
        script = (f"const schedulePhrase = {m.group(1)};\n"
                  f"console.log(JSON.stringify({json.dumps(cases)}"
                  ".map(([c, o]) => schedulePhrase(c, o))));")
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertEqual([T.schedule_phrase(c, o) for c, o in cases],
                         json.loads(out.stdout),
                         "the page and the report spell the schedule differently")

    def test_the_page_spells_an_age_the_same_way_the_report_does(self):
        """The page's own formatter, executed, against Python's. Not a
        comparison of two source files: `days_ago` and `daysAgo` are four lines
        each and the interesting cases are 0 and 1, which reading them
        confidently gets right and which have been got wrong here before.
        """
        if not shutil.which("node"):
            self.skipTest("no node on this machine to run the page's formatter through")
        import subprocess
        page = Path("docs/index.html").read_text()
        m = re.search(r"const daysAgo = (\(n\) => [^;]+);", page)
        self.assertIsNotNone(m, "the page no longer defines daysAgo — this "
                                "check has lost its subject")
        script = (f"const daysAgo = {m.group(1)};\n"
                  "console.log(JSON.stringify([0,1,2,3,12,15,100].map(daysAgo)));")
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(0, out.returncode, out.stderr)
        # 0 is the one the page never renders (the clause is gated on a truthy
        # age) and Python returns "" for; every other value must agree exactly.
        got = json.loads(out.stdout)[1:]
        want = [T.days_ago(n) for n in (1, 2, 3, 12, 15, 100)]
        self.assertEqual(want, got,
                         "the page and the report spell the same age differently")


class TestTheOverlapLogRecordsOnlyQueriesThatFinished(unittest.TestCase):
    """The log the States-query decision rests on must not archive a half-fetch.

    When a page fails after its retry the fetch loop keeps what it has and
    records the scope in FAILED_SCOPES. source_overlap() compared the two sets
    anyway, so a National query that lost half its pages was written down as
    evidence that the States query had bought the cars it never reached — the
    shape of a real finding, and entirely the failure. Reproduced through the
    real function before it was fixed: 7 States, 4 National, one failed scope,
    archived as states_only 3.
    """

    def setUp(self):
        self._vins = dict(T.SOURCE_VINS)
        self._failed = set(T.FAILED_SCOPES)
        T.SOURCE_VINS.clear(); T.FAILED_SCOPES.clear()

    def tearDown(self):
        T.SOURCE_VINS.clear(); T.SOURCE_VINS.update(self._vins)
        T.FAILED_SCOPES.clear(); T.FAILED_SCOPES.update(self._failed)

    TID = "bmw-i7-edrive50"

    def _serve(self, states, national):
        T.SOURCE_VINS[(self.TID, "States")] = {f"S{i}" for i in range(states)}
        T.SOURCE_VINS[(self.TID, "National")] = {f"S{i}" for i in range(national)}

    def test_a_run_where_both_scopes_finished_is_recorded(self):
        """The precondition: without it every assertion below passes on a
        function that records nothing at all."""
        self._serve(7, 4)
        o = T.source_overlap({})
        self.assertIn(self.TID, o, "a clean run must still be measured")
        self.assertEqual(3, o[self.TID]["states_only"])

    def test_a_scope_that_died_is_not_a_scope_that_looked(self):
        for scope in ("National", "States"):
            with self.subTest(scope=scope):
                T.FAILED_SCOPES.clear()
                self._serve(7, 4)
                T.FAILED_SCOPES.add((self.TID, scope))
                self.assertNotIn(self.TID, T.source_overlap({}),
                                 f"a failed {scope} fetch was archived as a measurement; "
                                 "the pages that never arrived read as cars the other "
                                 "source bought")

    def test_another_targets_failure_does_not_suppress_this_one(self):
        """Keyed on the target AND the scope, so one broken query does not
        empty the day's audit."""
        self._serve(7, 4)
        T.FAILED_SCOPES.add(("some-other-target", "National"))
        self.assertIn(self.TID, T.source_overlap({}))


class TestTheWatchlistOnlyPublishesWhatItCanStillFind(unittest.TestCase):
    """A row the watchlist has moved out from under is not current inventory.

    `watchlist_moved()` was only ever asked about a row that VANISHED. A row
    still sitting in the latest snapshot went onto the page as a live listing
    whatever its model year — so narrowing a target's `years` left its old cars
    there, priced, counted, and folded into the floor.

    Found by doing it: splitting the Lucid Air back into trims rebuilt two
    target ids the record already held, and 53 of the 68 listings that came
    back were model year 2022 and 2023 — cars no query on this sheet can return
    again. The departure path had always known that; the live path had never
    been asked.
    """

    def _rows(self, tid, years, day="2026-09-06"):
        base = {k: "" for k in T.FIELDS}
        out = []
        for i, y in enumerate(years):
            r = dict(base)
            r.update({"snapshot_date": day, "target": tid, "year": str(y),
                      "vin": f"V{i:016d}"[:17], "price": "45000", "miles": "12000"})
            out.append(r)
        return out

    def test_a_row_outside_its_targets_years_is_not_a_live_listing(self):
        tid = next(t["id"] for t in T.TARGETS.values() if "2024" in t["years"])
        rows = self._rows(tid, [2022, 2023, 2024, 2025])
        kept = T.current_rows(rows, {tid})
        self.assertEqual(["2024", "2025"], sorted(r["year"] for r in kept),
                         "a model year this watchlist can no longer return was "
                         "published as a car on the market")

    def test_a_row_inside_them_still_is(self):
        """The precondition. Without it the rule above is satisfied by a
        function that returns nothing."""
        tid = next(t["id"] for t in T.TARGETS.values() if "2024" in t["years"])
        rows = self._rows(tid, [2024, 2024, 2025])
        self.assertEqual(3, len(T.current_rows(rows, {tid})))

    def test_only_the_latest_day_survives_either_way(self):
        """The function's original job, which the scope rule must not break."""
        tid = next(t["id"] for t in T.TARGETS.values() if "2024" in t["years"])
        rows = self._rows(tid, [2024, 2025], day="2026-09-01") + \
               self._rows(tid, [2024], day="2026-09-06")
        kept = T.current_rows(rows, {tid})
        self.assertEqual(["2026-09-06"], sorted({r["snapshot_date"] for r in kept}))

    def test_a_target_the_config_no_longer_knows_keeps_its_rows(self):
        """An id with no target cannot be scope-checked against anything, and
        dropping its rows would silently delete history rather than scope it."""
        rows = self._rows("some-retired-target", [2019, 2024])
        kept = T.current_rows(rows, {"some-retired-target"})
        self.assertEqual(2, len(kept),
                         "rows whose target has left the config were dropped; "
                         "there is nothing to judge them against")

    def test_the_published_sheet_holds_no_car_its_own_watchlist_excludes(self):
        """The end-to-end statement, over the committed record."""
        site = json.loads(Path("docs/data.json").read_text())
        bad = []
        for bk, b in site["brands"].items():
            for mk, m in b["models"].items():
                years = set(m.get("years") or [])
                if not years:
                    continue
                for x in (m.get("listings") or []):
                    if str(x.get("year")) and str(x.get("year")) not in years:
                        bad.append(f'{bk}/{mk} {x.get("year")} {x.get("vin")}')
        self.assertEqual([], bad[:10],
                         f"{len(bad)} live listings the watchlist cannot return: {bad[:5]}")


class TestWhatTheSheetWeighsIsWhatTheReadmeSays(unittest.TestCase):
    """The sheet's size is a published number and the file grows every day.

    The browser suite fails the build past 250 KB gzipped, and README now
    quotes what the sheet weighs today and what it will weigh once every target
    is fetching. The first of those moves with every snapshot the daily job
    commits, so it is held to the file rather than to a memory of it — this is
    the fifth number in this repo's prose to be worth pinning, and the previous
    four all rotted.

    The projections are NOT pinned here: they take a synthetic full watchlist
    and would turn CI red on a day the market moved, which is a test whose
    answer depends on the day it runs. `tools/measure_sheet.py` recomputes them
    in under three seconds and README says to run it.
    """

    BUDGET_KB = 250

    @staticmethod
    def _row():
        """The table's first row, as the sheet on disk makes it."""
        import gzip
        site = json.loads(Path("docs/data.json").read_text())
        blob = json.dumps(site, indent=1).encode()
        cars = sum(len(m.get("listings") or [])
                   for b in site["brands"].values() for m in b["models"].values())
        live = sum(1 for b in site["brands"].values() for m in b["models"].values()
                   if m.get("listings"))
        gz = len(gzip.compress(blob, 9))
        return (f"| as committed | {live} | {cars:,} | {round(gz / 1024)} KB | "
                f"{round(gz / (250 * 1024) * 100)}% |")

    def test_the_committed_row_is_the_sheet_on_disk(self):
        readme = " ".join(Path("README.md").read_text().split())
        self.assertIn("| the sheet | models | cars | gzipped | of budget |", readme,
                      "README no longer carries the sheet-size table")
        self.assertIn(self._row(), readme,
                      "README's first row is not the sheet that is committed beside it; "
                      f"it should read {self._row()!r}")

    def test_the_measuring_tool_derives_its_cap_from_the_page_size(self):
        """LIGHT_CARS is what a light target actually reaches, and it has to
        follow PER_PAGE rather than be typed: a page-size change would leave
        the projection describing a fetch the code no longer makes."""
        src = Path("tools/measure_sheet.py").read_text()
        self.assertIn("LIGHT_CARS = 2 * T.PER_PAGE", src,
                      "the tool's per-model cap is no longer derived from PER_PAGE")

    def test_the_budget_the_tool_checks_is_the_one_the_browser_suite_fails_on(self):
        """Two files, one threshold. The smoke script fails the build at it and
        the tool reports against it; if they drift, the tool reports comfort
        the build does not share."""
        smoke = Path("tools/dashboard_smoke.mjs").read_text()
        self.assertIn(f"'data.json': {self.BUDGET_KB} * 1024", smoke)
        self.assertIn(f"cap = {self.BUDGET_KB} * 1024", Path("tools/measure_sheet.py").read_text())


class TestTheDecisionParagraphsNumbersAreTheOnesThePlanGives(unittest.TestCase):
    """The round's own arithmetic, in the two places it is argued.

    docs/how.html's copies of the plan figures are derived from
    planned_calls() and pinned. README's decision paragraph and targets.json's
    "// national_only" comment carry the same numbers — 973 a month, a worst
    day of 38 against 40, and the horizons that ruled out 13 and 14 — and were
    held by nothing. Rewriting the config comment to "700 calls/month", "a
    worst day of 12 against 40" and "plan_horizon() at 7 days" left the whole
    suite green, which is the state a comment is in when it is the only record
    of why a decision was made.

    The horizons are recomputed rather than trusted: 13 and 14 are named as
    rejected BECAUSE of what they do to the cycle, so the sentence is only true
    while that is still what they do.
    """

    @staticmethod
    def _horizon_at(cad):
        """plan_horizon() if the long tail ran at `cad`, by its own rule."""
        cycle = 1
        for t in T.TARGETS.values():
            c = cad if t["cadence"] == TAIL_CADENCE else t["cadence"]
            cycle = math.lcm(cycle, max(1, int(c)))
        return max(14, cycle)

    def test_both_surfaces_quote_the_plan_this_config_produces(self):
        today, worst, avg = T.planned_calls()
        month = round(avg * 30.5)
        readme = " ".join(Path("README.md").read_text().split())
        cfg = json.loads(Path("targets.json").read_text())
        comment = cfg["// national_only"]
        for where, text in (("README", readme), ("targets.json's comment", comment)):
            self.assertIn(str(month), text, f"{where} does not name the monthly plan ({month})")
            self.assertIn(str(worst), text, f"{where} does not name the worst day ({worst})")
            self.assertIn(str(T.BUDGET), text, f"{where} does not name the daily cap ({T.BUDGET})")
            self.assertIn(str(T.MONTHLY), text.replace(",", ""),
                          f"{where} does not name the monthly cap ({T.MONTHLY})")

    def test_the_horizons_that_ruled_out_thirteen_and_fourteen_still_do(self):
        """Both surfaces say fifteen was chosen because it keeps the cycle at
        60 where 13 and 14 push it to 156 and 84. Those three numbers are a
        function of every other cadence on the watchlist, so adding one model
        at a new cadence can make the sentence false without touching it."""
        readme = " ".join(Path("README.md").read_text().split())
        comment = json.loads(Path("targets.json").read_text())["// national_only"]
        here = T.plan_horizon()
        self.assertEqual(here, self._horizon_at(TAIL_CADENCE),
                         "the recomputation disagrees with plan_horizon() at the "
                         "cadence actually configured — the helper is wrong, not the prose")
        for text, where in ((readme, "README"), (comment, "targets.json's comment")):
            self.assertIn(str(here), text, f"{where} does not name the cycle it keeps ({here})")
            for cad in (13, 14):
                self.assertIn(str(self._horizon_at(cad)), text,
                              f"{where} says {cad} was rejected but does not name what it "
                              f"costs ({self._horizon_at(cad)} days)")

    def test_the_tier_the_prose_argues_from_is_the_one_the_config_runs(self):
        """"every tenth day became every fifteenth" is a claim about a real
        move, and the second half of it has to be where the config is. Only
        that half: the first names where the tier came FROM, which is history
        and does not move with the config."""
        readme = " ".join(Path("README.md").read_text().split())
        self.assertIn(f"became every {TAIL_CADENCE_WORD}", readme,
                      f"README says the tier moved somewhere other than {TAIL_CADENCE}")


class TestNoCommentCitesALineNumber(unittest.TestCase):
    """Cite the function, not `file.py:NN`.

    This repo's rule, and nothing enforced it. Six citations named lines of
    docs/index.html — :3818 twice, :3833, :3586, :3580 and :2392 — and every
    one of them pointed at unrelated code: :3818 was meant to be
    buildFilters()'s `#f-model-field` hide rule and lands on a chip-building
    ternary, :3833 was the seen-label comment and lands on a design note. Four
    were already wrong before the round that moved them further, which is the
    shape exactly: a line number is a citation that rots on somebody else's
    commit, silently, and reads as precision.

    Line numbers inside a STRING are left alone — an error message quoting a
    traceback is not a citation — so this looks only at comments.
    """

    ROOTS = ("Tracking.py", "tools", "tests", "docs/index.html", "docs/how.html")
    # A source filename followed by a colon and a line, and the bare
    # parenthesised colon-line form the smoke script used. Written as a pattern
    # rather than shown by example, because an example of a citation IS one —
    # the first draft of this comment carried two and the check found them,
    # which is the check working and the comment being wrong.
    CITE = re.compile(r"(?:[A-Za-z_][\w.-]*\.(?:py|mjs|js|html|json|md)\s*:\s*\d{2,})"
                      r"|(?:\(\s*:\d{3,}\s*\))")

    def _files(self):
        out = []
        for r in self.ROOTS:
            p = Path(r)
            if p.is_file():
                out.append(p)
            elif p.is_dir():
                out += [f for f in p.rglob("*")
                        if f.suffix in (".py", ".mjs", ".js", ".html")
                        and "node_modules" not in f.parts]
        return out

    @staticmethod
    def _comments(text, suffix):
        """Comment text only, so a line number inside a string is not a hit."""
        out = []
        for i, line in enumerate(text.split("\n"), 1):
            if suffix == ".py":
                # after the last quote on the line, a # is a comment; inside a
                # docstring every line counts, which is where the prose lives
                at = line.find("#")
                if at >= 0 and line.count('"', 0, at) % 2 == 0 and line.count("'", 0, at) % 2 == 0:
                    out.append((i, line[at:]))
            else:
                at = line.find("//")
                if at >= 0 and line.count("'", 0, at) % 2 == 0 and line.count('"', 0, at) % 2 == 0:
                    out.append((i, line[at:]))
                at2 = line.find("/*")
                if at2 >= 0:
                    out.append((i, line[at2:]))
        return out

    def test_no_comment_names_a_line_of_another_file(self):
        hits = []
        for f in self._files():
            text = f.read_text(errors="replace")
            for n, body in self._comments(text, f.suffix):
                m = self.CITE.search(body)
                if m:
                    hits.append(f"  {f}:{n} cites {m.group(0)!r} — {body.strip()[:70]}")
        self.assertEqual([], hits,
                         "cite the function, not the line: a line number is a citation "
                         "that rots on somebody else's commit and reads as precision.\n"
                         + "\n".join(hits))

    def test_the_docstrings_carry_prose_this_would_search(self):
        """The precondition. If the walker stopped finding comments at all —
        a suffix filter that matches nothing, a comment parser that returns
        empty — the check above would pass over an empty set and report a rule
        it was no longer applying."""
        seen = sum(len(self._comments(f.read_text(errors="replace"), f.suffix))
                   for f in self._files())
        self.assertGreater(seen, 500,
                           f"only {seen} comment lines found across {len(self._files())} "
                           "files — the walker has lost its subject")


class TestTheWorkedExampleFollowsTheCadenceItIsDrawnFrom(unittest.TestCase):
    """Three files carry the same worked example for seen_label's old form, and
    its numbers are a function of the long tail's cadence.

    "Twenty-eight of the thirty-six models run every tenth day now. A car
    present at every single fetch of one of them read 'seen 4 of 31 days'" —
    four, because a 31-day span holds four fetch days at cadence 10. The tier
    moved to 15 and all three copies went on saying ten and four, in the
    present tense, in the sentence that is the whole justification for the
    label's shape. The prose that was pinned (README, how.html) moved; the
    prose that was not did not.

    Pinned here against the config the same way the surface counts are, so the
    next cadence change fails rather than rots. The span is left as prose — it
    is an illustration, not a measurement — but the fetch count inside it has
    to be the one that cadence produces.
    """

    SPAN = 31
    FILES = ("Tracking.py", "docs/index.html", "tests/test_tracking.py")

    def test_every_copy_names_the_tier_the_config_actually_runs(self):
        want = f"run every {TAIL_CADENCE_WORD} day now"
        for name in self.FILES:
            text = Path(name).read_text()
            if "run every" not in text:
                continue
            self.assertIn(want, text,
                          f"{name} names a cadence tier the config does not run "
                          f"(the tier is {TAIL_CADENCE})")

    def test_the_fetch_count_in_the_example_is_the_one_that_cadence_gives(self):
        """A 31-day span holds ceil(31 / cadence) fetch days for a model on that
        cadence — 4 at ten, 3 at fifteen. The example's "seen N of 31 days" has
        to be that N, and the gap it is contrasted with has to be N-1, or the
        sentence stops making the point it is there to make."""
        n = -(-self.SPAN // TAIL_CADENCE)
        self.assertGreaterEqual(n, 2, "the example needs a gap to contrast with")
        for name in self.FILES:
            text = Path(name).read_text()
            if f"of {self.SPAN} days" not in text and f"of {self.SPAN})" not in text:
                continue
            self.assertIn(f"seen {n} of {self.SPAN} days", text,
                          f"{name}: at cadence {TAIL_CADENCE} a {self.SPAN}-day span "
                          f"holds {n} fetches, and the example says otherwise")
            self.assertNotIn(f"{n + 1}-of-{self.SPAN}", text,
                             f"{name}: the contrast pair is still the old cadence's")


class TestTheStatesQueryPaidForItself(unittest.TestCase):
    """The measurement README's decision rests on, held three ways.

    The 28 national-only targets were standing on `sources_for()`'s premise —
    that a second query into the buyer's own states re-fetches a subset of the
    national answer. `data/source_overlap.json` exists to test that premise and
    had never been read. It is false for exactly the targets it was applied to:
    the redundancy is real at `depth: full`, where the national query fetches
    about a hundred cars, and it collapses at `depth: light`, where it fetches
    twenty — the cheapest twenty in America, of which almost none are drivable.

    Pinning it needs care the obvious way does not survive. The log is appended
    to and `git add data`-ed by the daily job, so a test comparing the prose to
    the LIVE log turns CI red on the next tracker run — the fourth number in
    this repo's prose to rot would have been replaced by one that rots nightly.
    So the window the prose cites is frozen as a fixture, and the three checks
    below divide the work: the fixture cannot be invented, the prose cannot
    drift from the fixture, and the FINDING — not the number — is what is asked
    of the live log.
    """

    WINDOW = Path("tests/fixtures/source_overlap_window.json")

    @classmethod
    def frozen(cls):
        return json.loads(cls.WINDOW.read_text())

    @staticmethod
    def share(rows):
        """States cars found, and the share of them the national query missed."""
        st = sum(r["states"] for r in rows)
        return st, sum(r["states_only"] for r in rows), (sum(r["states_only"] for r in rows) / st if st else None)

    def test_every_frozen_observation_is_one_the_log_really_recorded(self):
        """The fixture is evidence, so it has to BE evidence.

        Each row is checked against the live log by its own day and target. The
        first version of this reasoned "the log only grows, so a frozen row
        that has gone missing means the fixture was edited to fit a sentence" —
        and that is false twice: `save_overlap_history()` prunes to the newest
        `keep` days and overwrites the current day rather than appending. Driven
        forward a day at a time, the window's first day falls out of the live
        log 120 runs after it was recorded, at which point this test would have
        gone red every day thereafter and blamed the fixture for the retention
        policy. A day the log no longer reaches is not checkable; a day it holds
        must match exactly. The precondition below is what stops "not checkable"
        from quietly becoming "nothing checked".
        """
        live = json.loads(Path("data/source_overlap.json").read_text())
        obs = self.frozen()["observations"]
        self.assertEqual(28, len(obs), "the prose names 28 observations")
        oldest = min(live) if live else None
        checkable = [r for r in obs if oldest and r["day"] >= oldest]
        self.assertTrue(checkable,
                        "the live log no longer reaches any day the fixture froze, so "
                        "nothing here is checked — re-cut the fixture against a window "
                        f"the log still holds (log starts {oldest})")
        missing, differs = [], []
        for r in checkable:
            got = live.get(r["day"], {}).get(r["target"])
            if got is None:
                missing.append(f'{r["day"]} {r["target"]}'); continue
            want = [r["states"], r["national"], r["both"], r["states_only"]]
            if list(got[:4]) != want:
                differs.append(f'{r["day"]} {r["target"]}: log {list(got[:4])} vs frozen {want}')
        self.assertEqual([], missing, f"frozen rows the live log does not hold: {missing}")
        self.assertEqual([], differs, f"frozen rows the live log contradicts: {differs}")

    def test_every_frozen_row_carries_the_depth_it_was_fetched_at(self):
        """`depth` is the analytic variable the whole decision turns on, and
        nothing checked it: the previous guard compared only the four counts,
        so the fixture's depths could be inverted and README's two percentages
        would follow, publishing the exact opposite of the finding with the
        suite green.

        Checked against the config for every target the watchlist still holds.
        The rest — targets since merged or stood down — are held to a named
        list rather than to nothing, because "the config no longer knows" is
        exactly the gap the first cut of this fixture fell into: it gave those
        five rows a null depth, dropped them out of both groups, and understated
        the light share by three points.
        """
        RETIRED = {"lucid-air-touring": "light", "lucid-air-grand-touring": "light",
                   "hyundai-ioniq5": "light"}
        wrong, unattributed = [], []
        for r in self.frozen()["observations"]:
            t = T.TARGETS.get(r["target"])
            want = t.get("depth") if t else RETIRED.get(r["target"])
            if want is None:
                unattributed.append(r["target"]); continue
            if r["depth"] != want:
                wrong.append(f'{r["day"]} {r["target"]}: frozen {r["depth"]!r}, really {want!r}')
        self.assertEqual([], wrong, f"the fixture misattributes evidence: {wrong}")
        self.assertEqual([], sorted(set(unattributed)),
                         "these rows are on targets the config no longer knows and are not "
                         "in the retired list either, so their depth is a guess: "
                         f"{sorted(set(unattributed))}")

    def test_the_percentages_the_readme_quotes_are_the_ones_the_window_yields(self):
        """Each figure inside its own sentence, not loose in the file.

        This asserted `assertIn(f"{round(share*100)}%", readme)` — a bare "NN%"
        anywhere in a four-hundred-line README, which already contains 99%, 97%,
        95%, 88%, 35%, 21% and 4% in sentences about other things. Exchanging
        README's two figures so it read "a `depth: full` target … loses 88%" and
        "a `depth: light` target … loses 21%" — the exact inversion of the
        finding that justifies moving 28 targets off national_only — left all
        482 tests green. Reproduced before it was fixed.
        """
        obs = self.frozen()["observations"]
        readme = " ".join(Path("README.md").read_text().split())
        window = self.frozen()["window"]
        self.assertEqual([min(r["day"] for r in obs), max(r["day"] for r in obs)], window,
                         "the fixture's window does not span its own observations")
        self.assertIn(f"between {window[0]} and {window[1]}", readme,
                      "the prose must name the window it read")
        st, only, _ = self.share(obs)
        self.assertIn(f"the {len(obs)} observations recorded", readme,
                      "the headline count is quoted and must be the fixture's")
        self.assertIn(f"States query found {st} cars and {only} of them were invisible", readme)
        # each share attached to the depth it describes, as one contiguous run
        shares = {}
        for depth in ("full", "light"):
            rows = [r for r in obs if r["depth"] == depth]
            self.assertTrue(rows, f"no {depth} observation in the window")
            shares[depth] = self.share(rows)[2]
        self.assertIn(f"a `depth: full` target — about a hundred cars nationally — "
                      f"loses {round(shares['full'] * 100)}% by dropping its States half",
                      readme, f"the full share is {shares['full']:.0%}")
        self.assertIn(f"A `depth: light` target fetches twenty, the whole country, cheapest "
                      f"first, and loses **{round(shares['light'] * 100)}%**",
                      readme, f"the light share is {shares['light']:.0%}")
        # …and the DIRECTION, so an inversion fails even if both digits are real
        self.assertGreater(shares["light"], shares["full"],
                           "the fixture no longer shows what the paragraph claims")

    def test_the_finding_still_holds_on_the_log_as_it_stands_today(self):
        """The one that is allowed to fail. Everything above pins a sentence to
        a frozen window; this asks the CURRENT log the question the decision
        turns on, and it is deliberately about the gap rather than either
        number, because the numbers move every night and the gap is the reason
        28 targets changed shape.

        A depth that has left the watchlist is skipped rather than guessed: a
        row whose target no longer exists cannot be attributed to a group.
        """
        live = json.loads(Path("data/source_overlap.json").read_text())
        rows = {"full": [], "light": []}
        for day, tgts in live.items():
            for tid, v in tgts.items():
                t = T.TARGETS.get(tid)
                if not t or t.get("depth") not in rows:
                    continue
                st, nat, both, only = v[:4]
                rows[t["depth"]].append({"states": st, "states_only": only})
        for d in rows:
            if not rows[d]:
                self.skipTest(f"the live log holds no {d} observation to compare")
        _, _, full = self.share(rows["full"])
        _, _, light = self.share(rows["light"])
        self.assertGreater(light, full + 0.25,
                           "the whole reason 28 targets ask their own states is that a "
                           f"light target loses far more by not asking: light {light:.0%} "
                           f"vs full {full:.0%} on today's log. If that gap has closed, the "
                           "cadence those targets pay for it in is being spent for nothing "
                           "and README's paragraph is out of date.")


NUMBER_WORD = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
               7: "seven", 8: "eight", 9: "nine", 10: "ten"}


class TestTheCadenceProseMatchesTheConfig(unittest.TestCase):
    """"BMW siblings run every other day, rival brands every third day", on a
    watchlist where four of the six non-shopped BMW targets are on cadence 3.

    The tier the prose described was by BRAND and the config is by TARGET, so
    the sentence was false for the i7's own trims and for the whole iX. Both
    surfaces carried it, and the call figures quoted beside it — 29 a day, 884
    a month, worst day 32 of 40 — are the config's, not the sentence's.
    """

    def _by_cadence(self):
        out = {}
        for t in T.TARGETS.values():
            out.setdefault(t["cadence"], []).append(t["id"])
        return out

    def test_no_surface_claims_a_cadence_tier_by_brand(self):
        for name in ("README.md", "docs/how.html"):
            text = Path(name).read_text()
            self.assertNotIn("BMW siblings run every other day", text, name)
            self.assertNotIn("BMW siblings are fetched every other day", text, name)

    def test_the_daily_targets_are_shopped_trims_and_there_are_two(self):
        daily = sorted(self._by_cadence().get(1, []))
        self.assertEqual(len(daily), 2, f"the prose says two run daily: {daily}")
        self.assertTrue(set(daily) <= set(T.SHOPPING),
                        f"…and that they are shopped ones: {daily}")

    def test_every_other_day_is_the_i5s_other_trims_and_nothing_else(self):
        every_other = sorted(self._by_cadence().get(2, []))
        self.assertEqual(len(every_other), 3,
                         f"the prose says the i5's other three: {every_other}")
        self.assertTrue(all(t.startswith("bmw-i5-") for t in every_other),
                        f"…and that all three are the i5's: {every_other}")
        self.assertIn("bmw-i5-cpo", every_other,
                      "the prose names the certified watch as one of them")

    def test_the_third_day_tier_is_the_i7s_other_trims_and_the_ix(self):
        """It used to hold every rival too. One EV per brand moved the rivals
        onto their own tiers, so the sentence naming them here had to move
        with them — this is the half that fails if only one of the two does."""
        rest = sorted(self._by_cadence().get(3, []))
        self.assertTrue(rest and all(t.startswith("bmw-i7-") or t.startswith("bmw-ix")
                                     for t in rest),
                        f"the prose says the i7's other trims and the iX: {rest}")

    def test_the_fourth_day_tier_is_the_models_that_already_have_a_record(self):
        """Derived from the prose rather than a literal five. The Lucid Air was
        on this tier and left it: its record is 257 rows of which most are model
        years the 2024+ watchlist can no longer return, so it was here on the
        strength of history the config cannot reproduce."""
        kept = sorted(self._by_cadence().get(4, []))
        readme = " ".join(Path("README.md").read_text().split())
        self.assertIn(f"the {NUMBER_WORD[len(kept)]} models already carrying a record", readme,
                      f"the tier holds {len(kept)} and README says otherwise: {kept}")
        self.assertFalse([t for t in kept if t.startswith("bmw-")],
                         f"…and that none of them is a BMW: {kept}")
        self.assertFalse([t for t in kept if T.TARGETS[t].get("national_only")],
                         f"…and that they are the ones that kept both queries: {kept}")

    def test_the_slowest_tier_is_one_ev_a_brand_asking_both_queries(self):
        """Derived, not counted. This asserted `== 27` and went red the moment
        a brand was added — which is the guard working, and also a literal
        doing a rule's job. The rule is: every brand outside BMW that is not
        one of the five with a record, one target each.

        It said "national query only" for as long as the tier was national_only,
        and that half is now inverted rather than dropped: the overlap log
        settled the question these targets were standing on the wrong side of,
        so the tier asks BOTH queries and the assertion is that none of them is
        national_only. Inverted rather than deleted because it is the same rule
        the prose states, and a rule that stops being asserted the moment it
        changes is the assertion this file exists to avoid.
        """
        ref = sorted(self._by_cadence().get(TAIL_CADENCE, []))
        kept = {T.TARGETS[t]["brand"] for t in self._by_cadence().get(4, [])}
        self.assertEqual({T.TARGETS[t]["brand"] for t in ref},
                         {t["brand"] for t in T.TARGETS.values()} - kept - {"bmw"})
        self.assertFalse([t for t in ref if T.TARGETS[t].get("national_only")],
                         "the prose says every one of them asks its own states too")
        self.assertEqual(len({T.TARGETS[t]["brand"] for t in ref}),
                         len({T.TARGETS[t]["model_key"] for t in ref}),
                         "…one nameplate from each — a brand may carry trims of it, "
                         "the way the Lucid Air does, but not a second model")

    def test_the_config_has_no_tier_the_prose_does_not_name(self):
        named = {1, 2, 3, 4, TAIL_CADENCE}
        others = {c for c in self._by_cadence() if c not in named}
        self.assertFalse(others, f"both surfaces name {sorted(named)}, "
                                 f"the config also has {sorted(others)}")

    def test_the_call_figures_both_surfaces_quote(self):
        today, worst, avg = T.planned_calls()
        for name, needed in (("README.md", [f"about {round(avg)} API calls a day"]),
                             ("docs/how.html", [f"about {round(avg)} calls a day",
                                                f"roughly {round(avg * 30.5)} a month",
                                                f"worst day in the {T.plan_horizon()}-day cycle at {worst}",
                                                f"hard cap of {T.BUDGET}"])):
            text = Path(name).read_text()
            for want in needed:
                self.assertIn(want, text, f"{name}: {want}")

    def test_the_brand_counts_both_surfaces_quote(self):
        """Four numbers in prose that move with the watchlist.

        README's opening names the brands beside BMW; both surfaces name the
        tenth-day tier. Nothing pinned either, and a brand added or dropped
        makes them false in a sentence nobody re-reads.
        """
        brands = {t["brand"] for t in T.TARGETS.values()}
        others = len(brands - {"bmw"})
        # BRANDS, because that is the noun the sentence uses. It counted
        # targets, which was the same number only while every tail model was
        # trimless; the Lucid Air carries three trims now and would have made
        # the prose claim seventeen brands where there are fifteen.
        tail = len({t["brand"] for t in T.TARGETS.values() if t["cadence"] == TAIL_CADENCE})
        word = TAIL_CADENCE_WORD
        readme = " ".join(Path("README.md").read_text().split())
        how = " ".join(Path("docs/how.html").read_text().split())
        self.assertIn(f"each of the other {others} brands", readme)
        self.assertIn(f"other {tail} brands every {word} day", readme)
        self.assertIn(f"other {tail} brands every {word} day", how)
        # The `5` here was a literal for the fourth-day tier and went stale the
        # moment the Lucid Air left it. Derived from the config, so the
        # invariant survives a tier changing size: outside BMW there are
        # exactly two tiers, the ones with a record and the rest, and every
        # brand is in one of them.
        recorded = len({t["brand"] for t in T.TARGETS.values() if t["cadence"] == 4})
        self.assertEqual(others, tail + recorded,
                         f"outside BMW there are two tiers — {recorded} brands with a "
                         f"record and {tail} in the tail — and they should account for "
                         f"all {others}; if they do not, the sentences above describe a "
                         "watchlist that no longer exists")

    def test_the_cycle_length_both_surfaces_quote(self):
        """README says the cycle is 60 days in prose and how.html names it in
        the worst-day clause. Neither was pinned while the cadences were 1/2/3
        and the horizon was the fortnight floor, so the day a cadence widened
        it both sentences went quietly false."""
        flat = " ".join(Path("README.md").read_text().split())
        self.assertIn(f"the cycle is {T.plan_horizon()} days", flat)


class TestTheComparisonPreambleDescribesTheQueriesItRan(unittest.TestCase):
    """"the 20 lowest asking … per model", over models the same block lists at
    66 and 68.

    The scan is per TARGET. Every comparison model on this watchlist carries two
    trim targets, each fetching one page of PER_PAGE from each of two sources,
    so the cap is 40 a target and 80 a model — and the union of two trim-sliced
    queries is not "the 20 lowest asking" of the model at all.
    """

    def test_the_counts_it_prints_can_run_past_the_number_it_names(self):
        """The preamble is checked against the arithmetic of the plan, not
        against today's market: a model with two trims can hold twice the cap
        of one, and the sentence has to allow for it."""
        multi = [mk for mk in {t["model_key"] for t in T.TARGETS.values()}
                 if len([t for t in T.TARGETS.values()
                         if t["model_key"] == mk and not t["shopping"]]) > 1]
        self.assertTrue(multi, "the watchlist should still have a two-trim "
                               "comparison model for this to be about")
        report = Path("REPORT.md").read_text()
        if "## Comparison" not in report:
            self.skipTest("the committed record has no comparison block")
        pre = report.split("## Comparison")[1].split("\n\n")[1]
        self.assertIn(f"the {T.PER_PAGE} lowest asking", pre)
        self.assertIn("per TRIM queried", pre,
                      "the cap is per target, and a model can carry several")
        for line in report.split("## Comparison")[1].splitlines():
            m = re.match(r"- \*\*(.+?)\*\* — (\d+) cars", line)
            if m and int(m.group(2)) > 2 * T.PER_PAGE:
                self.assertIn("run past 20", pre,
                              f"{m.group(1)} holds {m.group(2)} cars under a "
                              f"sentence naming {T.PER_PAGE}")


class TestTheNonMonotoneIllustrationIsAboutTheseBands(unittest.TestCase):
    """The figures README and band_cost() use to argue for marginal banding.

    Both said "$574 at 423 miles and $425 at 424". Those come from the ORIGINAL
    bands 1.15/0.85/0.68/0.58 and were carried forward verbatim when the bands
    were re-cut to 1.20/0.70/0.45/0.30 — which is the rot ship_for()'s own
    docstring records happening to it once already: "Every figure in it was
    false by the time it was committed". The sweep that class calls for was not
    run over these two.

    Derived here from the live config rather than typed, so the next re-cut
    either moves the prose or turns this red.
    """

    def _replace_model(self, road):
        """The rejected model: one band's rate applied to the whole distance."""
        for edge, rate in T.SHIP_BANDS:
            if edge is None or road <= edge:
                return round(road * rate)
        return round(road * T.SHIP_BANDS[-1][1])

    def test_the_two_numbers_both_places_quote(self):
        lo = self._replace_model(423 * T.SHIP_ROAD_FACTOR)
        hi = self._replace_model(424 * T.SHIP_ROAD_FACTOR)
        self.assertGreater(lo, hi, "the illustration is a car one mile further "
                                   "away costing less")
        want = f"${lo} at 423 miles and ${hi} at 424"
        readme = Path("README.md").read_text()
        self.assertIn(want, readme, f"README quotes something else: {want!r}")
        # One line, because a docstring wraps and the sentence is the claim.
        doc = " ".join(T.band_cost.__doc__.split())
        self.assertIn(f"at 423 straight-line miles it charged ${lo} and at 424 "
                      f"it charged ${hi}", doc)
        self.assertIn(f"was ${lo - hi} cheaper to bring home", doc)

    def test_and_the_property_they_are_defending_holds(self):
        """The point of the illustration, checked rather than illustrated."""
        prev = -1
        for d in range(0, 6001, 7):
            cost = T.band_cost(d * T.SHIP_ROAD_FACTOR)
            self.assertGreaterEqual(cost, prev, f"{d} miles cost less than {d - 7}")
            prev = cost


class TestAMistypedBandIsNamedNotFatal(unittest.TestCase):
    """_ship_bands()'s whole docstring is about surviving a hand-edited config,
    and it checks `per_mile` and `to` one level in — while a band that is not an
    object at all raised AttributeError from a module-level constant, before
    anything ran. The sibling key does exactly this check and says why:
    ship_calibration() calls a quote with the wrong key "indistinguishable from
    no quote at all"."""

    def test_an_entry_that_is_not_a_band_is_named_and_dropped(self):
        for bad in ("1000:0.7", 7, ["to", 500], None, True):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                got = T._ship_bands([{"to": 500, "per_mile": 1.2}, bad,
                                     {"to": None, "per_mile": 0.3}])
            self.assertEqual(got, [(500.0, 1.2), (None, 0.3)], f"with {bad!r} in the list")
            self.assertIn("not a band", out.getvalue(), f"with {bad!r} in the list")

    def test_a_ship_bands_that_is_not_a_list_is_named_and_ignored(self):
        for bad in ("1.20/0.70", {"to": 500, "per_mile": 1.2}, 3):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(T._ship_bands(bad), [], f"{bad!r}")
            self.assertIn("not a list", out.getvalue(), f"{bad!r}")

    def test_and_a_config_with_none_of_that_is_unchanged(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(T._ship_bands([{"to": 500, "per_mile": 1.2},
                                            {"to": None, "per_mile": 0.3}]),
                             [(500.0, 1.2), (None, 0.3)])
            self.assertEqual(T._ship_bands(None), [])
        self.assertEqual(T.SHIP_BANDS, T._ship_bands(T.BUYER.get("ship_bands")),
                         "and the shipped config still reads as it did")


class TestAMalformedFetchLogDoesNotKillTheRun(unittest.TestCase):
    """The one file the run writes for itself and a human may edit.

    load_fetch_log() checked `isinstance(log, dict)` and nothing below it, while
    delisted() goes three levels deeper — `(log.get(day) or {}).get(tid)`,
    `[logged.get(k) for k in keys]`, `f["window"] > price` — and
    save_fetch_log() does the same. Every crash lands inside build_outputs(),
    which main() reaches only after the entire API budget has been billed: the
    day would leave no snapshot row and no report for calls that were paid for.

    One shape is worse than a crash and is why the window is checked by TYPE:
    `"window": true` passed every guard, because isinstance(True, int) is True
    in Python, and a $59,000 car compared against 1 was published as a departure
    the record then declines to price — quietly, on a file nobody re-reads.
    """

    TID = "bmw-i5-m60"
    GOOD = {"window": 60000, "dim": "price", "exhausted": False, "failed": False, "raw": 40}

    def setUp(self):
        import tempfile
        self._keep = (dict(T.PRICE_WINDOW), set(T.EXHAUSTED), set(T.FAILED_SCOPES), T.FETCH_LOG)
        T.PRICE_WINDOW.clear(); T.EXHAUSTED.clear(); T.FAILED_SCOPES.clear()
        self._td = tempfile.TemporaryDirectory()
        T.FETCH_LOG = Path(self._td.name) / "fetch_log.json"
        d1 = date.fromordinal(T.TODAY_ORD - 1).isoformat()
        def row(vin, day, price):
            r = {k: "" for k in T.FIELDS}
            r.update({"target": self.TID, "vin": vin, "snapshot_date": day, "price": price,
                      "year": "2025", "trim": "M60", "miles": 9000, "state": "IL", "city": "Chicago"})
            return r
        self.rows = [row("V" * 17, d1, 59000), row("K" * 17, d1, 40000),
                     row("K" * 17, T.TODAY, 40000)]
        self.today = [r for r in self.rows if r["snapshot_date"] == T.TODAY]
        self.hist = T.build_history(self.rows)
        self.d1 = d1

    def tearDown(self):
        pw, ex, fs, log = self._keep
        T.PRICE_WINDOW.clear(); T.PRICE_WINDOW.update(pw)
        T.EXHAUSTED.clear(); T.EXHAUSTED.update(ex)
        T.FAILED_SCOPES.clear(); T.FAILED_SCOPES.update(fs)
        T.FETCH_LOG = log
        self._td.cleanup()

    def _verdict(self, shape):
        T.FETCH_LOG.write_text(json.dumps(shape))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            gone = T.delisted({self.TID}, self.rows, self.today, self.hist)
        return {g["vin"]: (g["likely"], g.get("exact")) for g in gone}, out.getvalue()

    def test_the_good_log_is_the_control(self):
        got, said = self._verdict({T.TODAY: {self.TID: {"National": self.GOOD,
                                                        "States": self.GOOD}}})
        self.assertEqual(got["V" * 17], ("delisted", True),
                         "a window that reached past the car, and it was not there")
        self.assertNotIn("malformed", said, "nothing to drop in a clean log")

    def test_none_of_them_crashes_and_every_one_says_so(self):
        bad = []
        for name, make in MALFORMED_LOGS:
            shape = make(T.TODAY, self.TID, self.GOOD)
            try:
                got, said = self._verdict(shape)
            except Exception as e:                        # noqa: BLE001 — the point
                bad.append(f"{name}: {type(e).__name__}: {e}")
                continue
            if got["V" * 17] == ("delisted", True):
                bad.append(f"{name}: still published a confirmed departure")
        self.assertEqual(bad, [],
                         "a log the run cannot read must not decide a departure, "
                         "and must not take the run down after the calls are paid")

    def test_a_boolean_window_is_not_a_window_of_one(self):
        """isinstance(True, int) — the trap this file's sister project hit at a
        different level. Read off the loaded log, because the verdict it
        produces happens to match the fallback's on this fixture and would pin
        nothing."""
        T.FETCH_LOG.write_text(json.dumps(
            {T.TODAY: {self.TID: {"National": {**self.GOOD, "window": True}}}}))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(T.load_fetch_log(), {})

    def test_a_good_entry_beside_a_bad_one_survives(self):
        """Dropping the file wholesale would throw away the days that are fine,
        and every one of those is a day whose departures can still be judged
        exactly."""
        T.FETCH_LOG.write_text(json.dumps({
            self.d1: {self.TID: {"National": "rubbish"}},
            T.TODAY: {self.TID: {"National": self.GOOD, "States": self.GOOD}}}))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            log = T.load_fetch_log()
        self.assertEqual(list(log), [T.TODAY])
        self.assertEqual(log[T.TODAY][self.TID]["National"], self.GOOD)
        self.assertIn("dropped 1 malformed entry", out.getvalue())


class TestTheWeeklyLoopKnowsWhenItDidNotLook(unittest.TestCase):
    """The one automation whose stated job is "the remembering".

    Its lint step pipes the linter through `tee`, and this file — unlike
    check.yml, which runs the identical line — set no `pipefail`, so a crash
    returned 0. `set -e` does not abort on a failure inside a `||` list either,
    so both clones could fail and the script carried on. `continue-on-error`
    would have swallowed the step's status regardless. What reached the digest
    was an empty /tmp/report.txt and an unset `open_candidates`, and
    `Number('') === 0` — so a run that looked at nothing read as a run that
    found nothing, and the digest commented "every candidate has a recorded
    verdict and the pin is current. Closing." and closed the tracking issue.

    Both halves are executed here rather than read: the shell against a stub
    git and node, the digest script against a stub `github`.
    """

    @staticmethod
    def _block(name, key):
        text = (Path(__file__).parent.parent / ".github/workflows/loop.yml").read_text()
        head = text.index(f"- name: {name}\n")
        at = text.index(f"{key}: |\n", head) + len(f"{key}: |\n")
        lines = text[at:].split("\n")
        indent = len(lines[0]) - len(lines[0].lstrip(" "))
        body = []
        for ln in lines:
            if ln.strip() == "":
                body.append("")
                continue
            if len(ln) - len(ln.lstrip(" ")) < indent:
                break
            body.append(ln[indent:])
        return "\n".join(body).rstrip() + "\n"

    # The pin used to be grepped out of docs/index.html; it is now whatever
    # tools/design_snapshot.mjs prints, so `node` is called TWICE in this block
    # for two unrelated jobs. A stub that fails both cannot tell the three
    # failure modes apart — and did not: with one blanket-failing `node`, the
    # linter-crash test below was green because the SNAPSHOT step died, which
    # is a different rule rejecting than the one its name promises. The stub
    # dispatches on the argument, so each test fails on its own mechanism.
    PIN_STUB = "0123456789abcdef0123456789abcdef01234567"
    NODE_STUB = ('#!/bin/sh\n'
                 'case "$*" in\n'
                 '  *design_snapshot*) echo ' + PIN_STUB + '; exit 0 ;;\n'
                 'esac\n'
                 'echo "cannot find module" >&2\n'
                 'exit 1\n')

    def _run_lint_step(self, tmp, git, node):
        """Execute loop.yml's own lint step against stub binaries.

        Every git stub is prefixed with a line recording what it was asked
        for, into the step's own working directory. That log is the only
        thing that can tell "the linter died" apart from "the step never got
        that far" — the stdout cannot, because a snapshot step and a linter
        step both die through the same `node`.
        """
        import subprocess
        (tmp / "bin").mkdir()
        for name, script in (("git", git.replace("#!/bin/sh\n", '#!/bin/sh\necho "$*" >> git-calls\n', 1)),
                             ("node", node)):
            f = tmp / "bin" / name
            f.write_text(script)
            f.chmod(0o755)
        return subprocess.run(
            ["bash", "-e", "-c",
             self._block("Run the consumer policy against the pinned sheet", "run")],
            cwd=tmp, capture_output=True, text=True,
            env={**os.environ, "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}",
                 "GITHUB_STEP_SUMMARY": str(tmp / "sum")})

    @staticmethod
    def _git_calls(tmp):
        f = tmp / "git-calls"
        return f.read_text() if f.exists() else ""

    def test_a_clone_that_fails_takes_the_step_down_with_it(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            r = self._run_lint_step(
                tmp, '#!/bin/sh\nif [ "$1" = "clone" ]; then exit 128; fi\nexit 0\n',
                self.NODE_STUB)
            self.assertNotEqual(r.returncode, 0,
                                "a step that could not fetch the sheet it lints must "
                                f"not report success:\n{r.stdout}\n{r.stderr}")
            self.assertIn(f"could not fetch design-system@{self.PIN_STUB}",
                          r.stdout + r.stderr,
                          "…and it must say which thing it could not get")

    def test_a_snapshot_that_will_not_verify_takes_the_step_down_first(self):
        """The third failure mode, and the one the local snapshot introduced.

        The pin is no longer a string grepped out of a page: it is the output
        of a tool that verifies every digest in docs/design-system first. If
        that tool refuses, there is no ref to clone and nothing to lint — so
        the step must stop THERE, before a clone reports success against an
        empty `$PIN` and the linter runs against whatever it dragged down.
        `set -e` aborts an assignment whose command substitution failed; that
        is executed here rather than assumed, because it is the whole guard.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            r = self._run_lint_step(
                tmp, '#!/bin/sh\nmkdir -p /tmp/ds-unused\nexit 0\n',
                '#!/bin/sh\ncase "$*" in\n'
                '  *design_snapshot*) echo "Design asset changed outside the atomic'
                ' snapshot: sc.css" >&2; exit 1 ;;\nesac\nexit 0\n')
            self.assertNotEqual(r.returncode, 0,
                                "a snapshot that will not verify has no ref to lint "
                                f"against; the step must stop:\n{r.stdout}\n{r.stderr}")
            self.assertNotIn("could not fetch design-system@", r.stdout + r.stderr,
                             "…and it must stop at the snapshot, not carry an empty "
                             "pin as far as the clone")
            self.assertEqual("", self._git_calls(tmp),
                             "…and git must not have been asked for anything at all: "
                             "a clone against an empty ref is how a wrong sheet gets "
                             "linted as if it were the right one")

    def test_a_linter_that_crashes_takes_the_step_down_too(self):
        """The other half, and the one only `pipefail` catches: the checkout
        arrives and the linter itself dies. `tee` returns 0 over it, which is
        why check.yml sets pipefail on the identical line and this file did
        not."""
        import tempfile, shutil as _sh
        ds = Path("/tmp/ds")
        had = ds.exists()
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            try:
                r = self._run_lint_step(
                    tmp, "#!/bin/sh\nmkdir -p /tmp/ds && : > /tmp/ds/sc.css\nexit 0\n",
                    self.NODE_STUB)
                calls = self._git_calls(tmp)
            finally:
                if not had:
                    _sh.rmtree(ds, ignore_errors=True)
        self.assertNotEqual(r.returncode, 0,
                            "the checkout was there and the linter died; `tee` "
                            f"returns 0 over that unless pipefail is set:\n{r.stdout}\n{r.stderr}")
        # The discriminator is not the stub's message — a blanket-failing `node`
        # prints the same words from the snapshot step, and `set -e` aborts the
        # assignment before the clone, so stdout looks identical. What differs
        # is that the clone RAN: reaching the linter means the pin resolved and
        # /tmp/ds/sc.css was there, so the death below it can only be the
        # linter's. Measured from the git log, not read off the output.
        self.assertIn(self.PIN_STUB, calls,
                      "…and it must be the LINTER that died, not the snapshot step "
                      "above it: nothing ever asked git for the verified pin, so "
                      f"this would pass on a rule it does not name:\n{calls!r}")
        self.assertNotIn("could not fetch design-system@", r.stdout + r.stderr,
                         f"the clone guard fired, so the linter was never reached:"
                         f"\n{r.stdout}\n{r.stderr}")
        self.assertIn("cannot find module", r.stdout + r.stderr,
                      f"the linter's own failure never reached the log:\n{r.stderr}")

    def _digest(self, outcome, candidates, report, has_issue):
        import subprocess, tempfile, json as _json
        if not shutil.which("node"):
            self.skipTest("no node on this machine to run the digest script through")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "digest.js").write_text(self._block("Open or update the digest issue", "script"))
            (tmp / "report.txt").write_text(report)
            (tmp / "run.mjs").write_text(RUN_DIGEST_HARNESS.replace("REPORT_PATH", str(tmp / "report.txt")))
            r = subprocess.run(["node", str(tmp / "run.mjs"), str(tmp / "digest.js")],
                               capture_output=True, text=True,
                               env={**os.environ, "LINT_OUTCOME": outcome,
                                    "OPEN_CANDIDATES": candidates,
                                    "HAS_ISSUE": "1" if has_issue else ""})
            self.assertEqual(r.returncode, 0, r.stderr)
            return _json.loads(r.stdout)

    def test_a_run_that_could_not_check_says_so_and_closes_nothing(self):
        acts = self._digest("failure", "", "", has_issue=True)
        self.assertNotIn("closed", [a[1] for a in acts],
                         f"it must not close the tracking issue: {acts}")
        self.assertTrue(any("could not run" in str(a) for a in acts),
                        f"…and it must say why: {acts}")

    def test_a_success_with_nothing_to_show_is_not_a_clean_run_either(self):
        """A step can exit 0 and produce no report — a linter that dies after
        its own exit code is set, a redirect that goes nowhere. There is no
        report to read "nothing open" out of, so there is no all-clear to
        give."""
        acts = self._digest("success", "0", "", has_issue=True)
        self.assertNotIn("closed", [a[1] for a in acts], f"{acts}")

    def test_a_run_that_checked_and_found_nothing_still_closes(self):
        """The other side: the all-clear is right when it was earned."""
        acts = self._digest("success", "0", "ok    pin v2.4.0 is the newest\nconsumer-lint policy: clean\n",
                            has_issue=True)
        self.assertIn("closed", [a[1] for a in acts], f"{acts}")

    def test_and_an_open_candidate_still_opens_the_issue(self):
        acts = self._digest("success", "2", "note  index.html — 2 candidate(s)\n", has_issue=False)
        self.assertEqual([a[0] for a in acts][:1], ["create"], f"{acts}")


RUN_DIGEST_HARNESS = """
import * as realfs from 'node:fs';
const body = realfs.readFileSync(process.argv[2], 'utf8');
const acts = [];
const github = { rest: { issues: {
  createLabel: async () => ({}),
  listForRepo: async () => ({ data: process.env.HAS_ISSUE ? [{ number: 7 }] : [] }),
  createComment: async (a) => acts.push(['comment', String(a.body).split('\\n')[1]]),
  update: async (a) => acts.push(['update', a.state || ('body: ' + String(a.body).split('\\n')[1])]),
  create: async (a) => (acts.push(['create', a.title]), { data: { number: 9 } }),
} } };
const context = { repo: { owner: 'o', repo: 'r' } };
const core = { info: (m) => acts.push(['info', m]) };
const fs = { readFileSync: (p, e) => realfs.readFileSync(p === '/tmp/report.txt' ? 'REPORT_PATH' : p, e),
             existsSync: (p) => realfs.existsSync(p === '/tmp/report.txt' ? 'REPORT_PATH' : p) };
const require = (m) => (m === 'fs' ? fs : null);
const fn = new Function('github', 'context', 'core', 'require', 'process',
  '"use strict"; return (async () => {' + body + '})();');
await fn(github, context, core, require, process);
console.log(JSON.stringify(acts));
"""


class TestASecondRunOfTheDayLeavesOneAnswer(unittest.TestCase):
    """The fetch log is the only record of what was ASKED, and the CSV keeps
    only what the last run KEPT.

    save_fetch_log() merged a second run of the same day by taking the widest
    window either reached, reasoning that "the union is what the day actually
    saw" — while main() rebuilds the CSV as every row that is not today's plus
    this run's, so the first run's rows are gone. The log then claimed a reach
    the surviving rows cannot support, and delisted() turned "pushed out of the
    window" into a confirmed departure on every later read: tomorrow's run,
    tools/rebuild_outputs.py, the workflow's own exit-3 rebuild. That verdict
    carries exact=True, which departure_is_evidence() admits into the published
    exit prices and the "N gone" headline.

    ALLOW_REFETCH is not a corner: it is a tick-box on the workflow's manual
    dispatch, put there for exactly this case.
    """

    TID = "bmw-i5-xdrive40"

    def setUp(self):
        import tempfile
        self._keep = (dict(T.PRICE_WINDOW), set(T.EXHAUSTED), set(T.FAILED_SCOPES),
                      dict(T.RAW_N), T.FETCH_LOG)
        self._td = tempfile.TemporaryDirectory()
        T.FETCH_LOG = Path(self._td.name) / "fetch_log.json"

    def tearDown(self):
        pw, ex, fs, raw, log = self._keep
        T.PRICE_WINDOW.clear(); T.PRICE_WINDOW.update(pw)
        T.EXHAUSTED.clear(); T.EXHAUSTED.update(ex)
        T.FAILED_SCOPES.clear(); T.FAILED_SCOPES.update(fs)
        T.RAW_N.clear(); T.RAW_N.update(raw)
        T.FETCH_LOG = log
        self._td.cleanup()

    def _run(self, window, raw=40, exhausted=False, failed=False):
        """One run of the day, through the real fetch_log_row()/save_fetch_log()."""
        T.PRICE_WINDOW.clear(); T.EXHAUSTED.clear(); T.FAILED_SCOPES.clear(); T.RAW_N.clear()
        for src in ("States", "National"):
            key = (self.TID, src)
            T.PRICE_WINDOW[key] = window
            T.RAW_N[key] = raw
            if exhausted: T.EXHAUSTED.add(key)
            if failed: T.FAILED_SCOPES.add(key)

    def _rows(self):
        d1 = date.fromordinal(T.TODAY_ORD - 1).isoformat()
        def row(vin, day, price):
            r = {k: "" for k in T.FIELDS}
            r.update({"target": self.TID, "vin": vin, "snapshot_date": day,
                      "price": price, "year": "2025", "trim": "xDrive40",
                      "miles": 9000, "state": "IL", "city": "Chicago"})
            return r
        # The dear car was seen by the FIRST run and its row is gone: the CSV
        # holds only what the second run kept, which is the cheap one.
        return [row("V" * 17, d1, 59000), row("K" * 17, d1, 40000),
                row("K" * 17, T.TODAY, 40000)]

    def _verdicts(self, rows, live):
        if not live:
            T.PRICE_WINDOW.clear(); T.EXHAUSTED.clear(); T.FAILED_SCOPES.clear(); T.RAW_N.clear()
        today = [r for r in rows if r["snapshot_date"] == T.TODAY]
        return {g["vin"]: (g["likely"], g.get("exact"))
                for g in T.delisted({self.TID}, rows, today, T.build_history(rows))}

    def test_the_rebuild_publishes_what_the_run_published(self):
        rows = self._rows()
        self._run(60000); T.save_fetch_log(T.fetch_log_row())     # run 1 reached the car
        self._run(50000)                                          # run 2 did not
        live = self._verdicts(rows, live=True)
        T.save_fetch_log(T.fetch_log_row())
        rebuilt = self._verdicts(rows, live=False)
        self.assertEqual(live["V" * 17], ("out of window", True),
                         "the precondition: the run that wrote the rows knows "
                         "this car sat above its own cut-off")
        self.assertEqual(rebuilt, live,
                         "and every later read of the same file says the same "
                         "thing — a departure the run called uncertain was "
                         "published as confirmed, with an exit price")

    def test_the_reach_is_the_last_runs_and_the_spend_is_the_days(self):
        self._run(60000, raw=40); T.save_fetch_log(T.fetch_log_row())
        self._run(50000, raw=40); T.save_fetch_log(T.fetch_log_row())
        day = json.loads(T.FETCH_LOG.read_text())[T.TODAY][self.TID]
        self.assertEqual({k: v["window"] for k, v in day.items()},
                         {"States": 50000, "National": 50000},
                         "the window describes the rows that survived")
        self.assertEqual({k: v["raw"] for k, v in day.items()},
                         {"States": 80, "National": 80},
                         "…and the raw count is what the day really spent, which "
                         "no row has to survive for")

    def test_an_exhausted_first_run_does_not_vouch_for_a_narrower_second(self):
        """The same defect on the other field: "this query saw the whole
        market" made every absence a confirmed departure."""
        self._run(60000, exhausted=True); T.save_fetch_log(T.fetch_log_row())
        self._run(50000, exhausted=False); T.save_fetch_log(T.fetch_log_row())
        day = json.loads(T.FETCH_LOG.read_text())[T.TODAY][self.TID]
        self.assertEqual([v["exhausted"] for v in day.values()], [False, False])

    def test_a_target_the_second_run_never_asked_keeps_no_entry(self):
        """Its rows went with the day, so an entry for it is a reach with
        nothing behind it — the same defect, reached another way. Silence puts
        delisted() back on what the rows can prove."""
        self._run(60000); T.save_fetch_log(T.fetch_log_row())
        T.PRICE_WINDOW.clear(); T.EXHAUSTED.clear(); T.FAILED_SCOPES.clear(); T.RAW_N.clear()
        key = ("bmw-i5-m60", "National")
        T.PRICE_WINDOW[key] = 70000; T.RAW_N[key] = 20
        T.save_fetch_log(T.fetch_log_row())
        day = json.loads(T.FETCH_LOG.read_text())[T.TODAY]
        self.assertEqual(sorted(day), ["bmw-i5-m60"],
                         f"the second run asked only the M60; the log holds {sorted(day)}")


class TestTheCommittedRecordIsThisCodesOwn(unittest.TestCase):
    """REPORT.md and docs/data.json are the two artefacts everything else reads.

    The browser suite compares the committed record against the rendered page
    clause by clause — the typical-days sentence, the cut counts, the arrivals,
    the trim headings, the margin on each car — and every one of those checks is
    only as good as the record being the one this code produces. A change to
    Tracking.py alone does not regenerate it, so a drift introduced there is
    invisible in CI: the page moves and the file it is compared against does
    not, and the pair agrees on the old answer.

    The rebuild is pinned to the day the record says it was BUILT, which is its
    own first line — that is the only input to build_outputs() that is not in
    the repository, and pinning it is what makes the comparison exact rather
    than approximate. On a live run that day is the data day; on a rebuild it is
    the day the rebuild ran, and either way the file names it.
    """

    def test_rebuilding_the_record_on_the_day_it_names_reproduces_it(self):
        import json as _json
        report = Path("REPORT.md")
        if not report.exists() or not T.SNAPSHOTS.exists():
            self.skipTest("no committed record beside this checkout")
        head = report.read_text().splitlines()[0]
        m = re.match(r"# \S+ — (\d{4}-\d{2}-\d{2})$", head)
        self.assertTrue(m, f"the record's first line names the day it was built: {head!r}")
        day = m.group(1)
        keep = (T.TODAY, T.TODAY_ORD, dict(T.PRICE_WINDOW), set(T.EXHAUSTED),
                set(T.FAILED_SCOPES))
        T.TODAY, T.TODAY_ORD = day, date.fromisoformat(day).toordinal()
        # A rebuild holds no live fetch state; the committed fetch log is what
        # it reads, exactly as tools/rebuild_outputs.py does.
        T.PRICE_WINDOW.clear(); T.EXHAUSTED.clear(); T.FAILED_SCOPES.clear()
        try:
            rows = T.load_history()
            days = sorted({r["snapshot_date"] for r in rows})
            latest = [r for r in rows if r["snapshot_date"] == days[-1]]
            built, site, _ = T.build_outputs(latest, rows, T.build_history(rows))
        finally:
            (T.TODAY, T.TODAY_ORD) = keep[0], keep[1]
            T.PRICE_WINDOW.clear(); T.PRICE_WINDOW.update(keep[2])
            T.EXHAUSTED.clear(); T.EXHAUSTED.update(keep[3])
            T.FAILED_SCOPES.clear(); T.FAILED_SCOPES.update(keep[4])
        if built != report.read_text():
            import difflib
            diff = list(difflib.unified_diff(report.read_text().splitlines(),
                                             built.splitlines(),
                                             "committed", "rebuilt", n=1, lineterm=""))
            self.fail("REPORT.md is not what this code builds from the committed "
                      "snapshot on " + day + " — regenerate it "
                      "(AUTODEV_API_KEY=offline python3 tools/rebuild_outputs.py):\n"
                      + "\n".join(diff[:40]))
        # Through the writer, not through a loaded object: a tuple and a list
        # are the same JSON and a different Python value, and it is the FILE the
        # dashboard fetches.
        self.assertEqual(_json.dumps(site, indent=1),
                         (T.DOCS / "data.json").read_text(),
                         "…and docs/data.json with it, which is the file the "
                         "dashboard and every browser check read")


class TestAPercentReadsTheSameOnBothSurfaces(unittest.TestCase):
    """Python rounds half to EVEN; JavaScript's Math.round rounds half UP.

    Every percentage the record prints is recomputed on the page, so a margin
    landing on an exact half-percent was published as two different numbers for
    the same car on the same day. The field one place to the left already
    carries this fix and names it — "floor(x + .5), not round(): … the page
    recomputes this figure — two picks read '$1,936 less' here and '$1,937
    less' there" — and the percentage in the same f-string was not swept.

    The right-hand column is what `node -e "Math.round(v * 100)"` really prints,
    measured rather than derived, so this test is not the fix restated: the last
    assertion shows Python's own format disagrees with it on five of the seven.
    """

    # value: what the dashboard renders for it
    JS = {0.005: "1%", 0.015: "2%", 0.025: "3%", 0.045: "5%",
          0.105: "11%", 0.125: "13%", 0.135: "14%"}

    def test_the_record_rounds_the_way_the_page_does(self):
        for v, want in self.JS.items():
            self.assertEqual(T.pct(v), want, f"{v!r} should read {want}")

    def test_and_the_old_rule_really_did_disagree(self):
        """Without this the table above could be Python's own answer written
        down, and the test would pass on the defect it is named for."""
        differ = [v for v, want in self.JS.items() if f"{v:.0%}" != want]
        self.assertEqual(len(differ), 5,
                         f"only {differ} differ between the two rules")

    def test_the_stand_floor_no_longer_prints_a_claim_with_no_content(self):
        """score_picks() admits a stand at a margin of exactly half a percent,
        its docstring calling that "the smallest margin that rounds to a digit,
        ON BOTH SIDES OF THE SHEET". The record printed that car as "0% under
        typical" — the empty claim the floor exists to forbid — while the page
        called it 1%."""
        self.assertEqual(T.pct(0.005), "1%")

    def test_every_share_the_record_prints_goes_through_it(self):
        """Not only the picks: the cut share and the staleness share are
        recomputed on the page too, with Math.round."""
        import re
        src = Path("Tracking.py").read_text()
        left = re.findall(r"\{[^{}]*:\.0%\}", src)
        self.assertEqual(left, [],
                         f"these still round the other way: {left}")


class TestFlagsSayTheCarsHistory(unittest.TestCase):
    """Owners and accidents, which no test in this repo could fail on.

    flags() builds the `_1-owner · no accidents · ex-lease_` line under every
    car in REPORT.md and the `flags` array on every row of docs/data.json. The
    only class named for it fixed `owners: 1, accidents: 0` in its row factory
    and asserted nothing but the usage words, so both branches of each could be
    inverted with the whole suite and the whole browser run green — publishing
    300 rows reading "1-owner" over cars with two owners, and 293 reading "no
    accidents" over 74 that had one. Two of the four things a used-car buyer
    checks before calling a dealer, and this is the only place either is said.
    """

    @staticmethod
    def row(**kw):
        r = {"usage": "Personal Use", "owners": None, "accidents": None}
        r.update(kw)
        return r

    def test_one_owner_and_only_one(self):
        self.assertIn("1-owner", T.flags(self.row(owners=1)))
        self.assertNotIn("1-owner", T.flags(self.row(owners=2)))
        self.assertNotIn("1-owner", T.flags(self.row(owners=0)))
        self.assertNotIn("1-owner", T.flags(self.row(owners=None)))

    def test_more_than_one_owner_is_counted(self):
        self.assertIn("2 owners", T.flags(self.row(owners=2)))
        self.assertIn("5 owners", T.flags(self.row(owners=5)))

    def test_no_owner_line_at_all_when_the_sheet_does_not_say(self):
        """0 is what the API sends for "not reported", and the page reads it
        the same way — a fact the record must not invent."""
        for v in (None, 0, "", "n/a"):
            got = T.flags(self.row(owners=v))
            self.assertFalse([w for w in got if "owner" in w],
                             f"owners={v!r} produced {got}")

    def test_no_accidents_means_zero_and_nothing_else(self):
        self.assertIn("no accidents", T.flags(self.row(accidents=0)))
        self.assertNotIn("no accidents", T.flags(self.row(accidents=1)))
        self.assertNotIn("no accidents", T.flags(self.row(accidents=3)))

    def test_an_accident_is_counted_and_pluralised(self):
        self.assertIn("1 accident", T.flags(self.row(accidents=1)))
        self.assertNotIn("1 accidents", T.flags(self.row(accidents=1)))
        self.assertIn("3 accidents", T.flags(self.row(accidents=3)))

    def test_an_unknown_accident_history_says_nothing_either_way(self):
        """The complement is what the rule cannot support: a car the sheet
        carries no accident count for is neither clean nor crashed."""
        for v in (None, "", "n/a"):
            got = T.flags(self.row(accidents=v))
            self.assertFalse([w for w in got if "accident" in w],
                             f"accidents={v!r} produced {got}")

    def test_the_order_is_the_one_the_page_draws(self):
        """Two surfaces that order the same facts differently read as
        disagreeing — flagsCell() puts certification first, then usage, then
        owners, then accidents, then the lease."""
        self.assertEqual(
            T.flags({"usage": "Lease", "owners": 2, "accidents": 1, "cpo": "1"}),
            ["CPO", "2 owners", "1 accident", "ex-lease"])
        self.assertEqual(
            T.flags({"usage": "Rental Use", "owners": 1, "accidents": 0, "cpo": ""}),
            ["rental", "1-owner", "no accidents"],
            "and the usage word sits between the certification and the owners")


class TestFlagsNameARental(unittest.TestCase):
    """The report and the dashboard describe the same car.

    flagsCell() has marked rentals and fleet cars on the page since the filter
    was written; flags() — which builds the committed REPORT.md's history line
    and the export's `flags` — said nothing, so a car the buyer's own picks rule
    excludes read as clean in the one artefact that gets committed.
    """

    @staticmethod
    def row(usage, **kw):
        r = {"usage": usage, "owners": 1, "accidents": 0}
        r.update(kw)
        return r

    def test_a_rental_says_rental(self):
        self.assertIn("rental", T.flags(self.row("Rental Use")))

    def test_a_fleet_car_says_fleet(self):
        self.assertIn("fleet", T.flags(self.row("Corporate Fleet")))

    def test_multiple_use_says_multi_use(self):
        self.assertIn("multi-use", T.flags(self.row("Multiple Use")))

    def test_a_lease_is_not_a_rental(self):
        got = T.flags(self.row("Lease"))
        self.assertIn("ex-lease", got)
        self.assertFalse(any(w in got for w in ("rental", "fleet", "multi-use")))

    def test_a_personal_car_says_none_of_it(self):
        got = T.flags(self.row("Personal Use"))
        self.assertEqual([w for w in got if w in ("rental", "fleet", "multi-use")], [])

    def test_the_word_sits_where_the_page_puts_it(self):
        """Right after the certified chip, which is the slot flagsCell uses —
        two surfaces reading the same list must not order it differently."""
        got = T.flags(self.row("Rental Use", cpo="true"))
        self.assertEqual(got.index("rental"), got.index("CPO") + 1)


class TestAnArrivalNamesWhatItWasComparedAgainst(unittest.TestCase):
    """"10% under typical" is the same claim the spicy-pick block twenty-five
    lines below prints as "10% under typical for a 2026 BMW i7 eDrive50 …
    from 45 such cars". One denominator, present in one section of the record
    and absent in the next — and the arrivals block is where a reader looks
    first, because it is the only part of the report that is time-sensitive.

    On the day this was written the arrivals headline named a $106,425
    eleven-mile car as the day's best value. It is a demo or a loaner priced
    near sticker, not a used i7 anyone is choosing between, and the real
    candidate — a $55,096 2024 eDrive50 sitting 8% under twenty-six
    comparable cars — was the second line down and unlabelled."""

    def pick(self, **kw):
        p = {"pick_pct": 0.10, "pick_under": 11827, "pick_stand": "under",
             "pick_year": "2026", "model_label": "BMW i7", "pick_trim": "eDrive50",
             "pick_n": 45}
        p.update(kw)
        return p

    def car(self, **kw):
        x = {"price": 106425, "year": 2026, "miles": 11, "local": False, "ship": 1336,
             "city": "Buena Park", "state": "CA", "days_listed": 23, "vin": "V" * 17}
        x.update(kw)
        return x

    def test_the_arrival_tag_carries_its_cohort_and_its_size(self):
        got = T.fmt_new(self.car(), self.pick())
        self.assertIn("10% under typical for a 2026 BMW i7 eDrive50 "
                      "($11,827 less, from 45 such cars)", got)

    def test_a_cohort_that_fell_back_names_only_what_it_used(self):
        """score_picks stamps pick_trim "" on a year-basis pick and pick_year
        None on a model-basis one. Joining the truthy parts is what makes "for
        a BMW i7" the honest short form — an earlier draft pluralised the trim
        into "from 26 2024 eDrive50s" and rendered "from 45 s" on every pick
        that had fallen back."""
        self.assertIn("under typical for a 2026 BMW i7 (",
                      T.fmt_new(self.car(), self.pick(pick_trim="")))
        self.assertIn("under typical for a BMW i7 (",
                      T.fmt_new(self.car(), self.pick(pick_trim="", pick_year=None)))

    def test_a_pick_with_no_cohort_at_all_says_nothing_about_one(self):
        """Rather than "for a " with a hole in it."""
        got = T.fmt_new(self.car(), self.pick(pick_trim="", pick_year=None, model_label=None))
        self.assertIn("10% under typical ($11,827 less, from 45 such cars)", got)
        self.assertNotIn("for a", got)

    def test_a_cohort_of_unknown_size_drops_only_the_size(self):
        got = T.fmt_new(self.car(), self.pick(pick_n=None))
        self.assertIn("for a 2026 BMW i7 eDrive50 ($11,827 less)", got)
        self.assertNotIn("such car", got)

    def test_one_comparable_car_is_one_car(self):
        self.assertIn("from 1 such car)", T.fmt_new(self.car(), self.pick(pick_n=1)))

    # -- the day's headline -------------------------------------------------

    def event(self, **kw):
        e = {"x": self.car(), "label": "BMW i7 eDrive50", "pct": 0.10,
             "p": self.pick(), "shopping": 1}
        e.update(kw)
        return e

    def headline(self, *events):
        sec, _ = T.build_today({"cuts": [], "new": list(events), "gone": []}, T.TODAY)
        return next(l for l in sec if "new on the shopped models" in l)

    def test_the_headline_names_the_cohort_the_mileage_and_the_kind_of_car(self):
        line = self.headline(self.event())
        self.assertIn("best 10% under typical for a 2026 BMW i7 eDrive50 "
                      "($106,425, Buena Park, CA · 11 mi — delivery-mileage stock, "
                      "from 45 such cars)", line)

    def test_a_used_arrival_gets_its_mileage_and_no_label(self):
        """Only the positive claim. A car over the line is not called anything
        — "used" is the complement, and the complement is what the mileage
        rule cannot support for a car whose mileage is missing."""
        line = self.headline(self.event(x=self.car(miles=30190, price=55096)))
        self.assertIn("· 30,190 mi, from 45 such cars)", line)
        self.assertNotIn("delivery-mileage", line)
        self.assertNotIn("used", line)

    def test_an_arrival_with_no_mileage_is_called_neither(self):
        line = self.headline(self.event(x=self.car(miles=None)))
        self.assertIn("($106,425, Buena Park, CA, from 45 such cars)", line)
        self.assertNotIn("mi", line.split("under typical")[1])

    def test_a_hundred_miles_is_not_delivery_mileage(self):
        """The same boundary market_stats splits its two markets on."""
        self.assertNotIn("delivery-mileage", self.headline(self.event(x=self.car(miles=100))))
        self.assertIn("delivery-mileage", self.headline(self.event(x=self.car(miles=99))))

    def test_the_reach_clause_keeps_its_place(self):
        """It was shipped first and says something the cohort does not: how
        many of these arrived in the tracker's window rather than the market."""
        old = date.fromordinal(T.TODAY_ORD - T.REACH_DAYS).isoformat()
        line = self.headline(self.event(
            x=self.car(days_listed=30, first_seen=T.TODAY, listed_since=old)))
        self.assertIn("reach, not arrival", line)
        self.assertLess(line.index("reach, not arrival"), line.index("under typical"))


class TestACertificationIsIssuedBySomeone(unittest.TestCase):
    """Manufacturer certification is issued by the manufacturer's own stores,
    so a car flagged certified at a dealership named for another marque is a
    claim to confirm rather than count on. On this record 21 of 46 certified
    cars are exactly that — including the cheapest car in the certified watch,
    a $64,491 i5 at "niello acura", printed with a bare CPO directly under a
    note saying the 2.99% certified promo is the reason to watch these.

    The dashboard has flagged it since the promo strip was built, in these
    words. The committed record said nothing, which is the drift this repo
    keeps finding: a rule enforced on the surface someone is reading and not
    on the one that is kept."""

    TID = "bmw-i5-edrive40"

    def row(self, dealer, **kw):
        r = {"usage": "Personal Use", "owners": 1, "accidents": 0,
             "cpo": "true", "target": self.TID, "dealer": dealer}
        r.update(kw)
        return r

    def test_a_bmw_store_gets_the_bare_badge(self):
        self.assertIn("CPO", T.flags(self.row("irvine bmw")))
        self.assertNotIn("CPO (seller not named BMW)", T.flags(self.row("irvine bmw")))

    def test_another_marque_says_so(self):
        self.assertIn("CPO (seller not named BMW)", T.flags(self.row("niello acura")))

    def test_the_brand_must_be_a_whole_word(self):
        """The page tests /\\bbmw\\b/i. A dealership called "bmwofchicago" is
        not evidence of anything — and one called "bmw-of-chicago" is, because
        a hyphen is where that boundary falls."""
        self.assertIn("CPO (seller not named BMW)", T.flags(self.row("bmwofchicago")))
        self.assertIn("CPO", T.flags(self.row("bmw-of-chicago")))
        self.assertNotIn("CPO (seller not named BMW)", T.flags(self.row("bmw-of-chicago")))

    def test_a_seller_with_no_name_is_the_one_to_confirm(self):
        """Conservative direction: a certification nobody is named for is
        exactly the case the caveat exists for."""
        self.assertIn("CPO (seller not named BMW)", T.flags(self.row("")))

    def test_an_uncertified_car_wears_neither(self):
        got = T.flags(self.row("niello acura", cpo=""))
        self.assertEqual([w for w in got if w.startswith("CPO")], [])

    def test_the_caveat_keeps_the_page_ordering(self):
        """Still the first flag, still ahead of the rental word — the slot
        flagsCell() uses, so the two surfaces read the same way."""
        got = T.flags(self.row("niello acura", usage="Rental Use"))
        self.assertEqual(got[0], "CPO (seller not named BMW)")
        self.assertEqual(got[1], "rental")

    def test_the_record_carries_it_where_a_reader_meets_it(self):
        """End to end, on the committed record: no certified row may print a
        bare CPO when its own seller line names another marque."""
        rows = T.load_history()
        latest = max(r["snapshot_date"] for r in rows)
        report = T.build_outputs([r for r in rows if r["snapshot_date"] == latest],
                                 rows, T.build_history(rows))[0]
        lines = report.splitlines()
        bare = [i for i, l in enumerate(lines)
                if "· CPO ·" in l or l.rstrip("_").endswith("· CPO")]
        for i in bare:
            seller = " ".join(lines[i + 1:i + 3]).lower()
            self.assertIn("bmw", seller,
                          f"line {i + 1} wears a bare CPO over {seller[:60]!r}")


class TestDepartureEvidence(unittest.TestCase):
    """A departure counts as a car that left only where a query actually looked.

    exit_stats() and one_cohort() have always refused to price a departure from
    a target whose two windows cannot be told apart from each other; sale_stats()
    did not, so the market line published "listings ran at least ~Nd (N gone)"
    and the report published "N gone since the last fetch" from exactly the
    departures the same file declines to put a price on. All 55 i7 and all 38 i5
    "delisted" rows on the audited sheet came from two-window targets.

    The gate is NOT the target's shape, though. Since the fetch log exists, a
    two-window target's departure can be exact — the run wrote down what each
    query reached and whether it was exhaustive — and throwing that away would
    withhold a number the record can defend. So delisted() stamps each row with
    how its own label was reached, and this reads that.
    """

    @staticmethod
    def row(exact=None, **kw):
        g = {"likely": "delisted", "last_price": 40000, "listed_since": "2026-08-01",
             "last_seen": "2026-08-15", "first_seen": "2026-08-01", "series": []}
        if exact is not None:
            g["exact"] = exact
        g.update(kw)
        return g

    def test_a_guessed_departure_is_not_counted(self):
        self.assertFalse(T.departure_is_evidence(self.row(exact=False)))
        st = T.sale_stats([self.row(exact=False) for _ in range(9)])
        # Both halves: the exit PRICE and the days-to-sale SPAN come from two
        # separate loops, and the market line publishes them in one sentence
        # ("listings ran at least ~6d (29 gone)"), so a gate on one and not the
        # other would leave half the claim standing on guesses.
        self.assertEqual(st["n_exits"], 0)
        self.assertEqual(st["n_sold"], 0)
        self.assertIsNone(st["median_days_to_sale"])

    def test_a_departure_a_query_confirmed_is_counted(self):
        self.assertTrue(T.departure_is_evidence(self.row(exact=True)))
        st = T.sale_stats([self.row(exact=True) for _ in range(9)])
        self.assertEqual(st["n_exits"], 9)
        self.assertEqual(st["n_sold"], 9)

    def test_a_two_window_target_can_still_be_exact(self):
        """The reason this reads the row and not the target. A departure the
        fetch log answered for is evidence whatever shape the target has, and
        gating on the shape would discard it."""
        two = next(t for t in T.TARGETS.values() if not T.window_reconstructable(t))
        self.assertTrue(T.departure_is_evidence(self.row(exact=True, trim_id=two["id"])),
                        "the log said a query looked; the target's shape does not overrule that")
        self.assertFalse(T.departure_is_evidence(self.row(exact=False, trim_id=two["id"])))

    def test_an_older_sheet_falls_back_to_the_targets_shape(self):
        """Rows written before delisted() carried its own provenance."""
        one = next(t for t in T.TARGETS.values() if T.window_reconstructable(t))
        two = next(t for t in T.TARGETS.values() if not T.window_reconstructable(t))
        self.assertTrue(T.departure_is_evidence(self.row(trim_id=one["id"])))
        self.assertFalse(T.departure_is_evidence(self.row(trim_id=two["id"])))

    def test_an_unknown_target_fails_closed(self):
        """The gate exists to keep a number off the page, and "nothing on
        record says which query found this car" is not a reason to publish."""
        self.assertFalse(T.departure_is_evidence(self.row(trim_id="a-target-we-stopped-watching")))
        self.assertFalse(T.departure_is_evidence(self.row()))
        # …and asking is not allowed to raise, either: a partial target used to
        # take sorts_pages() through t["depth"] and KeyError a whole rebuild
        # over a car that left in July.
        self.assertEqual(T.sorts_pages({}), ([], 1))


class TestCutTag(unittest.TestCase):
    """A cut that was undone is not a discount.

    `cuts` counts the downward steps and `delta` is last minus first, so a
    listing cut and then restored has cuts >= 1 and delta >= 0. The report
    printed "down 1x ($0)" for exactly that — five lines of one day's report,
    one of them for a car back at its exact opening price — and the same
    sentence would have printed a POSITIVE delta as if it were a cut.

    The aggregate definition is deliberately untouched: "was cut at some point"
    is a true thing to count, and a second real drop landing above an earlier
    low is still a price change the buyer wants to see. Only the wording moves.
    """

    def test_a_real_cut_still_reads_as_one(self):
        self.assertEqual(T.cut_tag(2, -1500), "down 2x ($1,500 down)".replace(
            "($1,500 down)", f"({T.money(-1500)})"))

    def test_a_cut_that_was_undone_says_so(self):
        self.assertEqual(T.cut_tag(1, 0), "cut 1x, then back up")
        self.assertNotIn("$0", T.cut_tag(1, 0))

    def test_a_price_now_above_where_it_started_says_that(self):
        got = T.cut_tag(2, 849)
        self.assertIn("above first seen", got)
        self.assertNotIn("down", got)

    def test_the_report_tag_uses_it(self):
        """The two real series from the audited sheet, through the function the
        report actually calls."""
        r = {k: "" for k in T.FIELDS}
        r.update({"target": "bmw-i5-edrive40", "vin": "V" * 17, "price": 39998,
                  "year": "2024", "miles": 20000, "state": "IL", "city": "Chicago"})
        back_up = T.fmt_row(r, {"cuts": 1, "delta": 0, "days_tracked": 5,
                                "series": [["2026-08-01", 39998]]}, T.TODAY)
        self.assertIn("cut 1x, then back up", back_up)
        self.assertNotIn("down 1x", back_up)
        above = T.fmt_row(r, {"cuts": 2, "delta": 849, "days_tracked": 7,
                              "series": [["2026-08-01", 42995]]}, T.TODAY)
        self.assertIn("above first seen", above)


class TestFetchDaysExport(unittest.TestCase):
    """The days each target actually fetched, which nothing else can be asked.

    The dashboard rebuilds a day row from the cars themselves whenever a filter
    is on, and to do that it has to know when each target last ran — a car is
    carried forward only to its own target's latest fetch. It used to work that
    out from the cars' sightings, which is wrong in one specific and live case:
    a car carries ONE trim_id, so a target whose every current car is filed
    under a sibling target leaves no sightings of its own at all. The i5's
    nationwide CPO watch is exactly that. It fetched on 2026-09-03 and returned
    two certified cars the export files under eDrive40; the page concluded the
    watch had not run since 09-01 and carried two cars that DEPARTED on 09-01
    forward into every day after. Its rebuilt model row read 135 against a
    precomputed 133.
    """

    @staticmethod
    def row(tid, vin, day, price, trim="M60"):
        r = {k: "" for k in T.FIELDS}
        r.update({"target": tid, "vin": vin, "snapshot_date": day, "price": price,
                  "year": "2024", "trim": trim, "miles": 20000,
                  "state": "IL", "city": "Chicago"})
        return r

    def test_a_targets_fetch_days_are_its_own_snapshot_dates(self):
        fast, slow = "bmw-i5-edrive40", "bmw-i5-m60"
        d1, d2, d3 = "2026-08-01", "2026-08-02", "2026-08-03"
        rows = [self.row(fast, "F" * 17, d, 40000, "eDrive40") for d in (d1, d2, d3)]
        rows += [self.row(slow, "S" * 17, d, 60000) for d in (d1, d3)]
        _, site, _ = T.build_outputs(rows, rows, T.build_history(rows))
        fd = site["brands"]["bmw"]["models"]["i5"]["fetch_days"]
        self.assertEqual(fd[fast], [d1, d2, d3])
        self.assertEqual(fd[slow], [d1, d3],
                         "a target that did not run on a day must not claim it")

    def test_a_target_whose_cars_are_filed_elsewhere_still_reports_its_days(self):
        """The whole reason this is exported rather than inferred.

        One VIN, matched by two targets. pick_display_rows keeps one copy per
        VIN so the car appears in the export under a single trim_id — and the
        OTHER target's fetch days would vanish with it if they were read off
        the cars, which is what the page did.
        """
        watch, ordinary = "bmw-i5-cpo", "bmw-i5-edrive40"
        d1, d2 = "2026-08-01", "2026-08-02"
        both = "B" * 17
        rows = [self.row(ordinary, both, d, 40000, "eDrive40") for d in (d1, d2)]
        rows += [self.row(watch, both, d, 40000, "eDrive40") for d in (d1, d2)]
        rows += [self.row(ordinary, "O" * 17, d, 41000, "eDrive40") for d in (d1, d2)]
        _, site, _ = T.build_outputs(rows, rows, T.build_history(rows))
        m = site["brands"]["bmw"]["models"]["i5"]
        filed = {x["vin"]: x["trim_id"] for x in m["listings"]}
        self.assertEqual(len({filed[both]}), 1,
                         "the shared VIN is filed under exactly one target")
        self.assertEqual(m["fetch_days"][watch], [d1, d2],
                         "the watch ran on both days and the export must say so, "
                         "however its cars were filed")


class TestLocalHistoryExport(unittest.TestCase):
    """Whether a car was DRIVABLE on a given day, for the few that moved.

    in_scope() reads the state field, and a state field is not a constant: a
    listing can move between a dealer group's lots or be re-listed by another
    store. Nine VINs in the real record have changed state and three have
    crossed the buyer's border doing it. daily_stats reads the row as it was on
    the day; the page held only today's flag and so counted an i5 that was in
    Indiana on 2026-09-01 as beyond the border on that day, one drivable car
    short. The two series are one definition in two languages, so the page gets
    the same fact — at the change points only, for the cars that have any.
    """

    @staticmethod
    def row(vin, day, state, price=40000):
        r = {k: "" for k in T.FIELDS}
        r.update({"target": "bmw-i5-edrive40", "vin": vin, "snapshot_date": day,
                  "price": price, "year": "2024", "trim": "eDrive40",
                  "miles": 20000, "state": state, "city": "Somewhere"})
        return r

    def test_a_car_that_never_moved_carries_nothing(self):
        rows = [self.row("S" * 17, d, "IL") for d in ("2026-08-01", "2026-08-02")]
        _, site, _ = T.build_outputs(rows, rows, T.build_history(rows))
        got = site["brands"]["bmw"]["models"]["i5"]["listings"][0]
        self.assertNotIn("local_hist", got,
                         "999 cars in 1000 must not pay bytes for this")

    def test_a_car_that_crossed_the_border_carries_the_change(self):
        vin = "M" * 17
        rows = [self.row(vin, "2026-08-01", "IL"), self.row(vin, "2026-08-02", "IL"),
                self.row(vin, "2026-08-03", "MO"), self.row(vin, "2026-08-04", "MO")]
        _, site, _ = T.build_outputs(rows, rows, T.build_history(rows))
        got = site["brands"]["bmw"]["models"]["i5"]["listings"][0]
        self.assertEqual(got["local_hist"], [["2026-08-01", 1], ["2026-08-03", 0]],
                         "the change points, not a value a day")
        self.assertFalse(got["local"], "and today's flag still says where it is now")

    def test_a_move_that_does_not_cross_the_border_says_nothing(self):
        """IL to OH is a move; both are states this buyer drives to, so the
        answer the page asks for — drivable? — never changed."""
        vin = "N" * 17
        rows = [self.row(vin, "2026-08-01", "IL"), self.row(vin, "2026-08-02", "OH")]
        _, site, _ = T.build_outputs(rows, rows, T.build_history(rows))
        got = site["brands"]["bmw"]["models"]["i5"]["listings"][0]
        self.assertNotIn("local_hist", got)

    def test_the_day_row_counts_the_car_where_it_was(self):
        """The bug this exists for, end to end: daily_stats already got this
        right, and the export is what lets the page agree with it."""
        vin = "M" * 17
        rows = [self.row(vin, "2026-08-01", "IL"), self.row(vin, "2026-08-02", "MO")]
        rows += [self.row("K" * 17, d, "MO") for d in ("2026-08-01", "2026-08-02")]
        by_day = {x["date"]: x for x in T.daily_stats(rows)}
        self.assertEqual(by_day["2026-08-01"]["n_local"], 1)
        self.assertEqual(by_day["2026-08-02"]["n_local"], 0)


class TestReportFooter(unittest.TestCase):
    def test_a_rebuild_does_not_overwrite_the_days_cost_with_zero(self):
        """CALLS is this PROCESS's counter and an offline rebuild makes none, so
        the footer printed "0 API calls today" over a day that had really spent
        24 — and every dispatch rebuilds, so that was the committed record."""
        rows = [dict({k: "" for k in T.FIELDS},
                     **{"target": "bmw-i5-edrive40", "vin": "V" * 17,
                        "snapshot_date": T.TODAY, "price": 40000, "year": "2024",
                        "miles": 20000, "state": "IL", "city": "Chicago"})]
        was = T.CALLS
        try:
            T.CALLS = 0
            report, _, _ = T.build_outputs(rows, rows, T.build_history(rows))
            self.assertNotIn("0 API calls today", report)
            self.assertIn("rebuilt from the snapshot on disk", report)
            T.CALLS = 24
            report, _, _ = T.build_outputs(rows, rows, T.build_history(rows))
            self.assertIn("24 API calls today", report)
        finally:
            T.CALLS = was


class TestIndexDate(unittest.TestCase):
    """An API index date is not a listing date.

    listed_since is the API's createdAt — when the RECORD was made — and a bulk
    load stamps tens of thousands of cars with one instant. On this sheet that
    is 2026-08-09: 106 of 2026-09-01's 321 rows carry it, across 8 targets, 25
    states and 85 dealers, while 2026-08-08 carries one row and 2026-08-10 none.

    Published, it made median_days_listed come out at exactly (snapshot date -
    2026-08-09) for six of seven models, incrementing by one every day — a
    constant wearing a market's clothes — and made every "sits longer than N%
    of the model" note a statement about the loader.
    """

    def setUp(self):
        self._was = set(T.INDEX_DATES)

    def tearDown(self):
        T.INDEX_DATES.clear()
        T.INDEX_DATES.update(self._was)

    @staticmethod
    def _rows(pairs, snap="2026-09-01"):
        return [{"target": "t", "vin": v, "snapshot_date": snap, "listed_since": d}
                for v, d in pairs]

    def test_a_bulk_load_is_recognised_by_its_shape_not_its_date(self):
        rows = self._rows([(f"BULK{i}", "2026-08-09") for i in range(40)]
                          + [("A", "2026-08-07"), ("B", "2026-08-08"),
                             ("C", "2026-08-10"), ("D", "2026-08-11")])
        self.assertEqual(T.find_index_dates(rows), {"2026-08-09"})

    def test_a_busy_day_that_looks_like_a_market_is_left_alone(self):
        """Ten times its neighbours, not merely more than them: a genuinely
        busy Monday must survive, or the rule quietly deletes real history."""
        rows = self._rows([(f"M{i}", "2026-08-09") for i in range(40)]
                          + [(f"N{i}", "2026-08-08") for i in range(9)]
                          + [(f"O{i}", "2026-08-10") for i in range(9)])
        self.assertEqual(T.find_index_dates(rows), set())

    def test_a_car_seen_every_day_votes_once(self):
        """Rows are per car per day. Counted raw, one long-lived car becomes a
        crowd: 25 sightings of a single VIN clears both the floor and the
        neighbour ratio and condemns its perfectly ordinary listing date,
        taking the days-on-market of every car sharing it with it."""
        rows = [{"target": "t", "vin": "SAME", "snapshot_date": f"2026-09-{i:02d}",
                 "listed_since": "2026-08-09"} for i in range(1, 26)]
        rows += [{"target": "t", "vin": "OTHER", "snapshot_date": "2026-09-01",
                  "listed_since": "2026-08-08"}]
        self.assertEqual(T.find_index_dates(rows), set(),
                         "one car is one car, however many days it was seen on")

    def test_a_real_build_withholds_the_loaded_cars_days_on_market(self):
        """End to end, through build_outputs: the set is populated from the
        history the run is publishing, not left to whatever a caller happened
        to put in it."""
        tid = "bmw-i5-m60"
        def row(vin, day, since, price=45000):
            r = {k: "" for k in T.FIELDS}
            r.update({"target": tid, "vin": vin, "snapshot_date": day,
                      "price": price, "year": "2024", "trim": "M60",
                      "miles": 20000, "state": "IL", "city": "Chicago",
                      "listed_since": since})
            return r
        day = T.TODAY
        rows = [row(f"BULK{i:02d}", day, "2026-06-01") for i in range(30)]
        rows += [row("REAL", day, "2026-06-20")]
        T.INDEX_DATES.clear()
        _, site, _ = T.build_outputs(rows, rows, T.build_history(rows))
        self.assertIn("2026-06-01", T.INDEX_DATES)
        got = {x["vin"]: x for x in site["brands"]["bmw"]["models"]["i5"]["listings"]}
        self.assertIsNone(got["BULK00"]["days_listed"],
                          "a car stamped with the load date has no measurable age")
        self.assertEqual(got["BULK00"]["listed_since"], "",
                         "and the load date must not ship as a listing date")
        self.assertIsNotNone(got["REAL"]["days_listed"])
        self.assertEqual(got["REAL"]["listed_since"], "2026-06-20")

    def test_days_on_market_is_withheld_for_an_index_date(self):
        T.INDEX_DATES.clear()
        T.INDEX_DATES.add("2026-08-09")
        self.assertIsNone(T.days_listed({"listed_since": "2026-08-09"}))
        real = T.days_listed({"listed_since": "2026-08-20"})
        self.assertIsNotNone(real, "a real listing date still measures")

    def test_the_median_and_the_percentile_skip_them(self):
        """market_stats must not average a withheld number in as a zero, and
        stale_pct must not rank against it."""
        listings = ([{"days_listed": None, "days_tracked": 3} for _ in range(8)]
                    + [{"days_listed": d, "days_tracked": 3} for d in (4, 10, 40)])
        st = T.market_stats(listings)
        self.assertEqual(st["median_days_listed"], 10,
                         "the three cars with a real date are the whole sample")
        self.assertTrue(all(x["stale_pct"] is None
                            for x in listings if x["days_listed"] is None))

    def test_a_span_needs_a_real_listing_date_not_our_own_first_sighting(self):
        """first_seen used to stand in for a missing listed_since, which
        measured how long the TRACKER had watched: on a ten-day-old record no
        span could exceed ten days, so the published 'listings ran at least
        ~Nd' was a fact about this repo's start date."""
        # exact=True: delisted() writes that down when a query actually
        # looked and did not find the car. This test is about the SPAN, so the
        # departures are given the provenance that lets them be counted at all.
        gone = [{"likely": "delisted", "last_price": 40000, "series": [],
                 "exact": True,
                 "listed_since": "", "first_seen": "2026-08-30",
                 "last_seen": "2026-09-01"} for _ in range(6)]
        self.assertEqual(T.sale_stats(gone)["n_sold"], 0)
        self.assertIsNone(T.sale_stats(gone)["median_days_to_sale"])
        # the exits themselves are still counted — a price needs no listing date
        self.assertEqual(T.sale_stats(gone)["n_exits"], 6)
        dated = [{**g, "listed_since": "2026-08-01"} for g in gone]
        self.assertEqual(T.sale_stats(dated)["n_sold"], 6)
        self.assertEqual(T.sale_stats(dated)["median_days_to_sale"], 31)


class TestMarketStats(unittest.TestCase):
    @staticmethod
    def entry(days_listed=None, days_tracked=1, series=None):
        return {"days_listed": days_listed, "days_tracked": days_tracked,
                "cuts": sum(1 for (_, a), (_, b) in
                            zip(series or [], (series or [])[1:]) if b < a),
                "series": series or []}

    def test_medians_cut_share_and_staleness(self):
        pool = [self.entry(days_listed=5, days_tracked=3,
                           series=[("d1", 50000), ("d2", 50000), ("d3", 50000)]),
                self.entry(days_listed=20, days_tracked=3,
                           series=[("d1", 48000), ("d2", 47000), ("d3", 46500)]),
                self.entry(days_listed=60, days_tracked=3,
                           series=[("d1", 45000), ("d2", 44000), ("d3", 44000)])]
        stats = T.market_stats(pool)
        self.assertEqual(stats["median_days_listed"], 20)
        self.assertAlmostEqual(stats["cut_share"], 2 / 3, places=2)
        self.assertEqual(stats["median_cut"], 1000)   # drops: 1000, 500, 1000
        self.assertEqual(pool[2]["stale_pct"], round(2 / 3, 2),
                         "the 60-day car has outlasted two of three")
        self.assertEqual(pool[0]["stale_pct"], 0.0)

    def test_empty_and_unknown_inputs_do_not_crash(self):
        stats = T.market_stats([self.entry()])
        self.assertIsNone(stats["median_days_listed"])
        self.assertIsNone(stats["cut_share"])
        self.assertIsNone(stats["median_cut"])
        self.assertEqual(T.market_stats([]), {"median_days_listed": None,
                                              "days_split": None,
                                              "dated": 0,
                                              "n": 0,
                                              "tracked_2d": 0,
                                              "cut_share": None,
                                              "median_cut": None,
                                              "net_down": 0,
                                              "restored": 0,
                                              "median_net_drop": None,
                                              "two_priced": 0,
                                              "n_cuts": 0})

    def test_days_to_sale_counts_only_real_delistings(self):
        """…and only cars with a real listing date to count from.

        The second row here used to contribute "4d, by first sighting". That
        was the tracker measuring itself: first_seen is the day THIS repo
        first saw the car, so the span it yields is bounded by how long the
        record has existed, and on a ten-day-old ledger every such span is
        under ten days whatever the market did. Blanking listed_since — which
        is now also what an API index load gets, see find_index_dates() —
        leaves a car with no measurable span, and no span is the honest
        answer.
        """
        gone = [
            {"likely": "delisted", "exact": True, "listed_since": "2026-08-10",
             "last_seen": "2026-08-20", "first_seen": "2026-08-15"},   # 10d, by listing date
            {"likely": "delisted", "exact": True, "listed_since": "",
             "last_seen": "2026-08-20", "first_seen": "2026-08-16"},   # no listing date: no span
            {"likely": "out of window", "exact": True, "listed_since": "2026-07-01",
             "last_seen": "2026-08-20", "first_seen": "2026-07-02"},   # not a sale
            {"likely": "delisted", "exact": True, "listed_since": "garbage",
             "last_seen": "2026-08-20", "first_seen": None},           # unparseable: skipped
        ]
        stats = T.sale_stats(gone)
        self.assertEqual(stats["n_sold"], 1)
        self.assertEqual(stats["median_days_to_sale"], 10)

    def test_market_line_reads_like_a_sentence(self):
        line = T.market_line({"median_days_listed": 34, "tracked_2d": 40,
                              "cut_share": 0.41, "median_cut": 1050, "n_cuts": 46})
        self.assertIn("typical car 34d on market", line)
        self.assertIn("41% of 40 cut while tracked, median $1,050 of 46 cuts", line)
        # …and a sheet written before the step count existed still reads: the
        # suffix rides on the key, never on a guess.
        self.assertIn("median $1,050", T.market_line({"median_days_listed": 34, "tracked_2d": 40,
                                                      "cut_share": 0.41, "median_cut": 1050}))
        self.assertNotIn(" of ", T.market_line({"tracked_2d": 40, "cut_share": 0.41,
                                                "median_cut": 1050}).split("median $1,050")[1])
        self.assertEqual(T.market_line({"median_days_listed": None,
                                        "tracked_2d": 2, "cut_share": 0.5,
                                        "median_cut": None}), "",
                         "thin data must not fake a market read")


# --------------------------------------------------------------------------
# Today: one event engine feeds the report lead and the email subject.
# --------------------------------------------------------------------------
class TestToday(unittest.TestCase):
    @staticmethod
    def cut(amount, price, local=False, shopping=True, vin="V1", label="BMW i5 eDrive40"):
        return {"amount": amount, "shopping": shopping, "label": label,
                "x": {"vin": vin, "price": price, "local": local,
                      "city": "Plano", "state": "TX"}}

    def test_quiet_day_has_an_honest_subject_and_no_section(self):
        sec, subject = T.build_today({"cuts": [], "new": [], "gone": []}, T.TODAY)
        self.assertEqual(sec, [])
        self.assertIn("quiet day", subject)

    def test_the_biggest_relevant_cut_leads_the_subject(self):
        sec, subject = T.build_today({
            "cuts": [self.cut(400, 45000, local=True),
                     self.cut(1200, 46000, local=False, shopping=False,
                              vin="V2", label="Kia EV6")],
            "new": [], "gone": []}, T.TODAY)
        self.assertIn("▼$400 cut on drivable BMW i5 eDrive40", subject,
                      "a shopping-model drivable cut outranks a bigger rival cut")
        self.assertIn("## Today", sec[0])

    def test_shortlist_alerts_outrank_everything(self):
        old = dict(T.SHORTLIST)
        T.SHORTLIST.clear()
        T.SHORTLIST.update({"V9": ""})
        try:
            sec, subject = T.build_today({
                "cuts": [self.cut(2500, 40000)],
                "new": [],
                "gone": [{"vin": "V9", "label": "BMW i7", "last_seen": "2026-08-25",
                          "last_price": 62000, "shopping": True}]}, T.TODAY)
            self.assertTrue(subject.startswith(f"{T.APP} — shortlist car GONE"))
            self.assertIn("**Shortlist: GONE**", "\n".join(sec))
        finally:
            T.SHORTLIST.clear()
            T.SHORTLIST.update(old)

    def test_new_and_gone_counts_reach_the_subject(self):
        sec, subject = T.build_today({
            "cuts": [],
            "new": [{"x": {"vin": "N1", "price": 44000, "city": "Plano",
                           "state": "TX", "local": False},
                     "label": "BMW i5", "pct": 0.06, "shopping": True}],
            "gone": [{"vin": "G1", "label": "BMW i5", "last_seen": "2026-08-25",
                      "last_price": 47000, "shopping": True}]}, T.TODAY)
        self.assertIn("1 new", subject)
        self.assertIn("1 gone", subject)
        self.assertIn("best 6% under typical", "\n".join(sec))


# --------------------------------------------------------------------------
# The shortlist: specific cars watched by VIN, first in the report.
# --------------------------------------------------------------------------
class TestShortlist(unittest.TestCase):
    def test_entries_parse_both_shapes(self):
        sl = T._parse_shortlist(["wby123", {"vin": " wba999 ", "note": "called dealer"},
                                 "", None])
        self.assertEqual(list(sl), ["WBY123", "WBA999"])
        self.assertEqual(sl["WBA999"], "called dealer")

    def test_a_departure_is_missing_at_a_fetch_not_missing_today(self):
        """"missing today" is a claim about a fetch, and the fetch is the car's
        own model's — which on a three-day cadence is not today even on a live
        run, and on any build without a fetch is not today for anything."""
        yesterday = date.fromordinal(T.TODAY_ORD - 1).isoformat()
        row = {"vin": "GONE2", "likely": "out of window", "last_seen": "2026-08-25",
               "last_price": 47000, "trim_label": "eDrive40", "city": "", "state": "",
               "url": ""}
        was = dict(T.SHORTLIST)
        T.SHORTLIST.clear(); T.SHORTLIST.update({"GONE2": ""})
        try:
            stale = "\n".join(T.shortlist_section({}, {"GONE2": (row, yesterday)},
                                                  {}, yesterday))
            live = "\n".join(T.shortlist_section({}, {"GONE2": (row, T.TODAY)},
                                                 {}, T.TODAY))
        finally:
            T.SHORTLIST.clear(); T.SHORTLIST.update(was)
        self.assertIn(f"missing on {yesterday} — beyond that fetch's cut-off", stale)
        self.assertNotIn("today", stale)
        self.assertIn("missing today — beyond that fetch's cut-off", live,
                      "and on the day it really was fetched, the word is today")

    def test_section_reports_live_gone_and_unseen(self):
        old = dict(T.SHORTLIST)
        T.SHORTLIST.clear()
        T.SHORTLIST.update({"LIVE1": "my favourite", "GONE1": "", "NOPE1": ""})
        try:
            live = {"LIVE1": ({"vin": "LIVE1", "price": 42000, "year": 2024,
                               "miles": 12000, "local": True, "ship": 0,
                               "city": "Madison", "state": "WI", "series": [],
                               "cuts": 0, "delta": 0, "days_listed": 12,
                               "flags": [], "url": "https://x.example/1"},
                              "BMW i5")}
            # (row, the day its model was last fetched): "missing today" is a
            # claim about a fetch, and the fetch is that model's own.
            gone = {"GONE1": ({"vin": "GONE1", "likely": "delisted",
                               "last_seen": "2026-08-25", "last_price": 47000,
                               "trim_label": "eDrive40", "city": "", "state": "",
                               "url": ""}, T.TODAY)}
            sec = "\n".join(T.shortlist_section(live, gone, {}, T.TODAY))
            self.assertIn("$42,000", sec)
            self.assertIn("my favourite", sec)
            # not "sold or pulled": a listing ends four ways and three of them
            # are not a sale, which every other surface here already says
            self.assertIn("GONE — the listing ended", sec)
            self.assertNotIn("sold", sec)
            self.assertIn("location n/a", sec)
            self.assertIn("not seen yet by the tracker `NOPE1`", sec)
        finally:
            T.SHORTLIST.clear()
            T.SHORTLIST.update(old)

    def test_empty_shortlist_adds_nothing(self):
        old = dict(T.SHORTLIST)
        T.SHORTLIST.clear()
        try:
            self.assertEqual(T.shortlist_section({}, {}, {}, T.TODAY), [])
        finally:
            T.SHORTLIST.update(old)


# --------------------------------------------------------------------------
# Config resolution and small parsers.
# --------------------------------------------------------------------------
class TestConfig(unittest.TestCase):
    def test_a_parameter_resolves_through_all_four_layers(self):
        """trim ← model ← brand ← defaults, one live example of each.

        This used to read the years off the model, which is where the 2024+
        rule used to be written four times over. It is one line in `defaults`
        now, so the defaults layer is what the years demonstrate and the model
        layer needed an example that survives — the i5's own price floor,
        inherited by the trim that does not override it.
        """
        self.assertEqual(target("bmw-i5-m60")["min_price"], 30000,
                         "the trim's own override")
        self.assertEqual(target("bmw-i5-xdrive40")["min_price"], 20000,
                         "…and its sibling takes the model's")
        # The brand layer used to be demonstrated with national_only, which no
        # brand carries any more, and then with tesla-model-y, which is stood
        # down. Naming a model here is what keeps breaking it: the watchlist is
        # a thing the buyer edits. FOUND rather than named — any active target
        # whose cadence comes from its brand and is restated by neither the
        # model nor the trim — so the example survives the next trim.
        cfg = json.loads(Path("targets.json").read_text())
        dflt = cfg["defaults"].get("cadence", 1)
        example = None
        for t in T.TARGETS.values():
            b = cfg["watchlist"][t["brand"]]
            m = b["models"][t["model_key"]]
            trims = m.get("trims") or {}
            tr = trims.get(t["trim_key"], {}) if t["trim_key"] != "all" else {}
            if (b.get("cadence") not in (None, dflt) and m.get("cadence") is None
                    and tr.get("cadence") is None):
                example = (t, b["cadence"]); break
        self.assertIsNotNone(example, "no target inherits its cadence from its brand — "
                                      "the brand layer has no live example to demonstrate")
        t, brand_cadence = example
        self.assertEqual(t["cadence"], brand_cadence,
                         f"{t['id']} should take its brand's cadence, over the defaults' {dflt}")
        self.assertNotEqual(brand_cadence, dflt,
                            "…and the brand's value must actually differ from the default, "
                            "or this demonstrates nothing")
        self.assertEqual(target("bmw-i5-m60")["years"],
                         ["2024", "2025", "2026", "2027"],
                         "and the 2024+ rule from defaults, which no target "
                         "restates")
        self.assertEqual(target("bmw-i5-m60")["make"], "BMW")

    def test_a_model_without_trims_is_one_target(self):
        """Read off the config rather than naming one model: most of this
        watchlist is trimless now, and the one this test used to name
        (hyundai-ioniq5) was stood down when the Ioniq 9 took Hyundai's
        place."""
        cfg = json.loads(Path("targets.json").read_text())["watchlist"]
        trimless = [(bk, mk) for bk, b in cfg.items() if b.get("active", True)
                    for mk, m in b["models"].items()
                    if m.get("active", True) and not m.get("trims")]
        # This was `> 20`, a floor typed when the tail was thirty-odd. The rule
        # it sits in is that a model with no trims resolves to exactly one
        # target, which is checked below and does not depend on how many there
        # are; the floor only says the watchlist has not been emptied.
        self.assertTrue(trimless, "the watchlist has no trimless model left to check")
        for bk, mk in trimless:
            tid = f"{bk}-{mk}"
            self.assertIn(tid, T.TARGETS)
            self.assertEqual(T.TARGETS[tid]["trim_key"], "all", tid)

    def test_shopping_is_the_i5_and_the_i7(self):
        # The decision is between the i5 and the i7 now. The iX came off the
        # shopping list and the i7 — removed once before, when it was not in
        # the running — came back onto it; each shopped model brings its
        # nationwide CPO watch, because the certified promo rate is what makes
        # any of them affordable, plus the daily hunt on the trim being bought.
        shopped = sorted(t for t, v in T.TARGETS.items() if v["shopping"])
        # The i7's certified watch is stood down — see
        # test_the_i7_certified_watch_cannot_reach_a_certified_i7 — so the i7
        # is shopped through its edrive50 hunt alone.
        self.assertEqual(shopped, ["bmw-i5-cpo", "bmw-i5-edrive40",
                                   "bmw-i7-edrive50"])

    def test_the_i4_paid_for_the_i7(self):
        """The i4 was already a benchmark rather than a candidate, and at full
        depth on a daily cadence it was the single most expensive target on the
        list — ten calls a day, a third of the whole plan. Standing it down is
        what bought the i7 its own daily hunt without the month moving."""
        self.assertTrue(all("i4" not in tid for tid in T.TARGETS))
        self.assertIn("bmw-i7-edrive50", T.TARGETS)
        _, worst, avg = T.planned_calls()
        self.assertLessEqual(worst, T.BUDGET)
        self.assertLessEqual(avg * 30.5, T.MONTHLY)

    def test_the_ix_stepped_back_without_leaving(self):
        """Toned down, not removed: still tracked for comparison, on the slow
        cadence the other comparison models use, but its nationwide certified
        sweep — the most expensive thing a model can carry — stands down with
        the shopping decision that justified it."""
        ix = [t for t in T.TARGETS if t.startswith("bmw-ix")]
        self.assertTrue(ix, "the iX is a comparison model, not a deletion")
        self.assertNotIn("bmw-ix-cpo", T.TARGETS)
        for tid in ix:
            self.assertFalse(T.TARGETS[tid]["shopping"])
            self.assertGreaterEqual(T.TARGETS[tid]["cadence"], 3)

    def test_cpo_watches_are_affordable_and_staggered(self):
        # national_only halves each watch's cost (no States query — the
        # national miles.asc answer already contains the states), and the
        # two watches take alternating cadence-2 days, so neither the worst
        # day nor the month blows the budget the way two full-depth
        # nationwide targets naively would.
        watches = [tid for tid in T.TARGETS if tid.endswith("-cpo")]
        self.assertTrue(watches, "the i5 certified watch is the live one")
        for tid in watches:
            t = T.TARGETS[tid]
            self.assertEqual(T.sources_for(t), [("National", None)])
            self.assertEqual(T.calls_for(t), 2)     # 1 source x 1 sort x 2 pages
            self.assertEqual(T.window_dim(t), "miles")
        offsets = [T.TARGETS[tid]["offset"] for tid in watches]
        self.assertEqual(len(offsets), len(set(offsets)),
                         "two watches on the same days doubles the worst-day "
                         "cost for no coverage gain")

    def test_the_i7_certified_watch_cannot_reach_a_certified_i7(self):
        """Stood down because it cannot work, not because it was expensive.

        Stood down before it ever ran, on a prediction rather than a
        measurement — added 2026-09-01 with offset 1 on cadence 2, first due
        2026-09-02, stood down the same day: 0 rows, 0 calls, and the 30 calls
        a month is a plan figure, not a spend. The mechanism is what is
        measured: the query takes the 40 lowest-mileage i7s nationally on
        miles.asc and then filters to certified under 30,000 miles, and in the
        i7 rows observed the whole 40-record window is 2026 new inventory at
        1-4 miles, none certified. Deeper pagination would eventually reach a
        certified car, but not for 30 calls a month while the ordinary eDrive50
        query already holds certified sub-30k i7s.

        This test exists so it cannot be switched back on without the fix. The
        i5 watch is left alone: it works, on a narrower year range.
        """
        cfg = json.loads(Path("targets.json").read_text())
        i7cpo = cfg["watchlist"]["bmw"]["models"]["i7"]["trims"]["cpo"]
        # The guidance rides on the assertion rather than sitting behind an
        # `if active:` branch below it — that branch could never run, because
        # the line above has already asserted active is False, so the one thing
        # a person re-enabling this needs to read would never have printed.
        self.assertIs(i7cpo.get("active"), False,
                      "Re-enabling this needs more than a flag: miles.asc alone "
                      "cannot reach a certified i7 while 2026 is in its years. "
                      "Drop 2026 first (the i5 watch works precisely because "
                      "its years stop at 2025), or switch the sort.")
        self.assertNotIn("bmw-i7-cpo", T.TARGETS)
        self.assertNotIn("bmw-i7-cpo", cfg["buyer"]["shopping"],
                         "a stood-down target must not stay on the shopping list")

    def test_no_history_is_orphaned_by_standing_it_down(self):
        """Retiring a target that HAD rows would strand them: the report reads
        history through TARGETS. The i7 watch never returned one, so there is
        nothing to strand — and this checks that rather than assuming it."""
        import csv as _csv
        seen = {r["target"] for r in _csv.DictReader(
            (Path(__file__).parent.parent / "data/snapshots.csv").open(newline=""))}
        self.assertNotIn("bmw-i7-cpo", seen)

    def test_one_ev_per_brand_outside_bmw(self):
        """The rule that replaced the old reduction.

        The EV6 and the Q4 e-tron came off to pay for the CPO watches; they
        stay off for a different reason now — their brands are represented by
        the EV9 and the A6 e-tron, and the watchlist carries one nameplate per
        brand outside BMW, whose trims are the thing being shopped. The
        Equinox EV came back as Chevrolet's, which is why the old assertion
        that no target contains "equinox" could not survive.
        """
        for tid in T.TARGETS:
            self.assertNotIn("ev6", tid)
            self.assertNotIn("q4-etron", tid)
        self.assertIn("audi-a6-etron", T.TARGETS)
        self.assertIn("chevrolet-equinox-ev", T.TARGETS)
        # One NAMEPLATE per brand, not one target. It was one target, which was
        # the same thing only while every non-BMW model was trimless — and the
        # Lucid Air stopped being: one trimless watch fetched the twenty
        # cheapest Airs, which on a model whose prices collapsed is twenty
        # Pures and no Grand Touring in sight, so it carries Pure / Touring /
        # Grand Touring the way the i5 carries its own. Counting targets would
        # have made that read as a second Lucid.
        per_brand = Counter(t["model_key"] for t in T.TARGETS.values()
                            if t["brand"] != "bmw")
        by_brand = Counter()
        for t in T.TARGETS.values():
            if t["brand"] != "bmw":
                by_brand[t["brand"]] = len({x["model_key"] for x in T.TARGETS.values()
                                            if x["brand"] == t["brand"]})
        extra = {b: n for b, n in by_brand.items() if n > 1}
        self.assertFalse(extra, f"one nameplate per brand outside BMW: {extra}")
        # This was `> 25` — a floor typed when the watchlist held 33 non-BMW
        # brands, which says nothing about the RULE and fails the day someone
        # deliberately trims the list. Re-typing it as 23 would just move the
        # rot. What is worth guarding is that a brand leaves ON PURPOSE: the
        # config's own `active` flag stands a model down and keeps its research
        # notes, and a stood-down model must say why beside the flag, so a
        # watchlist that has quietly shrunk is not mistakable for one that was
        # cut.
        cfg = json.loads(Path("targets.json").read_text())["watchlist"]
        silent = []
        for bk, b in cfg.items():
            if bk.startswith("//") or not isinstance(b, dict):
                continue
            for mk, m in (b.get("models") or {}).items():
                if mk.startswith("//") or not isinstance(m, dict):
                    continue
                if m.get("active", True) is False and not m.get("// active"):
                    silent.append(f"{bk}/{mk}")
        self.assertEqual([], silent,
                         "these models are stood down with no reason beside the flag; "
                         "a watchlist that shrank by accident reads exactly like one "
                         f"that was trimmed: {silent}")
        self.assertGreaterEqual(len(per_brand), 1,
                                "…and at least one brand outside BMW is still watched")

    def test_site_dates_the_data_not_the_build(self):
        """generated is the day the file was BUILT (an offline rebuild stamps
        it with no fetch); data_through is the newest snapshot day anywhere —
        the honest date the pages show. It must equal the max snapshot_date
        and never exceed generated."""
        rows = [
            {"snapshot_date": "2026-08-27", "vin": "A", "target": "t"},
            {"snapshot_date": "2026-08-29", "vin": "B", "target": "t"},
            {"snapshot_date": "2026-08-28", "vin": "C", "target": "t"},
        ]
        self.assertEqual(max(r["snapshot_date"] for r in rows), "2026-08-29")
        import json, pathlib
        site = json.loads((pathlib.Path(__file__).parent.parent / "docs" / "data.json").read_text())
        self.assertIn("data_through", site)
        dates = [d["date"] for b in site["brands"].values()
                 for m in b["models"].values() for d in (m.get("daily") or [])]
        if dates:
            self.assertEqual(site["data_through"], max(dates))
        self.assertLessEqual(site["data_through"], site["generated"])

    def test_parsers_survive_dirty_input(self):
        self.assertEqual(T.to_int("$46,590"), 46590)
        self.assertEqual(T.to_int(""), None)
        self.assertEqual(T.to_int(None), None)
        self.assertEqual(T.to_float("39.77"), 39.77)
        self.assertEqual(T.dig({"a": {"b": 1}}, "a.b"), 1)
        self.assertIsNone(T.dig({"a": "string"}, "a.b"),
                          "digging into a string must not raise")
        self.assertEqual(T.first({"a": "", "b": "x"}, ["a", "b"]), "x")


class TestDashboardContract(unittest.TestCase):
    """What docs/index.html is allowed to assume about docs/data.json.

    The dashboard is 3,700 lines that no test in this file has ever run, and
    the multi-select comparison leans on four properties of the payload that
    nothing on the Python side promises in writing. Each one, if it quietly
    stopped holding, would not crash the page — it would make it show a
    slightly wrong number, which is the failure this repo cares about most.

    These read the committed docs/data.json, the same file the pages fetch.
    """

    @staticmethod
    def _models():
        site = json.loads((Path(__file__).parent.parent / "docs" / "data.json").read_text())
        return site, [(bk, mk, m) for bk, b in site["brands"].items()
                      for mk, m in b["models"].items()]

    def test_the_published_sheet_still_covers_the_watchlist(self):
        """The anchor every other test in this class needs.

        They all iterate the sheet's own models, so a sheet with a model
        DELETED satisfies each of them vacuously — the properties hold over
        whatever is left. Derived from TARGETS rather than a literal list, so
        it follows the config instead of duplicating it.
        """
        site, _ = self._models()
        tids = {tid for b in site["brands"].values()
                for m in b["models"].values() for tid in (m.get("trims") or {})}
        self.assertEqual(set(T.TARGETS) - tids, set(),
                         "docs/data.json no longer names every watched trim")

    def test_listings_are_one_row_per_vin(self):
        """pick_display_rows collapses a VIN two targets both matched into one
        row. Every count, median and pooled ranking on the dashboard takes that
        as given — a duplicate would be counted twice by all of them."""
        _, models = self._models()
        for bk, mk, m in models:
            vins = [x["vin"] for x in m["listings"]]
            self.assertEqual(len(vins), len(set(vins)),
                             f"{bk}/{mk}: listings must be one row per VIN")

    def test_departures_all_carry_their_price_history(self):
        """The scoped and pooled charts rebuild each day from every car's own
        series, departures included — that second half is what stops a day
        looking more expensive than it was because the cheap car sold on it. A
        gone row with no series silently drops the page back to the national
        line (docs/index.html scopedDaily)."""
        _, models = self._models()
        for bk, mk, m in models:
            for g in (m.get("gone") or []):
                self.assertTrue(g.get("series"),
                                f"{bk}/{mk} {g.get('vin')}: a departure without its series")

    def test_every_row_names_a_trim_the_config_still_has(self):
        """The trim chips are built from m.trims and filter on x.trim_id. A row
        pointing at a trim that is not there is a car no chip can ever show."""
        _, models = self._models()
        for bk, mk, m in models:
            trims = set(m.get("trims") or {})
            for x in m["listings"] + (m.get("gone") or []):
                self.assertIn(x.get("trim_id"), trims,
                              f"{bk}/{mk}: {x.get('vin')} claims an unknown trim")
            for tid in (m.get("daily_by_trim") or {}):
                self.assertIn(tid, trims, f"{bk}/{mk}: daily_by_trim has no trim {tid}")

    def test_the_record_can_be_rebuilt_from_the_cars_themselves(self):
        """Every day in m.daily must be a day some car's series covers. This is
        the precondition for the rebuild that a filtered or multi-trim scope
        runs: a day the cars cannot account for is a day the chart would drop
        without saying so."""
        _, models = self._models()
        for bk, mk, m in models:
            days = {d["date"] for d in (m.get("daily") or [])}
            seen = {p[0] for x in m["listings"] + (m.get("gone") or [])
                    for p in (x.get("series") or [])}
            self.assertEqual(days - seen, set(),
                             f"{bk}/{mk}: a snapshot day no car's history covers")

    def test_the_trim_rows_cover_the_model_row(self):
        """The per-trim series are per FETCH TARGET, and targets overlap: the
        nationwide CPO watch and the ordinary trim both see the same certified
        car. So a day's per-trim counts SUM TO AT LEAST the model's own count,
        and on 2026-08-30 the i5's four trims totalled 137 against a model row
        of 136 — which is why the dashboard rebuilds a pooled trim scope
        VIN-uniquely from the cars instead of adding these up (and why it never
        merges their medians, which have no combination at all).

        Failing the other way — the trims totalling LESS than the model — would
        mean a car in the record belongs to no trim, and no chip could show it.
        """
        _, models = self._models()
        for bk, mk, m in models:
            by_day = {}
            for series in (m.get("daily_by_trim") or {}).values():
                for d in series:
                    by_day.setdefault(d["date"], []).append(d.get("n") or 0)
            for d in (m.get("daily") or []):
                parts = by_day.get(d["date"])
                if parts is None:
                    continue
                self.assertGreaterEqual(
                    sum(parts), d.get("n") or 0,
                    f"{bk}/{mk} {d['date']}: the trim rows do not cover the model row")


# --------------------------------------------------------------------------
# The share card. Not Tracking.py, but the same rule: every number defensible.
# --------------------------------------------------------------------------
class TestShareCard(unittest.TestCase):
    """docs/og.png must not be able to go stale.

    It used to be a dashboard screenshot, taken 2026-08-26 and never retaken.
    Five days later it was still unfurling "Every model", "10 models", a
    Chevrolet chip and "$13,901 · Kia EV6" on Slack, iMessage, X and LinkedIn
    — while data.json said "The watchlist", seven models, five brands (no
    Chevrolet, no EV6). Nothing corrects it: the crawlers that draw the unfurl
    do not run the JS that writes the real title, and the daily run writes
    data.json, REPORT.md and snapshots.csv but has never written a PNG.

    So the card carries no number and names no car. These tests hold that,
    against a future session that helpfully puts "7 models" back on it.
    """

    ROOT = Path(__file__).parent.parent

    @classmethod
    def _card_text(cls):
        """The card's visible words — body markup with the tags taken out. The
        head is excluded on purpose: the comment there quotes the stale numbers
        this class exists to explain."""
        src = (cls.ROOT / "tools" / "og_card.html").read_text()
        # <body[^>]*> not "<body>": the day someone adds a class to it, a
        # str.split would raise IndexError and this class would fail for a
        # reason that has nothing to do with the promise it holds.
        body = re.split(r"<body[^>]*>", src, maxsplit=1)[1].split("</body>", 1)[0]
        # Entities are unescaped BEFORE the digit scan, in both directions:
        # &#8212; displays an em dash and must not trip the check, while
        # &#55; displays a 7 and must.
        return html_mod.unescape(re.sub(r"<[^>]*>", " ", body))

    def test_the_card_carries_no_number(self):
        """A count, a price or a date on the card is wrong the day after it is
        committed and stays wrong, because nothing regenerates the image."""
        text = self._card_text()
        self.assertNotIn("$", text, "a price on the og card")
        self.assertFalse(re.search(r"\d", text),
                         f"a digit on the og card: {re.findall(r'.{0,24}[0-9].{0,24}', text)}")

    def test_the_card_names_no_car(self):
        """The watchlist is config: brands and models come and go (Chevrolet
        did). A card that names one is a card that has to be retaken."""
        site = json.loads((self.ROOT / "docs" / "data.json").read_text())
        names = {b["label"] for b in site["brands"].values()}
        names |= {m["label"] for b in site["brands"].values() for m in b["models"].values()}
        # The subject comes from today's data.json, never from a name typed in
        # here — that is what stops a tracker run rotting this test. If a run
        # ever leaves it with nothing to look for, say so out loud: a vacuous
        # pass reading as green coverage is the failure mode one rung up.
        if not names:
            self.skipTest("docs/data.json names no brand or model today — nothing to look for")
        text = self._card_text()
        for name in names:
            self.assertIsNone(re.search(rf"\b{re.escape(name)}\b", text),
                              f"the og card names {name}")

    def test_both_pages_describe_the_card(self):
        """og:image with no og:image:alt leaves the unfurl's whole visual
        payload undescribed to a screen reader. The alt is checked for digits
        too — it is the same promise in text."""
        for page in ("index.html", "how.html"):
            head = (self.ROOT / "docs" / page).read_text()
            # Find the TAG first, then its content attribute. Pinning the whole
            # line ties this test to attribute order and spacing in a head block
            # other work also edits: it would then fail while the promise it
            # encodes is still kept, which is how a suite gets loosened.
            tag = re.search(r"<meta\b[^>]*\bproperty=[\"']og:image:alt[\"'][^>]*>", head)
            self.assertIsNotNone(tag, f"docs/{page}: og:image with no og:image:alt")
            m = re.search(r"content=[\"']([^\"']*)", tag.group(0))
            self.assertIsNotNone(m, f"docs/{page}: og:image:alt with no content")
            self.assertFalse(re.search(r"[0-9$]", m.group(1)),
                             f"docs/{page}: a number in og:image:alt")

    def test_the_shipped_card_is_the_frame_the_renderer_declares(self):
        """A guard, not a regression: it passes on the commit before it, and it
        is here because nothing else looks at the PNG at all.

        tools/shoot_hero.mjs declares the frame docs/og.png is rendered in.
        Reading the PNG's own header back and comparing binds the shipped
        bytes to the committed recipe, so changing the frame without
        re-rendering fails here instead of shipping a card the unfurl
        letterboxes. It cannot see a content-only edit to the card — for that
        the answer is to run the renderer, which refuses to shoot a card with
        a digit on it.
        """
        src = (self.ROOT / "tools" / "shoot_hero.mjs").read_text()
        frame = re.search(r"CARD_FRAME\s*=\s*\{\s*width:\s*(\d+),\s*height:\s*(\d+)", src)
        scale = re.search(r"CARD_SCALE\s*=\s*(\d+)", src)
        self.assertIsNotNone(frame, "tools/shoot_hero.mjs no longer declares CARD_FRAME")
        self.assertIsNotNone(scale, "tools/shoot_hero.mjs no longer declares CARD_SCALE")
        want = (int(frame.group(1)) * int(scale.group(1)),
                int(frame.group(2)) * int(scale.group(1)))

        png = (self.ROOT / "docs" / "og.png").read_bytes()
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n", "docs/og.png is not a PNG")
        self.assertEqual(png[12:16], b"IHDR", "docs/og.png has no leading IHDR chunk")
        got = struct.unpack(">II", png[16:24])
        self.assertEqual(got, want,
                         f"docs/og.png is {got[0]}x{got[1]}, but tools/shoot_hero.mjs renders "
                         f"{want[0]}x{want[1]} — re-render it: node tools/shoot_hero.mjs <ds> --only card")


class TestCanonicalUrl(unittest.TestCase):
    """One address per page, stated in the markup.

    The dashboard puts real state in the query string — ?brand, ?m, ?models,
    ?trims — because a comparison you cannot send to the person buying the car
    with you is not a comparison. But GitHub Pages has no server-side render:
    it returns the same byte-identical docs/index.html for every one of those
    URLs and the JS applies the state afterwards. So to a crawler, which is the
    only reader a canonical speaks to, they are one document with one title and
    one description — and without a canonical, nothing says which address that
    document actually is.

    These tests hold the decision, both halves of it: the tag is there and
    agrees with og:url, and it is STATIC. A canonical rewritten per view by JS
    is read by nobody who matters (the unfurl crawlers do not run JS) and would
    claim distinct pages for identical HTML, which is the duplication the tag
    exists to resolve.
    """

    ROOT = Path(__file__).parent.parent
    PAGES = ("index.html", "how.html")

    @classmethod
    def _canonicals(cls, src):
        return [re.search(r"href=[\"']([^\"']*)", t).group(1)
                for t in re.findall(r"<link\b[^>]*\brel=[\"']canonical[\"'][^>]*>", src)]

    def test_each_page_declares_exactly_one_canonical(self):
        for page in self.PAGES:
            hrefs = self._canonicals((self.ROOT / "docs" / page).read_text())
            self.assertEqual(len(hrefs), 1,
                             f"docs/{page}: {len(hrefs)} rel=canonical link(s), want exactly 1")

    def test_the_canonical_agrees_with_og_url(self):
        """Two identity claims in one head that disagree are worse than one:
        the unfurl would say one thing and the crawlable markup another."""
        for page in self.PAGES:
            src = (self.ROOT / "docs" / page).read_text()
            og = re.search(r"<meta\b[^>]*\bproperty=[\"']og:url[\"'][^>]*>", src)
            self.assertIsNotNone(og, f"docs/{page}: no og:url")
            og_url = re.search(r"content=[\"']([^\"']*)", og.group(0)).group(1)
            self.assertEqual(self._canonicals(src), [og_url],
                             f"docs/{page}: canonical and og:url disagree")

    def test_the_canonical_names_a_bare_page_not_a_view(self):
        """It has to be the address the whole ?models=/?trims=/?brand= space
        collapses onto — absolute, and carrying no state of its own."""
        for page in self.PAGES:
            hrefs = self._canonicals((self.ROOT / "docs" / page).read_text())
            # Named, not indexed: a missing tag should read as the finding it is,
            # not as an IndexError three lines down.
            self.assertTrue(hrefs, f"docs/{page}: no rel=canonical to check")
            href = hrefs[0]
            self.assertTrue(href.startswith("https://"),
                            f"docs/{page}: canonical {href!r} is not an absolute https URL")
            self.assertNotIn("?", href, f"docs/{page}: canonical carries a query string")
            self.assertNotIn("#", href, f"docs/{page}: canonical carries a fragment")

    def test_no_script_rewrites_the_canonical(self):
        """The decision, not just its result. syncUrl() rewrites the address bar
        on every chip press; the day someone makes the canonical follow it, the
        tag starts claiming a page per permutation to crawlers that never see
        the rewrite anyway."""
        for page in self.PAGES:
            src = (self.ROOT / "docs" / page).read_text()
            for block in re.findall(r"<script\b[^>]*>([\s\S]*?)</script>", src):
                # Every way a script reaches this tag pairs "rel" with
                # "canonical" a few characters apart: link[rel=canonical],
                # rel="canonical", setAttribute('rel', 'canonical'). The bare
                # word on its own is not the tell — docs/index.html already
                # says "canonicalize" about the address bar, which is a
                # different thing and allowed to stay.
                hit = re.search(r"rel[^;\n]{0,20}canonical", block, re.I)
                found = hit.group(0) if hit else ""
                self.assertIsNone(hit, f"docs/{page}: a script reaches the canonical link "
                                       f"({found!r} in a <script>) — it must stay static")




class TestFinance(unittest.TestCase):
    """The rate a car finances at, and the promo's end date.

    Monthly payment is the number this buyer decides on: a certified i5 at the
    2.99% promo beats a cheaper non-certified one at the ordinary rate by more
    than shipping ever moves, which is why it reorders a shortlist where landed
    cost does not. That makes the rate table load-bearing, and it has two ways
    to lie quietly.

    The first is the CPO boundary. A promo that leaked onto a non-certified car
    would invent a payment no lender has offered, on exactly the cars the
    ranking is meant to separate. The second is time: a promo has an end date,
    and a page still ranking on a rate that lapsed last month is worse than one
    that never had the feature. Both are settled in Python, against the run's
    own clock, so a reader's device cannot disagree.
    """

    def test_a_live_promo_is_active_and_an_expired_one_is_not(self):
        """The whole point of shipping `active` rather than a date the browser
        re-decides: one clock settles it, and it is this one."""
        today = date.fromordinal(T.TODAY_ORD)
        past = date.fromordinal(T.TODAY_ORD - 1).isoformat()
        future = date.fromordinal(T.TODAY_ORD + 30).isoformat()
        cfg = {"fallback_apr": 6.9, "promos": [
            {"model": "bmw/i5", "apr": 2.99, "expires": future},
            {"model": "bmw/i7", "apr": 3.49, "expires": past},
            {"model": "bmw/ix", "apr": 2.49},                     # no end date
        ]}
        with unittest.mock.patch.dict(T.BUYER, {"finance": cfg}, clear=False):
            out = T.finance_export()
        by = {p["model"]: p for p in out["promos"]}
        self.assertTrue(by["bmw/i5"]["active"], "a promo ending in 30 days still applies")
        self.assertFalse(by["bmw/i7"]["active"], "a promo that ended yesterday must not apply")
        self.assertTrue(by["bmw/ix"]["active"], "no end date is a standing offer, not an expired one")
        self.assertEqual(by["bmw/i5"]["days_left"], 30)
        self.assertIsNone(by["bmw/ix"]["days_left"])
        # An expired promo still ships, because "that rate ran out on the 31st"
        # explains a page that suddenly ranks differently.
        self.assertEqual(len(out["promos"]), 3)
        self.assertEqual(out["stale_days"], None)
        del today

    def test_terms_and_the_cpo_boundary_round_trip(self):
        """`cpo_only` defaults to True because the safe leak is the promo
        reaching too few cars, never too many — and the page trusts the
        exported boolean for arithmetic, so it is asserted by identity: a
        mutant returning a real False passes any truthiness check on the
        i7 promo below and quietly rates every uncertified i5 at 2.99%."""
        cfg = {"fallback_apr": 6.9, "terms": [36, 48, 60, 72], "default_term": 48, "promos": [
            {"model": "bmw/i5", "apr": 2.99},                     # unstated: certified only
            {"model": "bmw/i7", "apr": 3.49, "cpo_only": False},  # stated: every i7
        ]}
        with unittest.mock.patch.dict(T.BUYER, {"finance": cfg}, clear=False):
            out = T.finance_export()
        self.assertEqual(out["terms"], [36, 48, 60, 72])
        self.assertEqual(out["default_term"], 48)
        by = {p["model"]: p for p in out["promos"]}
        self.assertIs(by["bmw/i5"]["cpo_only"], True)
        self.assertIs(by["bmw/i7"]["cpo_only"], False)

    def test_a_promo_that_ends_today_still_applies_today(self):
        """The offer runs THROUGH its end date. `active` is the only field
        that separates >= from >: days_left is 0 either way, so a page counting
        down to zero would still rank on a rate the export had already switched
        off."""
        today = date.fromordinal(T.TODAY_ORD).isoformat()
        cfg = {"fallback_apr": 6.9, "promos": [{"model": "bmw/i5", "apr": 2.99, "expires": today}]}
        with unittest.mock.patch.dict(T.BUYER, {"finance": cfg}, clear=False):
            p = T.finance_export()["promos"][0]
        self.assertTrue(p["active"], "a promo that ends today applies today")
        self.assertEqual(p["days_left"], 0)

    def test_a_bad_date_does_not_take_the_run_down(self):
        """targets.json is hand-edited. A typo in an expiry must degrade to a
        standing offer, not raise inside build_outputs at 11:00 UTC."""
        cfg = {"fallback_apr": 6.9, "fallback_checked": "not-a-date",
               "promos": [{"model": "bmw/i5", "apr": 2.99, "expires": "2026-13-45"}]}
        with unittest.mock.patch.dict(T.BUYER, {"finance": cfg}, clear=False):
            out = T.finance_export()
        self.assertTrue(out["promos"][0]["active"])
        self.assertIsNone(out["stale_days"], "an unparseable check date is unknown, not zero")

    def test_no_finance_block_means_no_finance_key(self):
        """A buyer who never set a rate gets no payment ranking at all — the
        page hides the sort rather than quoting a made-up number."""
        with unittest.mock.patch.dict(T.BUYER, {"finance": {}}, clear=False):
            self.assertIsNone(T.finance_export())

    def test_the_shipped_config_is_coherent(self):
        """The real targets.json, held to the shape the dashboard assumes."""
        fin = json.loads((Path(__file__).parent.parent / "targets.json").read_text())["buyer"].get("finance")
        if not fin:
            self.skipTest("this buyer has no finance block")
        self.assertGreater(fin["fallback_apr"], 0, "the fallback rate is what every non-promo car uses")
        self.assertIn(fin["default_term"], fin["terms"], "the default term must be one the reader can pick")
        cfg = json.loads((Path(__file__).parent.parent / "targets.json").read_text())
        models = {f"{bk}/{mk}" for bk, b in cfg["watchlist"].items()
                  for mk in (b.get("models") or {})}
        for p in fin["promos"]:
            self.assertIn(p["model"], models, f"promo names {p['model']}, which is not a watched model")
            self.assertLess(p["apr"], fin["fallback_apr"],
                            "a promo above the ordinary rate is not a promo")
            date.fromisoformat(p["expires"])       # raises if the date is malformed

    def test_the_dashboard_gets_the_table(self):
        """docs/data.json is what the page actually reads; the block has to
        survive the export, not just exist in the config."""
        site = json.loads((Path(__file__).parent.parent / "docs" / "data.json").read_text())
        fin = (site.get("buyer") or {}).get("finance")
        if not fin:
            self.skipTest("no finance block in this snapshot")
        for p in fin["promos"]:
            self.assertIn("active", p, "the page trusts `active` for arithmetic; it must be published")
            self.assertIsNotNone(p.get("apr"))


class TestSourceOverlap(unittest.TestCase):
    """What the States query buys that National does not already bring.

    Half of most targets' calls go to asking the buyer's four states — plus the
    four watched from beyond them, one comma list, one call — the same question
    the national query just asked. This docstring used to argue the saving from
    flipping the benchmark models to `national_only`, and to say the flag would
    be flipped "when this audit has watched `states_only` sit at zero for a
    while". The audit ran and the answer went the other way: `states_only` did
    not sit at zero, it sat at 88% of the States catch on every shallow target,
    and twenty-eight targets came OFF the flag rather than going onto it. One
    is left, and it is national by definition rather than to save a call.

    So the paragraph is inverted but its point stands, and it is the reason
    these tests exist: the flag is not set or cleared on an argument, it is
    decided by this measurement — which makes a broken instrument the most
    expensive thing in the file. A National query that died mid-fetch used to
    be archived as a complete comparison, which is the shape of a real finding
    and entirely the failure; TestTheOverlapLogRecordsOnlyQueriesThatFinished
    holds that half.
    """

    def setUp(self):
        self._vins = dict(T.SOURCE_VINS)
        self._exh = set(T.EXHAUSTED)
        T.SOURCE_VINS.clear()
        T.EXHAUSTED.clear()

    def tearDown(self):
        T.SOURCE_VINS.clear(); T.SOURCE_VINS.update(self._vins)
        T.EXHAUSTED.clear(); T.EXHAUSTED.update(self._exh)

    @staticmethod
    def _a_target_with_both_sources():
        for t in T.TARGETS.values():
            if len(T.sources_for(t)) == 2:
                return t["id"]
        return None

    def test_states_only_is_what_national_only_would_lose(self):
        """The one number the decision rests on: cars no national query returned."""
        tid = self._a_target_with_both_sources()
        if not tid:
            self.skipTest("no target uses both sources")
        T.SOURCE_VINS[(tid, "States")] = {"A", "B", "C"}
        T.SOURCE_VINS[(tid, "National")] = {"B", "C", "D"}
        rows = {(tid, "A"): {"state": "OH"}, (tid, "B"): {"state": "CA"},
                (tid, "C"): {"state": "TX"}, (tid, "D"): {"state": "FL"}}
        o = T.source_overlap(rows)[tid]
        self.assertEqual(o["states_only"], 1, "A is the only car National never returned")
        self.assertEqual(o["both"], 2)
        self.assertEqual(o["states_only_in"], ["OH"],
                         "the states that would go dark are named, not just counted")

    def test_an_exhausted_national_query_settles_it(self):
        """A short page means that query returned its scope's ENTIRE result set.
        If National came back short, it saw the whole country, so the States
        half cannot be buying anything new — no local-coverage argument
        survives that, and the audit has to say so."""
        tid = self._a_target_with_both_sources()
        if not tid:
            self.skipTest("no target uses both sources")
        T.SOURCE_VINS[(tid, "States")] = {"A"}
        T.SOURCE_VINS[(tid, "National")] = {"A", "B"}
        T.EXHAUSTED.add((tid, "National"))
        self.assertTrue(T.source_overlap({})[tid]["national_exhausted"])

    def test_a_national_only_target_is_not_audited(self):
        """It has no States query to compare against; reporting it as
        zero-overlap would read as evidence for a saving already taken."""
        nat = next((t["id"] for t in T.TARGETS.values() if t.get("national_only")), None)
        if not nat:
            self.skipTest("no national_only target configured")
        T.SOURCE_VINS[(nat, "National")] = {"A", "B"}
        self.assertNotIn(nat, T.source_overlap({}))

    def test_the_log_keeps_one_entry_per_day(self):
        """The run is re-runnable; the audit is about days. A second run on the
        same date must correct its entry, not append a second one."""
        import tempfile
        tid = self._a_target_with_both_sources() or "x"
        o = {tid: {"states": 3, "national": 5, "both": 3, "states_only": 0}}
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ov.json"
            T.save_overlap_history(o, path=p)
            T.save_overlap_history(o, path=p)
            hist = json.loads(p.read_text())
            self.assertEqual(len(hist), 1)
            self.assertEqual(hist[T.TODAY][tid], [3, 5, 3, 0])

    def test_the_log_is_bounded(self):
        """It rides in the repo beside a ledger that already grows daily; it
        keeps a window, not a history."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ov.json"
            p.write_text(json.dumps({f"2020-01-{d:02d}": {"t": [1, 1, 1, 0]} for d in range(1, 29)}))
            T.save_overlap_history({"t": {"states": 1, "national": 1, "both": 1, "states_only": 0}},
                                   path=p, keep=10)
            hist = json.loads(p.read_text())
            self.assertEqual(len(hist), 10)
            self.assertIn(T.TODAY, hist, "today's entry is never the one evicted")

    def test_a_corrupt_log_is_replaced_not_fatal(self):
        """A half-written file from a killed run must not take down the next
        one — the audit is instrumentation, and instrumentation that can crash
        the thing it measures is worse than none."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ov.json"
            p.write_text("{not json")
            T.save_overlap_history({"t": {"states": 1, "national": 1, "both": 1, "states_only": 0}}, path=p)
            self.assertEqual(list(json.loads(p.read_text())), [T.TODAY])


class TestSpend(unittest.TestCase):
    """What the run actually cost, as opposed to what it was budgeted.

    planned_calls() is an upper bound: every due target billed for every page
    it may fetch. The fetch loop then spends less whenever a query comes back
    short, because that stops its pagination and skips its newest probe — and
    the thin certified markets do this most days. The difference is the real
    headroom, and every "can we afford one more model?" is a guess without it.

    The trap this guards is the one that would make the measurement worse than
    none: a target that was DUE and spent NOTHING has not saved money, it has
    failed. Counting that as headroom spends the budget twice — once on the
    model it appears to afford, and again when the broken target recovers.
    """

    def setUp(self):
        self._spent, self._exh = dict(T.SPENT), set(T.EXHAUSTED)
        T.SPENT.clear(); T.EXHAUSTED.clear()

    def tearDown(self):
        T.SPENT.clear(); T.SPENT.update(self._spent)
        T.EXHAUSTED.clear(); T.EXHAUSTED.update(self._exh)

    @staticmethod
    def _due():
        return [t for t in T.TARGETS.values() if T.due_on(t, T.TODAY_ORD)]

    def test_an_exhausted_market_banks_the_calls_it_did_not_spend(self):
        due = self._due()
        planned = sum(T.calls_for(t) for t in due)
        for t in due:
            T.SPENT[t["id"]] = T.calls_for(t)
        T.SPENT[due[0]["id"]] -= 3
        row = T.spend_report(planned)
        self.assertEqual(row["banked"], 3)
        self.assertEqual(row["unrun"], 0)
        self.assertEqual(row["off_plan"][due[0]["id"]], [T.calls_for(due[0]), T.calls_for(due[0]) - 3])

    def test_a_target_that_never_ran_is_not_headroom(self):
        """The whole reason this class exists."""
        due = self._due()
        planned = sum(T.calls_for(t) for t in due)
        for t in due:
            T.SPENT[t["id"]] = T.calls_for(t)
        T.SPENT[due[0]["id"]] -= 3          # a real saving
        T.SPENT[due[1]["id"]] = 0           # a failure wearing a saving's clothes
        row = T.spend_report(planned)
        self.assertEqual(row["banked"], 3, "only the working target's underspend is headroom")
        self.assertEqual(row["unrun"], T.calls_for(due[1]))
        self.assertIn(due[1]["id"], row["silent_targets"])

    def test_spending_the_whole_plan_banks_nothing(self):
        due = self._due()
        for t in due:
            T.SPENT[t["id"]] = T.calls_for(t)
        row = T.spend_report(sum(T.calls_for(t) for t in due))
        self.assertEqual(row["banked"], 0)
        self.assertEqual(row["off_plan"], {}, "a run that went to plan reports no exceptions")

    def test_retries_are_counted_because_they_are_billed(self):
        """SPENT increments per REQUEST, not per intended fetch.

        A retry costs a call whether or not it succeeds, and a ledger of
        intentions would hand back headroom that a bad network day already ate.
        This used to assert that a particular line of source sat inside the
        retry loop — which passes on code where the line is there and dead.
        Run the loop instead and count what it charged.
        """
        tid = "bmw-i5-edrive40"
        t = T.TARGETS[tid]
        was_spent, was_calls = dict(T.SPENT), T.CALLS
        try:
            T.SPENT.clear()
            T.CALLS = 0

            class Boom:
                status_code = 500
                text = "upstream is unwell"

            with unittest.mock.patch.object(T.requests, "get", lambda *a, **k: Boom()), \
                 unittest.mock.patch.object(T.time, "sleep", lambda *a: None):
                got = T.fetch("National", None, "price.asc", 1, t)
            self.assertIsNone(got, "two failures in a row is a failed fetch")
            self.assertEqual(T.CALLS, 2, "both attempts hit the network")
            self.assertEqual(T.SPENT.get(tid), 2,
                             "and both are charged to the target, or the budget "
                             "hands back headroom a bad network day already ate")
        finally:
            T.SPENT.clear(); T.SPENT.update(was_spent); T.CALLS = was_calls

    def test_the_log_survives_a_corrupt_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s.json"
            p.write_text("]]not json[[")
            hist = T.save_spend_history({"planned": 1, "actual": 1, "banked": 0}, path=p)
            self.assertEqual(list(hist), [T.TODAY])

    def _pace_line(self, per_day):
        """report_spend's month-to-date paragraph, for a month running at
        `per_day` calls. Two days is its minimum before it will project."""
        import io as _io, contextlib
        month = T.TODAY[:7]
        hist = {f"{month}-01": {"actual": per_day}, f"{month}-02": {"actual": per_day}}
        row = {"planned": per_day, "actual": per_day, "banked": 0, "unrun": 0,
               "silent_targets": [], "targets_due": 1, "exhausted": 0,
               "failed": 0, "off_plan": {}}
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            T.report_spend(row, hist)
        return buf.getvalue()

    def test_a_month_on_pace_to_overspend_says_so(self):
        """The headroom line used to print max(0, plan - projected), so a month
        four hundred calls over plan and a month exactly on it produced the SAME
        sentence: "~0 unspent at this rate". The one case the meter exists for
        was the one case it could not express. The plan is 92% committed before
        a single retry, and a retry bills twice, so this is not hypothetical."""
        over = T.MONTHLY / 30.4 * 1.5              # half again over plan
        out = self._pace_line(int(over) + 1)
        self.assertIn("OVERSPEND", out, out)
        self.assertNotIn("unspent at this rate", out, out)

    def test_a_month_inside_the_plan_still_reports_headroom(self):
        out = self._pace_line(int(T.MONTHLY / 30.4 * 0.5))
        self.assertIn("unspent at this rate", out, out)
        self.assertNotIn("OVERSPEND", out, out)

    def test_the_overspend_line_says_how_far_over(self):
        """"Over budget" without a magnitude is a feeling. The gap decides
        whether the answer is dropping a page or dropping a whole target."""
        per_day = int(T.MONTHLY / 30.4 * 2)
        out = self._pace_line(per_day)
        want = round(per_day * 30.4 - T.MONTHLY)
        self.assertIn(f"~{want}", out.replace(",", ""), out)


def to_float_or_zero(v):
    return T.to_float(v) or 0


class TestRerunGuard(unittest.TestCase):
    """A day already fetched must not be fetched again.

    The cron fires once. Every extra run is a workflow_dispatch, and each one
    re-bills the whole day at full price. Reconstructed from the snapshot
    commits' own footers: 529 calls over nine days — 58.8/day, ~1,790 a month
    against a 1,000-call tier — while planned_calls() reported 30.0/day and
    approved every one of them, because it reads INTENT.
    """

    def test_the_guard_reads_the_snapshot_not_the_plan(self):
        src = (Path(__file__).parent.parent / "Tracking.py").read_text()
        body = src[src.index("def main("):]
        body = body[:body.index("    rows = {}")]
        self.assertIn("load_history()", body,
                      "the guard must ask what was actually fetched, not what was planned")
        self.assertIn("ALLOW_REFETCH", body,
                      "a run that died partway needs a documented way back in")
        self.assertIn("rebuild_outputs.py", body,
                      "the guard must name the free alternative, or it just blocks people")

    def test_the_guard_is_before_the_first_call(self):
        """Refusing after spending is not refusing."""
        src = (Path(__file__).parent.parent / "Tracking.py").read_text()
        main = src[src.index("def main("):]
        self.assertLess(main.index("ALLOW_REFETCH"), main.index("batch = fetch("),
                        "the guard must sit above the fetch loop")


class TestSpendAccumulates(unittest.TestCase):
    """SPENT is a per-process global, so a second run of the day starts at
    zero. Writing hist[TODAY] = row straight over the first run's total made
    this log blind to the exact leak it was built to catch."""

    def _row(self, actual):
        return {"planned": actual, "actual": actual, "banked": 0, "unrun": 0,
                "silent_targets": [], "targets_due": 1, "exhausted": 0,
                "failed": 0, "off_plan": {}}

    def test_a_second_run_adds_to_the_day_rather_than_replacing_it(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s.json"
            T.save_spend_history(self._row(24), path=p)
            T.save_spend_history(self._row(24), path=p)
            hist = T.save_spend_history(self._row(24), path=p)
            day = hist[T.TODAY]
            self.assertEqual(day["actual"], 72, "three 24-call runs cost 72, not 24")
            self.assertEqual(day["runs"], 3)

    def test_the_days_plan_does_not_double_when_the_day_runs_twice(self):
        """`planned` is the DAY's plan, not a per-run cost.

        The first version of this accumulated it with the costs, so two runs of
        a 32-call day recorded planned 64 against actual 30 and reported 34
        calls banked — more headroom than the day ever had, on the day it was
        overspent. The one number this log exists to make honest was the one it
        inflated.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s.json"
            T.save_spend_history({**self._row(20), "planned": 30, "unrun": 0}, path=p)
            hist = T.save_spend_history({**self._row(20), "planned": 30, "unrun": 0}, path=p)
            day = hist[T.TODAY]
            self.assertEqual(day["planned"], 30, "the day was planned once")
            self.assertEqual(day["actual"], 40, "but it ran twice at 20 each")
            # Negative headroom is the point: the day spent more than it planned.
            self.assertEqual(day["banked"], -10)

    def test_the_first_run_of_a_day_still_reads_normally(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s.json"
            hist = T.save_spend_history(self._row(24), path=p)
            self.assertEqual(hist[T.TODAY]["actual"], 24)
            self.assertEqual(hist[T.TODAY]["runs"], 1)


class TestEmailDelivery(unittest.TestCase):
    """Email is off on purpose, and stays quiet about it.

    RESEND_API_KEY and EMAIL_TO have never been set. A first pass made the skip
    emit a ::warning:: annotation on every run — which is right for a feature
    that is broken and wrong for one that is switched off deliberately. The
    dashboard is this tool's delivery surface; an annotation nobody wants,
    fired daily, only teaches you to ignore annotations.
    """

    def _skip_output(self, env):
        import contextlib
        buf = _io.StringIO()
        with unittest.mock.patch.dict(os.environ, env, clear=True), \
             contextlib.redirect_stdout(buf):
            T.send_email("body", subject="s")
        return buf.getvalue()

    def test_it_says_so_without_raising_an_alarm(self):
        out = self._skip_output({"GITHUB_ACTIONS": "true"})
        self.assertIn("Email off", out)
        self.assertNotIn("::warning::", out,
                         "a deliberate setting must not annotate the run")
        self.assertNotIn("::error::", out)

    def test_it_names_where_the_report_went(self):
        self.assertIn("REPORT.md", self._skip_output({}))

    def test_configuring_both_secrets_turns_it_on(self):
        """The off state is the ABSENCE of config, not a hard-coded switch, so
        setting the two secrets is all it takes to start sending.

        This used to read the function's SOURCE for the two strings it expects.
        That passes on code with the send path deleted below the line it greps,
        on code that posts somewhere else, and on code that posts twice; it is
        the failure mode this repository has shipped more than once. Drive it
        instead: give it the two secrets, hand it a recording requests.post,
        and check what actually went out.
        """
        posts = []

        class Resp:
            status_code = 200
            text = "{}"

        def record(url, **kw):
            posts.append((url, kw))
            return Resp()

        with unittest.mock.patch.dict(os.environ,
                                      {"RESEND_API_KEY": "k", "EMAIL_TO": "someone@example.com"},
                                      clear=True), \
             unittest.mock.patch.object(T.requests, "post", record):
            T.send_email("body", subject="a subject")
        self.assertEqual(len(posts), 1, "exactly one message, not zero and not two")
        url, kw = posts[0]
        self.assertIn("api.resend.com", url)
        self.assertIn("a subject", json.dumps(kw.get("json") or {}),
                      "the subject the caller passed is the subject that is sent")

    def test_one_missing_secret_sends_nothing(self):
        """Half-configured is off, and off is silent — not a crash, and not a
        message to nobody."""
        posts = []
        with unittest.mock.patch.dict(os.environ, {"RESEND_API_KEY": "k"}, clear=True), \
             unittest.mock.patch.object(T.requests, "post", lambda *a, **k: posts.append(a)):
            T.send_email("body", subject="s")
        self.assertEqual(posts, [])


Exit = __import__("collections").namedtuple("Exit", "code text")


class TestGuardAndProvenanceBehaviour(unittest.TestCase):
    """These two features drive the real code. The rest of their coverage does
    not, and that is the point of this class.

    Every other assertion about the guard and about `via` greps Tracking.py's
    own source text, and an adversarial pass showed all of them pass on broken
    code: deleting the guard but leaving a comment carrying the tokens passes;
    inverting `TODAY in already` passes AND then makes real requests on a day
    already fetched; `sys.exit(` to `print(` passes; moving `via[key].add()`
    into the cheaper-duplicate branch — the exact failure the code's own
    comment warns about — passes; and lowercasing the vin in the key blanks the
    column universally and passes. Nothing downstream reads `via`, so that last
    regression has no symptom anywhere.
    """

    @staticmethod
    def _hist_row(day):
        r = {k: "" for k in T.FIELDS}
        r.update({"snapshot_date": day, "target": "bmw-i5-edrive40",
                  "vin": "X" * 17, "price": "40000", "miles": "1000",
                  "state": "IL", "year": "2024", "trim": "eDrive40"})
        return r

    def _drive(self, history, batches, allow_refetch=False, wrote=None):
        """Run the real main() with the API and every write stubbed out.

        write_rows is where the fetch loop's work lands, so capturing there and
        stopping runs everything under test and nothing after it.
        """
        seen, captured = [], {}
        # Tracking keeps per-run state in module globals, and other tests in
        # this file leave it dirty. EXHAUSTED especially: one stale
        # (target, source) entry short-circuits the second sort and this class
        # silently stops testing the thing it exists to test. It passed alone
        # and failed in the suite, which is exactly how that looks.
        for g in ("EXHAUSTED", "FAILED_SCOPES"):
            getattr(T, g).clear()
        for g in ("PRICE_WINDOW", "MILES_WINDOW", "SOURCE_VINS", "SPENT",
                  "OVERLAP", "TOTALS", "RAW_N"):
            getattr(T, g).clear()
        T.CALLS = 0
        T.FAILED_FETCHES = 0

        def fake_fetch(source_name, source, sort, page, t):
            seen.append((t["id"], source_name, sort, page))
            return batches(t, source_name, sort, page)

        def fake_write_rows(rows):
            captured["rows"] = list(rows)
            raise SystemExit("captured")

        env = dict(os.environ)
        env.pop("ALLOW_REFETCH", None)
        if allow_refetch is not False:
            env["ALLOW_REFETCH"] = "1" if allow_refetch is True else str(allow_refetch)
        patches = [
            unittest.mock.patch.object(T, "fetch", fake_fetch),
            unittest.mock.patch.object(T, "write_rows", fake_write_rows),
            unittest.mock.patch.object(T, "load_history", lambda: list(history)),
            unittest.mock.patch.object(T, "send_email", lambda *a, **k: None),
            unittest.mock.patch.object(T, "save_zip_cache", lambda *a, **k: None),
            # `wrote` is a list a caller can pass to learn WHICH of these ran
            # before main() returned — the writers are stubbed so nothing lands
            # in the working tree, and whether they were reached is the fact one
            # test below is about.
            unittest.mock.patch.object(T, "save_spend_history",
                                       lambda row, **k: ((wrote is not None and wrote.append("data/spend.json")), {})[1]),
            unittest.mock.patch.object(T, "save_overlap_history",
                                       lambda *a, **k: ((wrote is not None and wrote.append("data/source_overlap.json")), {})[1]),
            # …and the fetch log, or driving main() writes a data/fetch_log.json
            # of stub numbers into the working tree — which daily.yml would then
            # `git add data` and commit as a real day's record
            unittest.mock.patch.object(T, "save_fetch_log",
                                       lambda *a, **k: ((wrote is not None and wrote.append("data/fetch_log.json")), {})[1]),
            unittest.mock.patch.dict(os.environ, env, clear=True),
        ]
        for p_ in patches:
            p_.start()
        try:
            buf = _io.StringIO()
            with contextlib.redirect_stdout(buf):
                try:
                    T.main()
                    code = None
                except SystemExit as e:
                    code = e.code
            # The guard PRINTS its explanation and exits with a code; the code
            # is what the workflow branches on, the text is what a human reads.
            return seen, captured, Exit(code, buf.getvalue())
        finally:
            for p_ in reversed(patches):
                p_.stop()

    def test_a_night_that_fetched_nothing_says_what_it_wrote(self):
        """The exit message is the last line in the Actions log and the whole
        body of the run-FAILED email, and it said "leaving data, report and site
        untouched" — after save_overlap_history(), save_fetch_log() and
        save_spend_history() had all run. daily.yml commits data/ on this path,
        deliberately and with a comment saying so, so the record gained a spend
        row and a full set of per-scope fetch facts under a sentence saying
        nothing moved. The report and the site really are untouched; the fix is
        the sentence, not the writes."""
        wrote = []
        yesterday = date.fromordinal(T.TODAY_ORD - 1).isoformat()
        _, captured, out = self._drive([self._hist_row(yesterday)],
                                       lambda *a: [], wrote=wrote)
        self.assertNotIn("rows", captured, "nothing was fetched, so nothing is written")
        self.assertIn("data/spend.json", wrote)
        self.assertIn("data/fetch_log.json", wrote)
        msg = str(out.code)
        self.assertNotIn("leaving data", msg,
                         f"data/ moved on this path — {sorted(set(wrote))}: {msg}")
        self.assertIn("the report and the site are untouched", msg)
        self.assertIn("fetch log and spend row are written", msg)

    # ---- the guard ----------------------------------------------------------
    def test_an_already_fetched_day_spends_nothing(self):
        seen, captured, out = self._drive([self._hist_row(T.TODAY)],
                                          lambda *a: [])
        self.assertEqual(seen, [], "the guard must refuse BEFORE the first request")
        self.assertNotIn("rows", captured, "and must not rewrite the snapshot")
        self.assertIn("already been fetched", out.text)

    def test_it_exits_a_saving_not_a_failure(self):
        """Exit 3, and daily.yml branches on it to run the free offline rebuild.

        It exited 1 first, which painted the workflow run red — and the
        operator's own reaction to seeing that was "got this error". A guard
        that saves 32 calls and reports it the same way a crash does will get
        switched off.
        """
        _, _, out = self._drive([self._hist_row(T.TODAY)], lambda *a: [])
        self.assertEqual(out.code, T.ALREADY_FETCHED)
        self.assertNotEqual(out.code, 1, "1 is a failure; this is not one")
        wf = (Path(__file__).parent.parent / ".github/workflows/daily.yml").read_text()
        self.assertIn('"$rc" = "3"', wf,
                      "the workflow must branch on the code, or it is just a red X")
        self.assertIn("rebuild_outputs.py", wf,
                      "and it must do the free thing the guard points at")

    def test_the_record_is_committed_even_when_the_run_crashes(self):
        """data/ is the half that cannot be re-made.

        snapshots.csv holds rows the listings API will never serve again, and
        spend.json is the only count of what the month has cost. Both were
        thrown away by any run that crashed after the fetch loop, because the
        commit step runs only on success — so a crash in build_outputs() burned
        up to 32 calls and left no record that it had, and the next day's
        pre-flight read a month that looked cheaper than it was.

        Asserted on the workflow, because that is where the loss was: the
        Python side already writes both files before either exit.
        """
        wf = (Path(__file__).parent.parent / ".github/workflows/daily.yml").read_text()
        steps = wf.split("      - name: ")
        record = [s for s in steps if "git add data\n" in s]
        self.assertTrue(record, "no step stages data/ on its own")
        cond = re.search(r"^\s*if: (.*)$", record[0], re.M)
        self.assertTrue(cond, "the record step must carry a condition, or it runs nowhere special")
        self.assertIn("always()", cond.group(1),
                      "the record must be committed on the failure path too — that is "
                      "the only path on which it was being lost")
        # …and NOT on the success path. A bare always() ran this step on every
        # good day as well, ahead of the outputs step, and split each snapshot
        # into two commits — "record" then "snapshot" — for a day that had lost
        # nothing. The gate has to read the tracker step's own conclusion, so
        # the step it names must exist under that id.
        self.assertIn("steps.tracker.conclusion != 'success'", cond.group(1),
                      "the record step must stand down when the tracker succeeded — "
                      "the outputs step commits data/ on that path")
        tracker = [s for s in steps if s.startswith("Run tracker\n")]
        self.assertTrue(tracker, "the tracker step is not where the gate expects it")
        self.assertTrue(re.search(r"^\s+id: tracker$", tracker[0], re.M),
                        "the gate names steps.tracker, so the run step must carry that id")
        # …and only data/. Staging the outputs unconditionally would push a
        # REPORT.md describing a docs/data.json that was never written: main()
        # writes them in that order.
        self.assertNotIn("git add data docs", record[0],
                         "an always() step must not stage the derived outputs")
        outputs = [s for s in steps if "git add data docs REPORT.md" in s]
        self.assertTrue(outputs, "the outputs are still committed on success")
        self.assertNotIn("if: always()", outputs[0])

    def test_the_spend_and_the_snapshot_are_written_before_the_outputs(self):
        """The workflow assertion above is only worth anything if the files are
        on disk by the time the crash happens."""
        import inspect
        # The statements, not any mention of them: main() carries a comment
        # naming build_outputs above the line that writes the snapshot, and
        # matching that would have this test pass on a reordering.
        lines = [l.split("#")[0].strip() for l in inspect.getsource(T.main).splitlines()]
        spend = next(i for i, l in enumerate(lines) if l.startswith("report_spend("))
        write = next(i for i, l in enumerate(lines) if l.startswith("write_rows("))
        build = next(i for i, l in enumerate(lines) if "= build_outputs(" in l)
        self.assertLess(spend, build, "the month's spend must be on disk before the outputs are built")
        self.assertLess(write, build, "and so must the snapshot")

    def test_the_message_names_the_free_way_out(self):
        _, _, out = self._drive([self._hist_row(T.TODAY)], lambda *a: [])
        self.assertIn("rebuild_outputs.py", out.text)
        self.assertIn("ALLOW_REFETCH", out.text)
        self.assertIn("Nothing is wrong", out.text,
                      "a saving must not read like a fault")

    def test_an_exhausted_query_skips_the_rest_of_the_target(self):
        """The saving that pays for the whole watchlist, and nothing ran it.

        A short page means the query returned its scope's entire market, so the
        second sort and the newest probe would re-fetch cars already in hand.
        Both guards were pinned by nothing: deleting either costs real calls
        against a 1,000-a-month tier and no test noticed.
        """
        tid = "bmw-i5-edrive40"
        full = [{"vin": f"V{i:016d}", "price": 40000 + i, "year": 2024,
                 "miles": 20000, "trim": "eDrive40", "state": "IL", "city": "Chicago"}
                for i in range(T.PER_PAGE)]
        short = full[:5]

        def batches(t, source, sort, page):
            return short if t["id"] == tid else full

        seen, _, _ = self._drive([self._hist_row("2020-01-01")], batches)
        mine = [q for q in seen if q[0] == tid]
        self.assertTrue(mine, "the target was fetched at all")
        sorts = {q[2] for q in mine}
        self.assertEqual(sorts, {"price.asc"},
                         "a short first page is the whole scope — the second sort "
                         f"would re-fetch cars already in hand, but it asked {sorts}")
        self.assertNotIn(T.NEWEST_SORT, sorts,
                         "and so would the newest probe")

    def test_a_fresh_day_is_not_blocked(self):
        """The guard must not be a wall. A day not yet in the snapshot runs."""
        seen, _, _ = self._drive([self._hist_row("2020-01-01")], lambda *a: [])
        self.assertTrue(seen, "an unfetched day must reach the API")

    def test_the_hatch_lets_a_genuine_re_run_through(self):
        seen, _, _ = self._drive([self._hist_row(T.TODAY)], lambda *a: [],
                                 allow_refetch=True)
        self.assertTrue(seen, "ALLOW_REFETCH must reach the API")

    def test_an_empty_hatch_is_not_a_hatch(self):
        """daily.yml passes ALLOW_REFETCH as `inputs.allow_refetch && '1' || ''`,
        so on every scheduled run the variable is PRESENT and empty. If the
        guard tested for presence rather than truth, the cron would open its own
        escape hatch on every single day."""
        seen, _, out = self._drive([self._hist_row(T.TODAY)], lambda *a: [],
                                   allow_refetch="")
        self.assertEqual(seen, [], "an empty value must not unlock the guard")
        self.assertIn("already been fetched", out.text)

    # ---- provenance ---------------------------------------------------------
    def _via_batches(self):
        """A full page per query so the short-page EXHAUSTED short-circuit does
        not fire and both sorts actually run — that is the path under test."""
        base = copy.deepcopy({k: v for k, v in FIXTURES["clean"].items()
                              if not k.startswith("_")})

        def make(vin, price, miles, trim):
            r = copy.deepcopy(base)
            r.setdefault("vehicle", {}).update({"vin": vin, "trim": trim})
            r["vin"] = vin
            r.setdefault("retailListing", {}).update({"price": price, "miles": miles})
            for k in ("price", "miles"):
                if k in r:
                    r[k] = r["retailListing"][k]
            return r

        def batches(t, source_name, sort, page):
            if t["id"] != "bmw-i5-edrive40":
                return []
            trim = "eDrive40"
            if sort == "price.asc":
                # BOTH0000000000001 is also the CHEAPEST, so it is the record
                # that survives dedup; PRICEONLY is unique to this sort.
                out = [make("BOTH0000000000001", 30000, 500, trim)]
                out += [make(f"PRICEONLY{page}{i:06d}", 40000 + i, 9000, trim)
                        for i in range(19)]
                return out
            if sort == "miles.asc":
                # The same car arrives again, DEARER — so rows[key] keeps the
                # price.asc record and this one is discarded. via must survive
                # that, which is the whole reason it is accumulated separately.
                out = [make("BOTH0000000000001", 99000, 500, trim)]
                out += [make(f"MILESONLY{page}{i:06d}", 41000 + i, 100 + i, trim)
                        for i in range(19)]
                return out
            return []
        return batches

    def test_via_records_every_query_that_returned_a_row(self):
        _, captured, _ = self._drive([], self._via_batches(), allow_refetch=True)
        rows = {r["vin"]: r for r in captured.get("rows", [])
                if r["target"] == "bmw-i5-edrive40"}
        self.assertIn("BOTH0000000000001", rows)
        both = rows["BOTH0000000000001"]["via"].split("|")
        self.assertEqual(sorted(both),
                         ["National:miles.asc", "National:price.asc",
                          "States:miles.asc", "States:price.asc"],
                         "a car returned by both sorts on both sources must say so")

    def test_via_survives_the_cheaper_duplicate_replacing_the_record(self):
        """rows[key] is REPLACED whenever a cheaper duplicate arrives. If via
        rode on the record, the replacement would drop the earlier query from
        its own provenance — so it is accumulated separately, and this is what
        proves that actually works rather than merely being intended."""
        _, captured, _ = self._drive([], self._via_batches(), allow_refetch=True)
        rows = {r["vin"]: r for r in captured.get("rows", [])}
        both = rows["BOTH0000000000001"]
        self.assertEqual(T.to_int(both["price"]), 30000,
                         "the cheaper price.asc record is the one kept")
        self.assertIn("miles.asc", both["via"],
                      "but the discarded miles.asc sighting is still recorded")

    def test_a_row_from_one_sort_records_only_that_sort(self):
        """Or the column says nothing: if everything reported every query, it
        could not distinguish the windows it exists to distinguish."""
        _, captured, _ = self._drive([], self._via_batches(), allow_refetch=True)
        rows = {r["vin"]: r for r in captured.get("rows", [])}
        price_only = next(v for k, v in rows.items() if k.startswith("PRICEONLY"))
        miles_only = next(v for k, v in rows.items() if k.startswith("MILESONLY"))
        self.assertNotIn("miles.asc", price_only["via"], price_only["via"])
        self.assertNotIn("price.asc", miles_only["via"], miles_only["via"])
        self.assertIn("price.asc", price_only["via"])
        self.assertIn("miles.asc", miles_only["via"])


class TestProvenance(unittest.TestCase):
    """Which query returned a row. Without it a car pushed out of the
    lowest-by-miles window cannot be told from one that left the market, which
    is why exit prices are withheld for every multi-sort target today."""

    def test_via_is_a_column(self):
        self.assertIn("via", T.FIELDS)

    def test_a_normalized_row_always_carries_it(self):
        import copy
        rec = copy.deepcopy({k: v for k, v in FIXTURES["clean"].items()
                             if not k.startswith("_")})
        row = T.normalize(rec, target("bmw-i5-m60"), Counter())
        self.assertIsNotNone(row)
        self.assertIn("via", row)

    def test_history_without_the_column_still_loads(self):
        """Every row written before today has no provenance and never will —
        it cannot be reconstructed. Loading must treat that as empty, not as
        a crash, or the whole ten-day history becomes unreadable."""
        rows = T.load_history()
        self.assertTrue(rows)
        self.assertTrue(all("via" in r for r in rows))

    def test_the_fetch_loop_accumulates_across_queries(self):
        """A row is replaced whenever a cheaper duplicate arrives. If
        provenance rode on the record, the replacement would drop the earlier
        query from its own history — so it is accumulated separately."""
        src = (Path(__file__).parent.parent / "Tracking.py").read_text()
        main = src[src.index("def main("):]
        self.assertIn("via[key].add(", main)
        self.assertLess(main.index("via = defaultdict(set)"), main.index("via[key].add("))
        self.assertIn('r["via"] = "|".join(sorted(via.get(', main)


class TestShipModel(unittest.TestCase):
    """The banded, road-factored shipping estimate.

    The flat great-circle rate it replaces was wrong in a specific direction:
    a straight line understates a route, and one per-mile rate misprices both
    ends of a cost curve whose fixed component does not scale. Both errors
    flattered distant cars.

    These tests hold the SHAPE, not the constants. The constants ship
    uncalibrated on purpose — they are published typical ranges, not quotes
    anyone obtained — so a test asserting a particular dollar figure would be
    pinning a guess and would have to be rewritten the day real quotes arrive.
    What must not drift is the shape: monotone in distance, cheaper per mile
    the further you go, never below the floor, zero in-state, and identical to
    the old behaviour when no bands are configured.
    """

    def test_drivable_is_still_free(self):
        for st in T.STATES:
            self.assertEqual(T.ship_for({"state": st, "distance": 1200}), 0,
                             "a car in a state the buyer drives to is never shipped")

    def test_cost_rises_with_distance(self):
        """Swept every mile, not sampled.

        The first version of this test checked six widely spaced distances and
        passed while the model was badly broken: rates REPLACED each other by
        band, so crossing an edge cut the estimate — 423 miles cost $574 and
        424 cost $425, making a car one mile further away $149 cheaper to bring
        home. The samples straddled all three edges without landing on one.
        A property this cheap to check exhaustively should never be sampled.
        """
        prev, drops = 0, []
        for d in range(1, 3201):
            cost = T.ship_for({"state": "CA", "distance": d})
            if cost < prev:
                drops.append((d, prev, cost))
            prev = cost
        self.assertEqual(drops, [], f"further away must never be cheaper; first drop at {drops[:1]}")

    def test_the_effective_rate_still_falls(self):
        """Monotonicity must not be bought by flattening the curve — the whole
        reason for bands is that a long haul costs less PER MILE."""
        rates = [T.ship_for({"state": "CA", "distance": d}) / d for d in (400, 900, 1600, 2600)]
        self.assertTrue(all(a > b for a, b in zip(rates, rates[1:])),
                        f"per-mile must keep falling with distance, got {[round(r, 3) for r in rates]}")

    def test_every_band_edge_is_continuous(self):
        """One mile either side of a configured edge must differ by about one
        mile's worth of money, not by a step."""
        for edge, _ in T.SHIP_BANDS:
            if edge is None:
                continue
            straight = edge / T.SHIP_ROAD_FACTOR
            lo = T.ship_for({"state": "CA", "distance": straight - 1})
            hi = T.ship_for({"state": "CA", "distance": straight + 1})
            self.assertLessEqual(hi - lo, 10,
                                 f"a step of ${hi - lo} at the {edge}-mile edge; bands must be marginal")

    def test_per_mile_falls_with_distance(self):
        """The whole reason for bands: fixed costs spread over a longer haul."""
        rates = [T.ship_for({"state": "CA", "distance": d}) / d
                 for d in (700, 1200, 2000)]
        self.assertTrue(all(a > b for a, b in zip(rates, rates[1:])),
                        f"per-mile must fall as the haul lengthens, got {rates}")

    def test_the_floor_holds(self):
        floor = T.to_float(T.BUYER.get("ship_min")) or 0
        self.assertGreaterEqual(T.ship_for({"state": "MI", "distance": 5}), floor)

    def test_road_miles_exceed_the_straight_line(self):
        """A truck does not fly. Whatever the rate, the distance it bills is
        the route, and the route is longer than the great-circle figure.

        Pinned to the factor's EXACT effect, at several distances, because the
        loose version of this test was worthless. It asserted only that
        SHIP_ROAD_FACTOR > 1.0 and that ship_for(1000) exceeded 99% of the
        straight-line band cost. Deleting the multiplication entirely — so the
        constant was still 1.18 but nothing ever used it — left ship_for(1000)
        at exactly 100% of the straight line, which cleared the 99% bar. The
        estimate changed at 2,753 of 3,001 distances and all 142 tests passed.
        Applying the factor TWICE passed too: the old assertions bounded it
        from neither side.
        """
        self.assertGreater(T.SHIP_ROAD_FACTOR, 1.0)
        for d in (200, 423, 424, 700, 1000, 1600, 2600):
            want = int(round(max(to_float_or_zero(T.BUYER.get("ship_min")),
                                 T.band_cost(d * T.SHIP_ROAD_FACTOR))))
            self.assertEqual(T.ship_for({"state": "CA", "distance": d}), want,
                             f"at {d} straight-line miles the bill must be the "
                             f"bands applied to {d} x {T.SHIP_ROAD_FACTOR} road miles")
        # And the factor must actually be USED, not merely defined: the route
        # bill is strictly above the straight-line bill wherever money is owed
        # beyond the floor.
        self.assertGreater(T.ship_for({"state": "CA", "distance": 1000}),
                           T.band_cost(1000))

    def test_no_bands_means_the_old_behaviour_exactly(self):
        """A config without ship_bands must be untouched by this change — the
        fallback is what makes the new model safe to land."""
        saved = list(T.SHIP_BANDS)
        try:
            T.SHIP_BANDS.clear()
            d, rate = 900, T.to_float(T.BUYER.get("ship_per_mile"))
            floor = T.to_float(T.BUYER.get("ship_min")) or 0
            self.assertEqual(T.ship_for({"state": "CA", "distance": d}),
                             int(round(max(floor, d * rate))))
        finally:
            T.SHIP_BANDS[:] = saved

    def test_an_unplaceable_car_falls_back_to_the_flat_cost(self):
        self.assertEqual(T.ship_for({"state": "MI"}), T.to_int(T.BUYER.get("ship_cost")))

    def test_calibration_is_absent_until_quotes_exist(self):
        """The model must not claim to be calibrated when nobody has checked
        it. buyer.ship_quotes is empty, so there is no error to report — and
        an empty calibration reads as 'unknown', never as 'zero error'."""
        self.assertIsNone(T.BUYER.get("ship_calibrated"),
                          "shipping bands ship uncalibrated; set the date when quotes are added")
        self.assertIsNone(T.ship_calibration())

    def test_quotes_without_bands_report_unknown_not_zero(self):
        """The gap a mutation found. If quotes exist but no bands are
        configured, every quote is unpriceable and no error can be computed —
        which must read as UNKNOWN. Reporting mean_error 0 there would put
        'perfectly calibrated' on a model nothing has been measured against,
        which is worse than the uncalibrated state it replaced."""
        saved_q, saved_b = T.BUYER.get("ship_quotes"), list(T.SHIP_BANDS)
        try:
            T.SHIP_BANDS.clear()
            T.BUYER["ship_quotes"] = [{"miles": 900, "price": 800, "route": "test"}]
            self.assertIsNone(T.ship_calibration(),
                              "unpriceable quotes are no measurement, not a perfect one")
        finally:
            T.SHIP_BANDS[:] = saved_b
            if saved_q is None:
                T.BUYER.pop("ship_quotes", None)
            else:
                T.BUYER["ship_quotes"] = saved_q

    def test_calibration_measures_against_the_brokers_own_mileage(self):
        saved = T.BUYER.get("ship_quotes")
        try:
            T.BUYER["ship_quotes"] = [{"miles": 900, "price": T.band_cost(900) - 100, "route": "test"}]
            cal = T.ship_calibration()
            self.assertEqual(cal["n"], 1)
            self.assertEqual(cal["mean_error"], 100, "estimate minus quote, so + means we overcharge")
        finally:
            if saved is None:
                T.BUYER.pop("ship_quotes", None)
            else:
                T.BUYER["ship_quotes"] = saved


class TestFees(unittest.TestCase):
    """Tax and paperwork — the largest number the dashboard never showed.

    At the configured rate the tax on a median car is roughly $4,600, seven
    times the median shipping estimate the page has always displayed. It is
    also the first figure here that scales with price rather than sitting at a
    few hundred dollars whatever the car costs.

    The modelling choice worth guarding is `finance_shipping`. A lender writes
    the loan against the dealer's invoice — price, tax, doc, title,
    registration — while a transport broker is a separate cash transaction
    weeks later. Financing the shipping would make every payment on the page
    slightly too high, and nothing would say why.
    """

    def test_the_block_exports_or_is_absent_cleanly(self):
        f = T.fees_export()
        if f is None:
            self.skipTest("no fees configured")
        for k in ("tax_rate", "doc_fee", "title", "registration", "ev_surcharge"):
            self.assertIsInstance(f[k], (int, float))
            self.assertGreaterEqual(f[k], 0)

    def test_every_fee_round_trips_from_the_config(self):
        """Type checks let a key collapse to zero or to its neighbour's value
        and still pass — `"tax_rate": ... or 0` is one dropped `to_float` away
        from taxing nothing, on every total the page prints. A block of
        DISTINCT synthetic values, so any collapse shows on sight, and not the
        shipped numbers: pinning 9.25% here would turn the test into a mirror
        of targets.json that has to be edited whenever the county moves."""
        block = {"tax_rate": 0.11, "tax_note": "n", "doc_fee": 311, "title": 171,
                 "registration": 155, "ev_surcharge": 123, "finance_shipping": True,
                 "checked": "2026-01-02"}
        with unittest.mock.patch.dict(T.BUYER, {"fees": block}, clear=False):
            f = T.fees_export()
        for k, v in block.items():
            self.assertEqual(f[k], v, f"{k} did not round-trip")
        self.assertIs(f["finance_shipping"], True)

    def test_the_shipped_rate_charges_something(self):
        """The one fact about the SHIPPED block worth holding without pinning
        it: a zero rate means the page quietly stopped charging tax."""
        f = T.fees_export()
        if f is None:
            self.skipTest("no fees configured")
        self.assertGreater(f["tax_rate"], 0)

    def test_shipping_is_not_financed_by_default(self):
        f = T.fees_export()
        if f is None:
            self.skipTest("no fees configured")
        self.assertFalse(f["finance_shipping"],
                         "a transport broker is not the lender; default must be False")

    def test_the_default_is_read_explicitly_not_inferred(self):
        """A config that omits finance_shipping must get the documented default,
        not whatever a missing key happens to evaluate to."""
        saved = T.BUYER.get("fees")
        try:
            T.BUYER["fees"] = {"tax_rate": 0.09}
            self.assertIs(T.fees_export()["finance_shipping"], False)
        finally:
            if saved is None:
                T.BUYER.pop("fees", None)
            else:
                T.BUYER["fees"] = saved

    def test_fees_ship_unverified(self):
        """Same discipline as the shipping bands: a tax rate is locally
        specific and changes, so it is not presented as checked until it is."""
        f = T.fees_export()
        if f is None:
            self.skipTest("no fees configured")
        self.assertIsNone(f["checked"],
                          "set fees.checked once the rate has been verified against the county")

    def test_the_note_explains_why_tax_lands_on_every_car(self):
        """Illinois taxes at the buyer's home rate wherever the car was bought.
        Without that sentence the tax on a Phoenix car reads as a bug."""
        f = T.fees_export()
        if f is None:
            self.skipTest("no fees configured")
        self.assertTrue(f["tax_note"].strip(), "the rate needs its explanation shipped beside it")


class TestExitStats(unittest.TestCase):
    """Where comparable cars stopped being advertised.

    A tool with no transaction feed will never know a sale price. What it does
    know is the last number a car asked before its listing ended, and across a
    trim that is the closest honest proxy — which is why nothing here is named
    for a sale. A delisted car may have sold, gone to auction, moved to a
    sister lot, or simply had its ad expire, and all four look identical from
    outside.

    The trap this class exists to hold shut is the one the first version fell
    into: a median PRICE CUT among departed cars came out at exactly $0 for
    every trim on the sheet. Not because these cars never discount — 9 of 94
    demonstrably did — but because half are observed on two days or fewer of a
    listing life whose median is over three weeks, and a quarter are seen
    exactly once, where a cut cannot be observed at all. That number described
    the fetch cadence and would have been read as market behaviour.
    """

    @staticmethod
    def _gone(n, price=40000, series_len=3, trim="t1", exact=True):
        # `exact` is what delisted() writes down about how it reached the
        # label: True when a query actually looked and did not find the car.
        # These rows say True because this class is about the ARITHMETIC over
        # defensible departures; the gate itself is tested separately, in
        # TestDepartureEvidence.
        return [{"likely": "delisted", "trim_id": trim, "last_price": price,
                 "exact": exact,
                 "first_seen": "2026-08-01", "last_seen": "2026-08-15",
                 "listed_since": "2026-08-01",
                 "series": [["2026-08-0%d" % (i + 1), price] for i in range(series_len)]}
                for _ in range(n)]

    def test_no_median_cut_is_published(self):
        """The specific number that was wrong. If it comes back, this fails."""
        st = T.sale_stats(self._gone(10))
        self.assertNotIn("median_exit_cut", st,
                         "a median cut over a 2-day observation window describes the cadence, not the market")

    def test_a_cut_is_counted_only_where_it_could_be_seen(self):
        """Cars seen once cannot show a cut, so they must not sit in the
        denominator and quietly drag the rate toward zero."""
        once = self._gone(4, series_len=1)
        twice = self._gone(6, series_len=2)
        st = T.sale_stats(once + twice)
        self.assertEqual(st["exit_watched"], 6, "only multi-observation cars can be watched for a cut")
        self.assertEqual(st["n_exits"], 10, "but every departure still counts as an exit price")

    def test_a_real_cut_is_counted(self):
        g = self._gone(1, price=38000, series_len=2)
        g[0]["series"] = [["2026-08-01", 40000], ["2026-08-02", 38000]]
        self.assertEqual(T.sale_stats(g)["exit_cut_while_watched"], 1)

    def test_out_of_window_departures_are_excluded(self):
        """A car that fell out of the price window did not leave the market."""
        g = self._gone(6) + [{"likely": "out of window", "trim_id": "t1",
                              "last_price": 1, "series": [["2026-08-01", 1]]}]
        self.assertEqual(T.sale_stats(g)["n_exits"], 6)

    def test_a_thin_cohort_publishes_nothing(self):
        """Two departures make a median that swings by thousands on the third.
        Below the floor the trim ships no exit stats at all rather than a
        number a reader would reasonably trust."""
        self.assertEqual(T.exit_stats(self._gone(5), "t1"), {})
        self.assertTrue(T.exit_stats(self._gone(6), "t1"))
        # Six is not a preference. Five is the largest n at which NO
        # distribution-free interval for a median exists — even min-to-max
        # covers 93.75% — so a median of five has no honest error bar.
        self.assertIsNone(T.median_ci([1, 2, 3, 4, 5]))
        self.assertIsNotNone(T.median_ci([1, 2, 3, 4, 5, 6]))

    def test_the_median_carries_its_own_interval(self):
        """A reference price with no error bar invites a comparison it cannot
        support. exit_lo/exit_hi ship so the page can suppress a note whose gap
        is smaller than the median's own sampling error — which 129 of the 310
        notes it drew under the old flat $500 threshold were."""
        st = T.exit_stats(self._gone(12, price=40000), "t1")
        self.assertIsNotNone(st["exit_lo"])
        self.assertIsNotNone(st["exit_hi"])
        self.assertLessEqual(st["exit_lo"], st["exit_price"])
        self.assertLessEqual(st["exit_price"], st["exit_hi"])

    def test_the_interval_is_the_order_statistics_not_a_normal_curve(self):
        """Exit prices are skewed and small-n. A normal-theory interval on
        eight of them would be a worse lie than none, so the interval is
        distribution-free: it is a pair of the observed values themselves."""
        xs = [10, 20, 30, 40, 50, 60, 70, 80, 90, 900, 1000, 2000]
        lo, hi = T.median_ci(xs)
        self.assertIn(lo, xs)
        self.assertIn(hi, xs)
        # n=12 -> the 3rd and 10th order statistics (96.1% coverage)
        self.assertEqual((lo, hi), (sorted(xs)[2], sorted(xs)[9]))
        # …and the CONFIDENCE itself, which nothing pinned: at n=30 the 0.95
        # interval is the 10th and 21st order statistics, and a 0.90 one is the
        # 11th and 20th. Without this the default could be quietly widened or
        # narrowed and every published interval with it.
        self.assertEqual(T.median_ci(list(range(30))), (9, 20))

    def test_the_interval_widens_as_the_sample_thins(self):
        wide = T.median_ci(list(range(100)))
        narrow = T.median_ci(list(range(1000)))
        self.assertLess((narrow[1] - narrow[0]) / 1000, (wide[1] - wide[0]) / 100)

    def test_a_two_sort_target_publishes_no_exit_price(self):
        """The bug this gate exists for was live on the published sheet.

        bmw-i7-edrive50 fetches price.asc AND miles.asc, but delisted()
        reconstructs one cut-off on one axis pooled over both. The miles.asc
        rows are $115k-$125k delivery-mileage cars, so they lifted the
        reconstructed PRICE ceiling above every car in the set and nothing
        could be judged out of window. Eight 1-to-4-mile 2026 cars that had
        merely been pushed out of the lowest-by-miles window became
        "delistings", their median became a published exit_price of $118,834,
        and the dashboard told the reader a $54,000 i7 was "$64,834 below
        where this trim's listings ended".
        """
        # The rows must carry the target's OWN id. They used to be stamped
        # "t1", so exit_stats filtered to zero matching rows and returned {} at
        # the n < floor check before the gate was ever evaluated: deleting the
        # gate left the whole suite green, and rebuilding under that mutant
        # republished exit prices for the two-sort trims.
        real = [t for t in T.TARGETS.values() if len(T.sorts_pages(t)[0]) > 1 or t.get("newest")]
        self.assertTrue(real, "the watchlist should still have a two-window target")
        for t in real:
            self.assertFalse(T.departures_are_separable(t), t["id"])
            self.assertEqual(T.exit_stats(self._gone(20, trim=t["id"]), t["id"]), {},
                             f"{t['id']} fetches {T.sorts_pages(t)[0]} plus {t.get('newest') or 0} "
                             "newest page(s) — its departures cannot be told apart from window "
                             "churn, so it must publish no exit price")
        # …and the gate, not the floor, is what does that: the same twenty
        # departures on a one-window target DO produce a median.
        one = next(t for t in T.TARGETS.values() if T.departures_are_separable(t))
        self.assertTrue(T.exit_stats(self._gone(20, trim=one["id"]), one["id"]),
                        f"{one['id']} opens one window, so its exits are publishable")

    def test_which_targets_can_be_told_apart_is_a_fact_about_the_fetch(self):
        """`sorts` is the CONFIGURED list; sorts_pages() is what a run asks for.

        Eleven of the fourteen targets are `light` depth: they carry the
        two-sort default in config and fetch only the first of it. Reading the
        config called them two-window targets and withheld an exit price from
        every one of them for a reason that does not apply to them — while the
        two trims that really do open two windows are the shopped ones, where
        the withholding matters most and still holds.
        """
        light = T.TARGETS["bmw-i5-m60"]
        self.assertEqual(light["depth"], "light")
        self.assertEqual(len(light["sorts"]), 2, "config still names two sorts")
        self.assertEqual(T.sorts_pages(light)[0], ["price.asc"], "one is fetched")
        self.assertTrue(T.departures_are_separable(light))
        for tid in ("bmw-i5-edrive40", "bmw-i7-edrive50"):
            t = T.TARGETS[tid]
            self.assertEqual(len(T.sorts_pages(t)[0]), 2)
            self.assertTrue(t["newest"])
            self.assertFalse(T.departures_are_separable(t),
                             f"{tid} really does open two windows plus a newest probe")

    def test_the_published_sheet_carries_no_exit_price_it_cannot_defend(self):
        sheet = json.loads((Path(__file__).parent.parent / "docs/data.json").read_text())
        bad = []
        for b in (sheet.get("brands") or {}).values():
            for m in (b.get("models") or {}).values():
                for tid, tr in (m.get("trims") or {}).items():
                    t = T.TARGETS.get(tid) or {}
                    if tr.get("exit_price") and not T.departures_are_separable(t):
                        bad.append(tid)
        self.assertEqual(bad, [], f"these ship an exit price built on unseparable departures: {bad}")

    def test_departures_themselves_are_still_reported(self):
        """Withholding the exit PRICE is not withholding the departures. "This
        stopped being listed" is true whatever pushed it out, and the gone list
        labels each one; only the dollar claim built on top is withheld."""
        sheet = json.loads((Path(__file__).parent.parent / "docs/data.json").read_text())
        gone = [g for b in (sheet.get("brands") or {}).values()
                for m in (b.get("models") or {}).values()
                for g in (m.get("gone") or [])]
        self.assertGreater(len(gone), 50)
        self.assertTrue({g.get("likely") for g in gone} >= {"delisted", "out of window"})

    def test_a_pooled_cohort_is_not_one_trim(self):
        """one_trim asks the DATA, not the watchlist. The first version counted
        watchlist targets, which inverts the test: a catch-all target like
        `kia-ev9` is ONE entry covering the whole model, so it scored True
        while pooling a Light with a GT-Line, and `audi-a6-etron` scored True
        over a cohort spanning $28,077. The gate passed on exactly the
        cohorts it existed to stop."""
        mixed = self._gone(6, price=40000, trim="cheap") + self._gone(6, price=120000, trim="dear")
        self.assertFalse(T.one_cohort(mixed))
        self.assertTrue(T.one_cohort(self._gone(6, price=40000, trim="only")))

    def test_stats_are_scoped_to_one_trim(self):
        """A median mixing an eDrive50 with an M70 describes no car that exists."""
        mixed = self._gone(6, price=40000, trim="cheap") + self._gone(6, price=120000, trim="dear")
        self.assertEqual(T.exit_stats(mixed, "cheap")["exit_price"], 40000)
        self.assertEqual(T.exit_stats(mixed, "dear")["exit_price"], 120000)

    def test_the_report_never_calls_a_delisting_a_sale(self):
        """EVERY reader-facing surface, not just the one that was fixed.

        The first version of this sliced market_line() out of Tracking.py and
        checked that. It passed while docs/index.html shipped the banned string
        verbatim — `sold cars lasted ~15d (38 sold)` — to five of seven models,
        on the page that is the PRIMARY surface. The test's name promised the
        report and it read one function. So it now reads every file a reader
        can see, and it looks for the claim rather than one phrasing of it.
        """
        root = Path(__file__).parent.parent
        # Shapes of the CLAIM, not the word. Prose may reason about selling —
        # "pushed out rather than sold" is honest and belongs in a docstring —
        # so what is banned is a departure COUNT or DURATION presented as a
        # sale, which is what actually reached the reader.
        banned = {
            r"sold cars lasted": "calls departures sales outright",
            r"\bsold\b[^\n]{0,24}\blasted\b": "attributes a duration to sales",
            r"\}\s*sold\b": "renders a count as 'N sold'",
            r"\b\d+\s+sold\b": "states a number of cars sold",
            r"\bsold in ~": "claims a time to sale",
        }
        # Field NAMES may say sale — they ship in data.json and renaming them
        # breaks every sheet already stored. Only rendered text is a claim.
        ident = re.compile(r"median_days_to_sale|n_sold|days_to_sale")
        for name in ("Tracking.py", "docs/index.html", "docs/how.html", "README.md"):
            prose = ident.sub("", (root / name).read_text())
            for pat, why in banned.items():
                hit = re.search(pat, prose, re.I)
                self.assertIsNone(hit, f"{name} {why}: {hit.group(0) if hit else ''!r}")

    def test_the_page_and_the_report_use_the_same_floors(self):
        """Two surfaces, one rule. The report raised its cut-count floor to 12
        and its bare-median floor to 12 during the audit; the dashboard kept 5
        and went on printing exactly the string the report had just retired —
        `3 of 7 cut before going`, the case the comment calls indefensible.
        A floor that lives in two files drifts, so this pins them together.
        """
        page = (Path(__file__).parent.parent / "docs/index.html").read_text()
        self.assertNotIn("watched >= 5", page,
                         "the dashboard must not publish a cut fraction at n=5")
        self.assertIn("watched >= 12", page,
                      "the dashboard's cut floor must match market_line's")
        src = (Path(__file__).parent.parent / "Tracking.py").read_text()
        self.assertIn("watched >= 12", src)


class TestExitReporting(unittest.TestCase):
    """What the REPORT is allowed to say about departures.

    Two findings from the audit of this feature, both the same species: a
    number that is arithmetically fine and rhetorically false.

    The pooled median mixes trims. exit_stats() refuses to compute one per
    model for exactly that reason — an eDrive50 averaged with an M70 describes
    no car anyone can buy — and then market_line() published one anyway.

    The cut fraction invites a comparison it cannot carry. "3 of 7" has a
    confidence interval running from roughly a tenth to four fifths; set beside
    "3 of 33" it reads as a finding about two markets when it is a finding
    about two sample sizes.
    """

    @staticmethod
    def _stats(**kw):
        base = {"median_days_listed": 20, "median_exit_price": 50000,
                "n_exits": 30, "n_sold": 30, "median_days_to_sale": 15,
                "exit_watched": 20, "exit_cut_while_watched": 3, "one_trim": True}
        base.update(kw)
        return base

    def test_a_multi_trim_model_publishes_no_pooled_exit_median(self):
        line = T.market_line(self._stats(one_trim=False))
        self.assertNotIn("last ask", line,
                         "pooling an eDrive50 with an M70 describes no car that exists")

    def test_a_single_trim_model_does(self):
        self.assertIn("last ask", T.market_line(self._stats(one_trim=True)))

    def test_a_thin_cut_denominator_is_withheld(self):
        line = T.market_line(self._stats(exit_watched=7, exit_cut_while_watched=3))
        self.assertIn("last ask", line, "the median itself is still fine")
        self.assertNotIn("cut in the days", line,
                         "3 of 7 is a sample size, not a market rate")

    def test_a_real_denominator_is_published(self):
        self.assertIn("cut in the days",
                      T.market_line(self._stats(exit_watched=20, exit_cut_while_watched=3)))

    def test_the_denominator_floor_is_pinned_at_its_own_boundary(self):
        """7 against 20 is a gap a mutant walks through: any floor between 8
        and 20 passes both of the tests above. The floor is 12, so the pair
        that pins it is 11 and 12 — and the only other thing holding that
        literal was a grep over the source."""
        self.assertNotIn("cut in the days",
                         T.market_line(self._stats(exit_watched=11, exit_cut_while_watched=3)))
        self.assertIn("cut in the days",
                      T.market_line(self._stats(exit_watched=12, exit_cut_while_watched=3)))

    def test_the_wording_never_claims_a_sale(self):
        line = T.market_line(self._stats())
        for word in ("sold", "sale"):
            self.assertNotIn(word, line.lower(),
                             "a listing ending is not a confirmed sale")


# --------------------------------------------------------------------------
# The sheet's shape is the builder's promise, and the page no longer papers
# over a sheet without it.
# --------------------------------------------------------------------------
class TestTheSheetCarriesWhatThePageStoppedInventing(unittest.TestCase):
    def test_every_row_carries_a_boolean_local_and_the_sheet_a_buyer(self):
        """docs/index.html's adapt() used to synthesise a buyer block from
        per-market params and derive `local` from a `markets` list — for a
        data.json no Tracking.py revision ever wrote. Those branches are gone;
        this pins the producer, so a sheet without a buyer or a row without a
        boolean local is a builder regression the suite sees, not a shape the
        page quietly repairs."""
        def row(vin, day, price, state):
            r = {k: "" for k in T.FIELDS}
            r.update({"target": "bmw-i5-edrive40", "vin": vin, "snapshot_date": day,
                      "price": price, "year": "2024", "trim": "eDrive40", "miles": 20000,
                      "state": state, "city": "Chicago" if state == "IL" else "Plano"})
            return r
        yday = date.fromordinal(T.TODAY_ORD - 1).isoformat()
        rows = [row("L" * 17, yday, 45000, "IL"), row("L" * 17, T.TODAY, 45000, "IL"),
                row("F" * 17, yday, 44000, "TX"), row("G" * 17, yday, 43000, "TX")]   # G leaves today
        today = [r for r in rows if r["snapshot_date"] == T.TODAY]
        _, site, _ = T.build_outputs(today, rows, T.build_history(rows))
        self.assertIsInstance(site.get("buyer"), dict)
        self.assertTrue(site["buyer"].get("states"), "the buyer block names the drivable states")
        seen = 0
        for b in site["brands"].values():
            for m in b["models"].values():
                for x in m.get("listings", []) + m.get("gone", []):
                    self.assertIsInstance(x.get("local"), bool, f"{x.get('vin')}: local must be a boolean, not derived on the page")
                    seen += 1
        self.assertGreaterEqual(seen, 2, "live and gone rows both checked")


# --------------------------------------------------------------------------
# The snapshot commit step, replayed with git alone. The listings API is
# live-only, so a push that fails to land is a day the record loses.
# --------------------------------------------------------------------------
class TestTheSnapshotPushSurvivesAnOwnerRebuild(unittest.TestCase):
    """daily.yml rebases onto whatever landed on main during the run and
    retries, and used to say the tracker's outputs never conflict with source
    edits. They do: an owner who edits targets.json and runs
    tools/rebuild_outputs.py — exactly what the exit-3 guard tells them to do
    — commits the same REPORT.md and docs/data.json the tracker commits, the
    rebase stops in both, the -e shell aborts mid-rebase, and the day's
    snapshot never reaches main. The step now resolves those files in favour
    of the tracker's regeneration inside the loop. Replayed here with a bare
    remote, an owner clone and a runner clone, running the step's own script:
    the push must land, the outputs must be the tracker's, and the owner's
    source edit — which a `reset --soft` would have reverted silently — must
    survive."""

    def _git(self, cwd, *args, env=None):
        import subprocess
        e = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x", **(env or {})}
        return subprocess.run(["git", *args], cwd=cwd, env=e, capture_output=True, text=True, check=True).stdout

    def _step_script(self, name):
        """The named step's `run: |` block, read from the workflow as text.
        No YAML parser: the suite runs on the CI image's bare Python plus
        requests, and a dependency for one test is not worth the install."""
        text = (Path(__file__).parent.parent / ".github/workflows/daily.yml").read_text()
        head = text.index(f"- name: {name}\n")
        run = text.index("run: |\n", head) + len("run: |\n")
        lines = text[run:].split("\n")
        indent = len(lines[0]) - len(lines[0].lstrip(" "))
        body = []
        for ln in lines:
            if ln.strip() == "":
                body.append("")
                continue
            if len(ln) - len(ln.lstrip(" ")) < indent:
                break
            body.append(ln[indent:])
        return "\n".join(body).rstrip() + "\n"

    def _replay(self, step, owner_files, runner_files):
        import subprocess, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bare, seed, owner, runner = tmp / "remote.git", tmp / "seed", tmp / "owner", tmp / "runner"
            self._git(tmp, "init", "--bare", "-b", "main", str(bare))
            self._git(tmp, "init", "-b", "main", str(seed))
            for rel, text in {"REPORT.md": "seed report\n", "docs/data.json": "{\"seed\": 1}\n",
                              "data/snapshots.csv": "a,b\n1,2\n", "targets.json": "{\"picks\": 4}\n"}.items():
                (seed / rel).parent.mkdir(parents=True, exist_ok=True); (seed / rel).write_text(text)
            self._git(seed, "add", "."); self._git(seed, "commit", "-qm", "seed")
            self._git(seed, "remote", "add", "origin", str(bare)); self._git(seed, "push", "-q", "-u", "origin", "main")
            self._git(tmp, "clone", "-q", str(bare), str(runner))      # the runner checks out BEFORE the owner's push
            self._git(tmp, "clone", "-q", str(bare), str(owner))
            for rel, text in owner_files.items():
                (owner / rel).write_text(text)
            self._git(owner, "add", "."); self._git(owner, "commit", "-qm", "owner: config change + rebuild")
            self._git(owner, "push", "-q", "origin", "main")
            for rel, text in runner_files.items():
                (runner / rel).write_text(text)
            r = subprocess.run(["bash", "-e", "-c", self._step_script(step)], cwd=runner,
                               capture_output=True, text=True,
                               env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"})
            check = tmp / "check"
            self._git(tmp, "clone", "-q", str(bare), str(check))
            return r, {rel: (check / rel).read_text() for rel in ("REPORT.md", "docs/data.json", "data/snapshots.csv", "targets.json")}

    def test_the_outputs_step_lands_the_snapshot_and_keeps_the_owners_edit(self):
        r, main = self._replay("Commit outputs",
            owner_files={"targets.json": "{\"picks\": 6}\n", "REPORT.md": "owner rebuild\n", "docs/data.json": "{\"owner\": 1}\n"},
            runner_files={"REPORT.md": "tracker report\n", "docs/data.json": "{\"tracker\": 1}\n", "data/snapshots.csv": "a,b\n1,2\n3,4\n"})
        self.assertEqual(r.returncode, 0, f"the step must land the snapshot, not abort mid-rebase:\n{r.stdout}\n{r.stderr}")
        self.assertEqual(main["REPORT.md"], "tracker report\n", "the tracker's regeneration wins the conflict")
        self.assertEqual(main["docs/data.json"], "{\"tracker\": 1}\n")
        self.assertEqual(main["data/snapshots.csv"], "a,b\n1,2\n3,4\n", "the day's rows reached main")
        self.assertEqual(main["targets.json"], "{\"picks\": 6}\n",
                         "the owner's source edit survives — a reset --soft would have reverted it while reporting success")

    def test_a_conflict_in_the_record_itself_still_aborts_loudly(self):
        """Only the two regenerated outputs are resolved. data/ conflicting
        means two fetches of one day landed — a thing to see, not to settle
        on a runner — so the step still aborts and the safety-net artifact
        keeps the snapshot."""
        r, main = self._replay("Commit outputs",
            owner_files={"data/snapshots.csv": "a,b\n1,2\n9,9\n"},
            runner_files={"REPORT.md": "tracker report\n", "docs/data.json": "{\"tracker\": 1}\n", "data/snapshots.csv": "a,b\n1,2\n3,4\n"})
        self.assertNotEqual(r.returncode, 0, "a conflicting ledger must not be resolved silently")
        self.assertEqual(main["data/snapshots.csv"], "a,b\n1,2\n9,9\n", "and main is left as the owner pushed it")


# --------------------------------------------------------------------------
# The committed record says what the page says.
# --------------------------------------------------------------------------
class TestTheRecordSaysWhatThePageSays(unittest.TestCase):
    """REPORT.md and the dashboard read the same data.json and print the same
    claims, so a rule enforced on one surface and not the other is a file that
    disagrees with itself. Every case here was a real disagreement found by
    reading the two side by side on 2026-09-05, and each one is a place where
    the page was already careful and the record was not.

    The house rule is that a number is published with the denominator that
    makes it true, or not published. These fixes are that rule applied to the
    surface nobody was reading."""

    def _car(self, vin, prices, **kw):
        d0 = T.TODAY_ORD - len(prices)
        return {**listing(price=prices[-1], vin=vin, city="Chicago", state="IL"),
                "series": [[date.fromordinal(d0 + i).isoformat(), p]
                           for i, p in enumerate(prices)],
                "days_tracked": len(prices), "vin": vin,
                # the two the builder stamps beside the series, so a fixture
                # here is a car market_stats can actually count
                "cuts": sum(1 for a, b in zip(prices, prices[1:]) if b < a),
                "delta": prices[-1] - prices[0], **kw}

    def _rows(self, cars):
        return {c["vin"]: {**{k: "" for k in T.FIELDS}, "vin": c["vin"],
                           "price": c["price"], "miles": 20000, "year": "2024",
                           "trim": "T", "city": "Chicago", "state": "IL",
                           "target": "t"} for c in cars}

    # -- a sawtooth is not a price change, on either surface ----------------

    def test_a_car_seen_at_two_prices_is_not_a_price_change(self):
        """It is out of the cut share, out of the median, and out of the
        "asks less than when first seen" count already. Left in the price
        change tally it made one clause of the brief contradict the rest of
        the same sentence: 37 price changes over a paragraph whose every
        other figure had set those cars aside."""
        cars = [self._car("A" * 17, [50000, 49000]),
                self._car("B" * 17, [50000, 51000]),
                self._car("C" * 17, [54999, 55849, 54999, 55849]),
                # …and one that is flat at its second price today. It is a
                # sawtooth car, and it did NOT move between the last two
                # fetches, so it was never in the count this clause subtracts
                # from. Counting it would print "2 more" where one was lost —
                # which is exactly what shipped: 10 on a model where four had
                # moved, 15 on one where eight had.
                self._car("D" * 17, [54999, 55849, 54999, 55849, 55849])]
        out = T.brief_lines({"daily": [], "gone": [], "as_of": T.TODAY}, cars, "2026-09-04")
        line = next(l for l in out if "price change" in l)
        self.assertIn("2 price changes", line,
                      "the cut and the raise count; the sawtooth does not")
        self.assertIn("(1 more moved, seen at two prices, not counted)", line,
                      "one sawtooth car moved and was set aside; the flat one "
                      "was never in the figure to be subtracted from it")

    def test_the_note_is_absent_when_there_is_nothing_to_note(self):
        """Saying "0 more seen at two prices" on a clean pool is noise, and a
        clause that always fires stops being read."""
        cars = [self._car("A" * 17, [50000, 49000])]
        line = next(l for l in T.brief_lines({"daily": [], "gone": [], "as_of": T.TODAY}, cars, "2026-09-04")
                    if "price change" in l)
        self.assertIn("1 price change ·", line)
        self.assertNotIn("not counted", line)

    def test_the_price_change_block_leaves_the_sawtooth_out_too(self):
        """The brief's count and the section it points at must agree, or the
        reader who follows the number down the page finds a different set."""
        cars = [self._car("A" * 17, [50000, 49000]),
                self._car("C" * 17, [54999, 55849, 54999, 55849])]
        sec = []
        T.trim_detail(sec, {"id": "t", "label": "T", "note": "", "years": [2024]},
                      cars, self._rows(cars), {}, [], "2026-09-04", T.TODAY)
        i = sec.index("**Price changes**")
        block = "\n".join(sec[i:sec.index("", i)])
        self.assertIn("A" * 17, block, "the real cut is listed")
        self.assertNotIn("C" * 17, block,
                         "and the car that has been $54,999 and $55,849 all "
                         "fortnight is not a price change today")

    # -- the overflow line counts what it holds -----------------------------

    def test_the_bullets_and_the_overflow_add_up_to_the_cuts_there_were(self):
        """One total split in two, and only the sentence was pinned. The number
        of bullets printed and the number the line overflows FROM are two
        separate 3s in build_today(); the three that compute the sentence are
        each held by a test and the one that decides how many bullets go on
        screen by none. Four bullets over "…and 2 more" is 6 claimed against 5.
        Read off the rendered section rather than from the constant, so the
        check is the reader's own arithmetic."""
        import re
        for n in (1, 3, 4, 7):
            cuts = [{"x": {"vin": f"{i}" * 17, "price": 50000, "city": "Chicago",
                           "state": "IL", "local": False},
                     "label": "T", "amount": 500 + i, "shopping": 1} for i in range(n)]
            sec, _ = T.build_today({"cuts": cuts, "new": [], "gone": []}, T.TODAY)
            text = "\n".join(sec)
            bullets = len(re.findall(r"^- ▼ \$[\d,]+ cut · ", text, re.M))
            more = re.search(r"…and (\d+) more cuts? today", text)
            rest = int(more.group(1)) if more else 0
            self.assertEqual(bullets + rest, n,
                             f"{n} cuts: {bullets} bullets over "
                             f"{'a line saying ' + str(rest) if more else 'no overflow line'}"
                             f"\n{text}")
            self.assertLessEqual(bullets, 3, "and the section never grows past three")

    def test_the_overflow_line_counts_cuts_because_cuts_are_what_it_holds(self):
        """events["cuts"] takes only downward steps. The line under it said
        "N more price changes", and on the day this was found it headlined 53
        cuts while the sections below listed 64 price-change rows, 28 of them
        upward moves the line had never counted. Either the word or the list
        had to change, and the word was the one that was wrong: the three
        bullets it overflows from are cuts."""
        cuts = [{"x": {"vin": f"{i}" * 17, "price": 50000, "city": "Chicago",
                       "state": "IL", "local": False},
                 "label": "T", "amount": 500 + i, "shopping": 1} for i in range(5)]
        sec, _ = T.build_today({"cuts": cuts, "new": [], "gone": []}, T.TODAY)
        text = "\n".join(sec)
        self.assertIn("2 more cuts today", text)
        self.assertNotIn("more price change", text)

    def test_the_overflow_line_says_one_cut_for_one(self):
        cuts = [{"x": {"vin": f"{i}" * 17, "price": 50000, "city": "Chicago",
                       "state": "IL", "local": False},
                 "label": "T", "amount": 500 + i, "shopping": 1} for i in range(4)]
        sec, _ = T.build_today({"cuts": cuts, "new": [], "gone": []}, T.TODAY)
        self.assertIn("1 more cut today", "\n".join(sec))

    def test_the_overflow_line_promises_only_the_sections_that_exist(self):
        """"…listed in the sections below" was false for 23 of the 50 it
        counted. events["cuts"] is accumulated over every model fetched today,
        and only the SHOPPED models get "### trim" sections — the watchlist's
        other five are one line each under "## Comparison", which lists no cuts
        at all. The reader who went looking found nothing."""
        cuts = [{"x": {"vin": f"{i}" * 17, "price": 50000, "city": "Chicago",
                       "state": "IL", "local": False},
                 "label": "T", "amount": 900 - i, "shopping": 1 if i < 4 else 0}
                for i in range(7)]
        sec, _ = T.build_today({"cuts": cuts, "new": [], "gone": []}, T.TODAY)
        text = "\n".join(sec)
        self.assertIn("4 more cuts today, 1 of them listed in the sections below", text,
                      "three of the four biggest are shown, so one shopped cut "
                      "is left in the overflow and three unshopped ones are not")

    def test_the_overflow_line_drops_the_clause_when_every_cut_is_shopped(self):
        """The common case says the plain thing — a count qualified when it
        needs no qualification is a count the reader stops trusting."""
        cuts = [{"x": {"vin": f"{i}" * 17, "price": 50000, "city": "Chicago",
                       "state": "IL", "local": False},
                 "label": "T", "amount": 500 + i, "shopping": 1} for i in range(5)]
        text = "\n".join(T.build_today({"cuts": cuts, "new": [], "gone": []}, T.TODAY)[0])
        self.assertIn("2 more cuts today, listed in the sections below", text)
        self.assertNotIn("of them", text)

    def test_the_overflow_line_says_so_when_no_section_covers_it(self):
        """All four biggest are shopped, so the whole overflow is on models the
        record only summarises. Pointing at sections that hold none of them is
        the failure this replaces."""
        cuts = [{"x": {"vin": f"{i}" * 17, "price": 50000, "city": "Chicago",
                       "state": "IL", "local": False},
                 "label": "T", "amount": 900 - i, "shopping": 1 if i < 3 else 0}
                for i in range(6)]
        text = "\n".join(T.build_today({"cuts": cuts, "new": [], "gone": []}, T.TODAY)[0])
        self.assertIn("3 more cuts today on models the sections below do not cover", text)

    # -- the stale tag names what it compared against -----------------------

    def test_the_stale_share_carries_the_pool_it_is_a_share_of(self):
        """"Sits longer than 75% of the model" is a claim about the model.
        The share is computed against the cars carrying a listing date — 85 of
        the i5's 134 — so it is a claim about the dated part, and the page has
        printed the split since the days clause was built."""
        cars = [self._car(f"{i}" * 17, [50000], days_listed=d)
                for i, d in enumerate([5, 10, 20, 60])]
        cars += [self._car("E" * 17, [50000]), self._car("F" * 17, [50000])]
        for c in cars[4:]:
            c["days_listed"] = None
        T.market_stats(cars)
        self.assertEqual(cars[3]["stale_pct"], 0.75)
        self.assertEqual(cars[3]["stale_of"], 4,
                         "four cars had a date to compare against, not six")
        row = {**{k: "" for k in T.FIELDS}, "vin": "D" * 17, "price": 50000,
               "miles": 20000, "year": "2024", "trim": "T", "city": "Chicago",
               "state": "IL", "target": "t"}
        out = T.fmt_row(row, {}, T.TODAY, cars[3])
        # "the model's", because market_stats runs once per model over every
        # listing on it while the page's market sentence is recomputed for the
        # rows in view. On a trim view the two sat one line apart saying 92 and
        # 113, both true, reading as a contradiction.
        self.assertIn("sits longer than 75% of the model's 4 dated cars", out)

    def test_an_undated_pool_falls_back_to_the_word_it_can_defend(self):
        """No dated cars means no stale_pct at all, but an entry carried over
        from an older data.json can hold the share without the count. The tag
        says "the model" then — vaguer, and true."""
        entry = {"stale_pct": 0.8}
        row = {**{k: "" for k in T.FIELDS}, "vin": "D" * 17, "price": 50000,
               "miles": 20000, "year": "2024", "trim": "T", "city": "Chicago",
               "state": "IL", "target": "t"}
        self.assertIn("80% of the model", T.fmt_row(row, {}, T.TODAY, entry))

    # -- the typical-days clause borrows the page's floor and denominator ---

    def test_the_typical_days_clause_waits_for_twelve_dated_cars(self):
        """The dashboard has withheld this median under twelve dated cars
        since the days split was built: below that the median moves by days
        when one car is added. The record printed it from one."""
        cars = [self._car(f"{i:017d}", [50000], days_listed=10 + i) for i in range(11)]
        st = T.market_stats(cars)
        self.assertEqual(st["dated"], 11)
        self.assertIsNotNone(st["median_days_listed"])
        self.assertNotIn("on market", T.market_line(st),
                         "eleven dated cars is not enough to publish a typical")

    def test_the_typical_days_clause_carries_its_denominator(self):
        """And at twelve it says twelve of how many — the number that tells
        the reader the median speaks for two thirds of the pool or for a
        tenth of it."""
        cars = [self._car(f"{i:017d}", [50000], days_listed=10 + i) for i in range(12)]
        cars += [self._car(f"U{i:016d}", [50000], days_listed=None) for i in range(8)]
        st = T.market_stats(cars)
        line = T.market_line(st)
        self.assertIn("typical car 15d on market (12 of 20 dated)", line)

    # -- two markets, two medians ------------------------------------------

    def test_the_typical_days_figure_splits_the_two_markets(self):
        """Under a hundred miles a car is dealer stock — a demo, a loaner, a
        press car — reaching the used-listings API beside the used cars at a
        different price. The i7's "typical car 29d" is 49 stock cars at a
        median 78 days blended with 61 used at 21, and 29 is a number no car
        on either side sits at. The page has printed the split since the days
        clause was built and the record printed the blend alone."""
        cars = [self._car(f"S{i:016d}", [50000], days_listed=70 + i, miles=50) for i in range(12)]
        cars += [self._car(f"U{i:016d}", [50000], days_listed=10 + i, miles=30000) for i in range(12)]
        st = T.market_stats(cars)
        self.assertEqual(st["days_split"],
                         {"stock": {"n": 12, "days": 75}, "used": {"n": 12, "days": 15},
                          "none": 0})
        self.assertIn("typical car 45d on market (24 of 24 dated) — "
                      "12 dealer stock at 75d, 12 used at 15d", T.market_line(st))

    def test_one_thin_side_leaves_the_blend_to_stand_alone(self):
        """Twelve each side, the same floor the bare median already answers to.
        Eleven stock cars is not a market with a typical, and saying so of one
        side while the other is solid would be worse than saying nothing."""
        cars = [self._car(f"S{i:016d}", [50000], days_listed=70 + i, miles=50) for i in range(11)]
        cars += [self._car(f"U{i:016d}", [50000], days_listed=10 + i, miles=30000) for i in range(20)]
        st = T.market_stats(cars)
        self.assertIsNone(st["days_split"])
        line = T.market_line(st)
        self.assertIn("typical car", line)
        self.assertNotIn("dealer stock", line)

    def test_a_hundred_miles_is_where_the_two_markets_part(self):
        """The threshold is the rule, not a detail: 99 miles is a demo or a
        loaner priced near sticker, 100 is a used car. docs/index.html carries
        the same number, and a drift between them would have the page and the
        record splitting the same pool two different ways."""
        cars = [self._car(f"S{i:016d}", [50000], days_listed=70 + i, miles=99) for i in range(12)]
        cars += [self._car(f"U{i:016d}", [50000], days_listed=10 + i, miles=100) for i in range(12)]
        st = T.market_stats(cars)
        self.assertEqual((st["days_split"]["stock"]["n"], st["days_split"]["used"]["n"]), (12, 12),
                         "one mile either side of the line puts a car in the other market")
        self.assertEqual(T.NEW_STOCK_MILES, 100)

    def test_a_car_with_no_mileage_is_evidence_about_neither_market(self):
        """The split is a claim about two populations. A car that cannot be
        assigned to one is not evidence about either — the page's own rule,
        and the reason its used side tests mileage twice."""
        cars = [self._car(f"S{i:016d}", [50000], days_listed=70 + i, miles=50) for i in range(12)]
        cars += [self._car(f"U{i:016d}", [50000], days_listed=10 + i, miles=30000) for i in range(12)]
        cars += [self._car(f"N{i:016d}", [50000], days_listed=500, miles=None) for i in range(6)]
        st = T.market_stats(cars)
        self.assertEqual((st["days_split"]["stock"]["n"], st["days_split"]["used"]["n"]), (12, 12),
                         "the six undated-by-mileage cars joined neither side")
        self.assertEqual(st["dated"], 30, "…while still counting in the blend's own denominator")
        # …and the clause says so, because it names that denominator itself.
        self.assertEqual(st["days_split"]["none"], 6)
        self.assertIn("(30 of 30 dated) — 12 dealer stock at 75d, 12 used at 15d, "
                      "6 with no mileage", T.market_line(st))

    # -- a car with no state is in neither bucket --------------------------

    def test_a_car_with_no_state_is_not_counted_as_drivable_or_beyond(self):
        """in_scope() asks whether the listing's state is one of the buyer's,
        so a blank state falls out of "drivable" — and every count that opposes
        drivable to "beyond your states" then swept it into the second, which
        is the one thing the sheet cannot say about it. Three live cars carry
        no state today and eight VINs do over the record."""
        cars = [self._car("A" * 17, [50000], local=True, state="IL"),
                self._car("B" * 17, [50000], local=False, state="CA"),
                self._car("C" * 17, [50000], local=False, state="")]
        line = next(l for l in T.brief_lines({"daily": [], "gone": [], "as_of": T.TODAY}, cars, None)
                    if "on the market" in l)
        self.assertIn("3 on the market · 1 drivable · 1 with no state", line,
                      "one is drivable, one is beyond, and the third is in "
                      "neither — saying so is the whole point")

    def test_a_pool_the_sheet_can_place_says_nothing_about_it(self):
        """The clause only appears when there is something to say. A count
        that always fires is a count nobody reads."""
        cars = [self._car("A" * 17, [50000], local=True, state="IL"),
                self._car("B" * 17, [50000], local=False, state="CA")]
        line = next(l for l in T.brief_lines({"daily": [], "gone": [], "as_of": T.TODAY}, cars, None)
                    if "on the market" in l)
        self.assertEqual(line, "- 2 on the market · 1 drivable")

    # -- one clause, one exclusion, one denominator ------------------------

    def test_the_ask_less_clause_counts_the_cars_it_kept(self):
        """net_down excludes the sawtooth cars, as every cut figure does, and
        printed tracked_2d, which does not — 69 over 127 where the very next
        clause of the same sentence says "of 117" for the same exclusion. Ten
        cars apart on the i5, fifteen on the i7."""
        cars = [self._car(f"D{i:016d}", [50000, 49000 - i]) for i in range(6)]
        cars += [self._car(f"F{i:016d}", [50000, 50000]) for i in range(3)]
        cars += [self._car("S" * 17, [54999, 55849, 54999, 55849])]
        st = T.market_stats(cars)
        self.assertEqual((st["net_down"], st["tracked_2d"], st["two_priced"]), (6, 10, 1))
        line = T.market_line(st)
        self.assertIn("6 of 9 ask less than when first seen", line)
        self.assertIn("of 9 cut while tracked", line,
                      "the two clauses of one sentence answer to one denominator")

    def test_the_state_footer_puts_an_unplaced_car_in_neither_column(self):
        """The tiles and the brief line stopped counting a car with no state as
        "beyond". This footer is the same claim about the same cars one section
        further down, and went on doing it."""
        rows = T.load_history()
        latest = max(r["snapshot_date"] for r in rows)
        report = T.build_outputs([r for r in rows if r["snapshot_date"] == latest],
                                 rows, T.build_history(rows))[0]
        foot = next(l for l in report.splitlines()
                    if "vehicles across" in l and "beyond" in l)
        n = int(foot.split("vehicles across")[0].strip("_ "))
        parts = dict(p.rsplit(" ", 1) for p in foot.strip("_").split(" · ")[1:])
        self.assertEqual(sum(int(v) for v in parts.values()), n,
                         f"the columns must add to the model's own count: {foot}")
        self.assertIn("no state", foot,
                      "and today's i5 pool holds one such car, so it is named")

    # -- the record's vocabulary, checked on the record it prints ----------

    def test_the_record_never_claims_a_shipping_figure_was_stated(self):
        """Every shipping number on this site comes out of ship_for(), a model
        of road factor and distance bands whose constants are uncalibrated —
        buyer.ship_calibrated is null and the page says "estimate" for exactly
        that reason. "Shipping stated per car" was the comparison footnote
        promising a quote. It survived a pass that changed the same two words
        in a heading eleven lines away, which is why this reads the whole
        generated record rather than the one line that was fixed."""
        report = self._report()
        self.assertNotIn("shipping stated", report)
        self.assertIn("with a shipping estimate per car", report)

    def test_the_record_never_calls_a_list_sorted_by_asking_the_cheapest(self):
        """Same reason, same pass, other word: "the cheapest 20" is a claim
        about landed cost made by a list ordered on asking price."""
        report = self._report()
        for phrase in ("cheapest 20", "Cheapest beyond", "five cheapest"):
            self.assertNotIn(phrase, report, f"the record still says {phrase!r}")

    def _report(self):
        """The real record, built from the committed history — the only place
        these strings can be caught, since each is a literal inside a branch
        that only a full build reaches."""
        rows = T.load_history()
        latest = max(r["snapshot_date"] for r in rows)
        today = [r for r in rows if r["snapshot_date"] == latest]
        return T.build_outputs(today, rows, T.build_history(rows))[0]

    # -- the out-of-state list is sorted by asking, so it says asking -------

    def test_the_out_of_state_heading_names_the_price_it_sorted_by(self):
        """best5 takes the first five by ASKING price and every row prints a
        landed total, so "cheapest" is a claim the list cannot make: a sixth
        car asking more and shipping less lands under the fifth. "Shipping
        stated" was wrong the same way — the estimate is the tracker's."""
        cars = [self._car(f"{i:017d}", [50000 + i * 100]) for i in range(3)]
        sec = []
        T.trim_detail(sec, {"id": "t", "label": "T", "note": "", "years": [2024]},
                      cars, self._rows(cars), {}, [], "2026-09-04", T.TODAY)
        text = "\n".join(sec)
        self.assertIn("**Lowest asking beyond your states (shipping estimated)**", text)
        self.assertNotIn("Cheapest beyond", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
