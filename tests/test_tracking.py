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
import json
import os
import re
import struct
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
        for miles in (0, 5000, 20000, 60000, 150000):
            for ship in (0, 350, 1200):
                total = T.adjusted(40000, miles, ship)
                self.assertGreaterEqual(
                    total, 40000,
                    f"asking 40000 + ship {ship} at {miles} mi came to {total}; the "
                    f"mileage adjustment must stay off (buyer.cents_per_mile = 0)")

    def test_mileage_adjustment_is_off_by_default(self):
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
        """Two live targets are separated from a sibling by trim_exclude and
        nothing else. bmw-ix-m matches '' — every iX — and relies on excluding
        'xdrive'; lucid-air-touring matches 'touring', which the Grand Touring
        also contains, and relies on excluding 'grand'. Lose the branch and
        each target quietly absorbs the sibling's whole market under its own
        name, its median and its picks included. The trim gates run before
        year and price, so a record only has to carry the trim words."""
        import copy

        def rec_with(trim):
            r = copy.deepcopy({k: v for k, v in FIXTURES["clean"].items() if not k.startswith("_")})
            r["vehicle"]["trim"] = trim
            r["vehicle"]["series"] = trim
            return r
        self.assertIsNone(T.normalize(rec_with("xDrive50"), target("bmw-ix-m"), self.dropped))
        self.assertEqual(self.dropped["trim excluded"], 1, "an xDrive is not an M, whatever else it is")
        self.assertIsNone(T.normalize(rec_with("Grand Touring"), target("lucid-air-touring"), self.dropped))
        self.assertEqual(self.dropped["trim excluded"], 2, "'touring' is inside 'Grand Touring'; only the exclusion tells them apart")
        # …and the same words pass the trim gates of the target they belong to,
        # so the drop is the exclusion's doing and not a mismatch on the way in.
        kept = Counter()
        T.normalize(rec_with("xDrive50"), target("bmw-ix-xdrive"), kept)
        T.normalize(rec_with("Grand Touring"), target("lucid-air-grand-touring"), kept)
        self.assertEqual(kept["trim excluded"], 0)
        self.assertEqual(kept["trim mismatch"], 0)

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
            self.assertNotIn("under typical", "\n".join(T.shortlist_section(live, {}, {"N" * 17: inside})))
            self.assertIn("3% under typical", "\n".join(T.shortlist_section(live, {}, {"N" * 17: below})))
        finally:
            T.SHORTLIST.clear()
            T.SHORTLIST.update(old)

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
        self.assertIn("20% cut while tracked", line)
        self.assertIn("1 seen at two prices, not counted", line)

    def test_the_report_names_the_two_prices_instead_of_counting_cuts(self):
        s = {"cuts": 4, "delta": 0, "days_tracked": 13,
             "series": self.series([54999, 55849] * 6 + [54999])}
        x = {**listing(), "vin": "WBY33FK05RCP99465", "price": 54999, **s}
        old = dict(T.SHORTLIST)
        T.SHORTLIST.clear(); T.SHORTLIST.update({"WBY33FK05RCP99465": ""})
        try:
            text = "\n".join(T.shortlist_section({"WBY33FK05RCP99465": (x, "BMW i5")}, {}, {}))
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
        self.assertEqual(T.adjusted(40000, 20000, 1200), 41200)
        self.assertEqual(T.adjusted(40000, 20000, 0), 40000)

    def test_no_price_is_no_answer(self):
        self.assertIsNone(T.adjusted(None, 20000, 1200))

    def test_the_mileage_term_is_signed_the_way_the_docstring_says(self):
        """Off in the shipped config, so this patches it on rather than
        asserting a dead branch stays dead: above the baseline COSTS, below it
        credits, and a car with no mileage is not guessed at."""
        with unittest.mock.patch.dict(T.BUYER, {"cents_per_mile": 0.30,
                                                "mileage_baseline": 20000}, clear=False):
            self.assertEqual(T.adjusted(40000, 30000, 0), 43000,
                             "10,000 miles over the baseline at 30c is $3,000 more")
            self.assertEqual(T.adjusted(40000, 10000, 0), 37000,
                             "and 10,000 under it is $3,000 less")
            self.assertEqual(T.adjusted(40000, None, 500), 40500,
                             "a car with no mileage gets shipping and no guess")

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
    """"## Today" describes the newest snapshot, not the wall clock.

    Its new/gone detectors were already data-relative; the CUT detector was
    gated on TODAY. So a rebuild run on a day the tracker had not fetched —
    tools/rebuild_outputs.py, which is exactly what a dispatch runs — wrote a
    report whose Today section had lost every price-cut bullet while still
    printing "79 new · 31 gone" above it and per-model lines counting 42 price
    changes below. Three surfaces, one day, two different stories.
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
        self.assertIn("## Today", report,
                      "the section is about the newest snapshot, whenever it was taken")
        today = report.split("## Today")[1].split("\n## ")[0]
        self.assertIn("▼", today,
                      "a $2,000 cut at the last fetch is a cut whether or not the "
                      "tracker has run again since")
        self.assertIn("cut", subject.lower(),
                      "and the subject line says so too")


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
    """"New today" means first seen on this snapshot.

    days_tracked is the length of a car's price series and a series only grows
    on days its target was fetched, so a car seen once on Monday still reads
    days_tracked == 1 on Thursday. On any day when some OTHER trim of its model
    was due, the whole "New today" block re-announced it — three cars first seen
    on 2026-09-01 were headlined as "first seen this run" on a quiet 09-04 —
    and the report's per-model "N new", the dashboard tile and the `new` chip
    all counted it again with them.
    """

    def test_a_car_first_seen_today_is_new(self):
        self.assertTrue(T.is_new_today({"first_seen": T.TODAY, "days_tracked": 1}))

    def test_a_car_carried_forward_from_an_earlier_fetch_is_not(self):
        """The exact shape: seen once, days ago, its trim not fetched since."""
        self.assertFalse(T.is_new_today({"first_seen": "2026-01-01", "days_tracked": 1}),
                         "one sighting is not one DAY when the trim runs on a cadence")

    def test_a_car_seen_every_day_since_is_not_new_either(self):
        self.assertFalse(T.is_new_today({"first_seen": "2026-01-01", "days_tracked": 40}))

    def test_a_row_with_no_first_seen_falls_back(self):
        """An older sheet: better the old test than no answer at all."""
        self.assertTrue(T.is_new_today({"days_tracked": 1}))
        self.assertFalse(T.is_new_today({"days_tracked": 3}))

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
        self.assertFalse(T.is_new_today(e), "a car listed for ten days is not new because a second query found it")


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
        self.assertIn("66% cut while tracked", line, "the share of cars with any downward step still follows")
        self.assertLess(line.index("69 of 131"), line.index("66% cut"))


class TestSeenLabel(unittest.TestCase):
    """"tracked 21d" read as three weeks; it was twenty-one sightings on a
    target fetched every second day, six weeks on the market. The label now
    says what the count counts, and over what span."""

    def test_sightings_over_the_span_they_cover(self):
        series = [[f"2026-08-{d:02d}", 40000] for d in (1, 3, 5, 7, 9, 11)]
        self.assertEqual(T.seen_label({"days_tracked": 6, "series": series}), "seen 6 of 11 days")

    def test_one_sighting_is_once_and_no_series_is_just_the_count(self):
        self.assertEqual(T.seen_label({"days_tracked": 1, "series": [["2026-08-01", 1]]}), "seen once")
        self.assertEqual(T.seen_label({"days_tracked": 3}), "seen 3 days")

    def test_the_report_row_carries_it(self):
        r = {k: "" for k in T.FIELDS}
        r.update({"target": "bmw-i5-edrive40", "vin": "V", "year": "2024", "trim": "eDrive40",
                  "price": 45000, "miles": 20000, "state": "IL", "city": "Chicago", "snapshot_date": T.TODAY})
        series = [[f"2026-07-{d:02d}", 45000] for d in range(1, 22)]     # 21 sightings on 21 days
        line = T.fmt_row(r, {"series": series, "days_tracked": 21, "first_seen": series[0][0]})
        self.assertIn("seen 21 of 21 days", line)
        self.assertNotIn("tracked", line)


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
                                "series": [["2026-08-01", 39998]]})
        self.assertIn("cut 1x, then back up", back_up)
        self.assertNotIn("down 1x", back_up)
        above = T.fmt_row(r, {"cuts": 2, "delta": 849, "days_tracked": 7,
                              "series": [["2026-08-01", 42995]]})
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
                                              "tracked_2d": 0,
                                              "cut_share": None,
                                              "median_cut": None,
                                              "net_down": 0,
                                              "restored": 0,
                                              "median_net_drop": None,
                                              "two_priced": 0})

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
                              "cut_share": 0.41, "median_cut": 1050})
        self.assertIn("typical car 34d on market", line)
        self.assertIn("41% cut while tracked, median $1,050", line)
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
        sec, subject = T.build_today({"cuts": [], "new": [], "gone": []})
        self.assertEqual(sec, [])
        self.assertIn("quiet day", subject)

    def test_the_biggest_relevant_cut_leads_the_subject(self):
        sec, subject = T.build_today({
            "cuts": [self.cut(400, 45000, local=True),
                     self.cut(1200, 46000, local=False, shopping=False,
                              vin="V2", label="Kia EV6")],
            "new": [], "gone": []})
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
                          "last_price": 62000, "shopping": True}]})
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
                      "last_price": 47000, "shopping": True}]})
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
            gone = {"GONE1": {"vin": "GONE1", "likely": "delisted",
                              "last_seen": "2026-08-25", "last_price": 47000,
                              "trim_label": "eDrive40", "city": "", "state": "",
                              "url": ""}}
            sec = "\n".join(T.shortlist_section(live, gone, {}))
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
            self.assertEqual(T.shortlist_section({}, {}, {}), [])
        finally:
            T.SHORTLIST.update(old)


# --------------------------------------------------------------------------
# Config resolution and small parsers.
# --------------------------------------------------------------------------
class TestConfig(unittest.TestCase):
    def test_trim_inherits_from_model_brand_and_defaults(self):
        t = target("bmw-i5-m60")
        self.assertEqual(t["min_price"], 30000)          # trim override
        self.assertEqual(t["years"], ["2024", "2025"])   # from the model
        self.assertEqual(t["make"], "BMW")               # from the brand

    def test_a_model_without_trims_is_one_target(self):
        self.assertIn("hyundai-ioniq5", T.TARGETS)
        self.assertEqual(T.TARGETS["hyundai-ioniq5"]["trim_key"], "all")

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

    def test_the_watchlist_reduction(self):
        # Removed to pay for the CPO watches; the A6 e-tron replaces them.
        for tid in T.TARGETS:
            self.assertNotIn("ev6", tid)
            self.assertNotIn("q4-etron", tid)
            self.assertNotIn("equinox", tid)
        self.assertIn("audi-a6-etron", T.TARGETS)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)


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

    Half of most targets' calls go to asking the buyer's eight states the same
    question the national query just asked, and the obvious saving — make the
    benchmark models national_only — is worth about 120 calls a month, the
    States half of the eleven non-shopping targets at today's cadences. Whether
    it is FREE is a different question: National sorted by price returns the
    twenty cheapest in the country, which on a model whose cheap end sits in
    California can be twenty cars none of them drivable, while the States query
    is the only thing surfacing the Ohio one.

    So the flag is not flipped on an argument. It is flipped when this audit
    has watched `states_only` sit at zero for a while. These tests hold the
    measurement itself honest, because a saving justified by a broken
    instrument is the most expensive kind.
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

    def _drive(self, history, batches, allow_refetch=False):
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
            unittest.mock.patch.object(T, "save_spend_history", lambda row, **k: {}),
            unittest.mock.patch.object(T, "save_overlap_history", lambda *a, **k: {}),
            # …and the fetch log, or driving main() writes a data/fetch_log.json
            # of stub numbers into the working tree — which daily.yml would then
            # `git add data` and commit as a real day's record
            unittest.mock.patch.object(T, "save_fetch_log", lambda *a, **k: {}),
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
