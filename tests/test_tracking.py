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
from collections import Counter
from datetime import date
from pathlib import Path

os.environ.setdefault("AUTODEV_API_KEY", "test-key-not-used")
os.environ.pop("BUYER_HOME_ZIP", None)          # keep the import offline

import Tracking as T                            # noqa: E402

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
        self.assertNotIn("m70", target("bmw-i7-cpo")["trim_query"].lower())
        self.assertEqual(target("bmw-i7-cpo")["trim_exclude"], "m70",
                         "the i7 M70 is spelled with xDrive, so the query "
                         "alone cannot keep it out — trim_exclude must")

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
        cheap = [listing(price=p, miles=20000) for p in (19000, 19500, 20000, 20500)]
        dear = [listing(price=p, miles=20000) for p in (50000, 70000, 72000, 74000)]
        best_cheap = T.score_picks(cheap, "Cheap Model")[0]
        best_dear = T.score_picks(dear, "Dear Model")[0]
        self.assertLess(best_cheap["pick_pct"], 0.10)
        self.assertGreater(best_dear["pick_pct"], 0.20)
        self.assertGreater(best_dear["pick_pct"], best_cheap["pick_pct"],
                           "the biggest discount relative to its own model should win, "
                           "even though it is the more expensive car")

    def test_a_thin_pool_produces_no_picks(self):
        self.assertEqual(T.score_picks([listing(), listing()], "Two Cars"), [])

    def test_scoring_uses_the_model_year_cohort_when_it_is_big_enough(self):
        """A 2023 car must be judged against 2023 prices, not a median that
        blends in far dearer 2026 cars."""
        old = [listing(price=p, year="2023") for p in (58000, 60000, 62000)]
        new = [listing(price=p, year="2026") for p in (118000, 120000, 122000)]
        scored = T.score_picks(old + new, "i7")
        mid_old = next(p for p in scored if p["price"] == 60000)
        self.assertLess(abs(mid_old["pick_pct"]), 0.05,
                        "the median 2023 car is typical for 2023, not 50% under")
        self.assertEqual(mid_old["pick_year"], "2023")

    def test_scoring_prefers_the_trim_cohort_when_it_is_big_enough(self):
        """A cheapest-trim car must be judged against its own trim, not a
        median blended with the model's six-figure flagship trim."""
        base = [listing(price=p, year="2023", trim="eDrive50")
                for p in (58000, 60000, 62000)]
        flag = [listing(price=p, year="2023", trim="M70")
                for p in (118000, 120000, 122000)]
        scored = T.score_picks(base + flag, "BMW i7")
        mid = next(p for p in scored if p["price"] == 60000)
        self.assertLess(abs(mid["pick_pct"]), 0.05,
                        "a median eDrive50 is typical for eDrive50s, not 45% under")
        self.assertEqual(mid["pick_year"], "2023")
        self.assertEqual(mid["pick_trim"], "eDrive50")

    def test_a_thin_trim_falls_back_to_the_year_cohort(self):
        pool = ([listing(price=p, year="2023", trim="xDrive60")
                 for p in (60000, 62000)]              # two: no trim cohort
                + [listing(price=61000, year="2023", trim="eDrive50")])
        scored = T.score_picks(pool, "BMW i7")
        self.assertTrue(all(p["pick_trim"] == "" for p in scored))
        self.assertTrue(all(p["pick_year"] == "2023" for p in scored))

    def test_trim_display_drops_model_words(self):
        self.assertEqual(T.trim_disp("BMW i7", "i7 xDrive60"), "xDrive60")
        self.assertEqual(T.trim_disp("BMW i5", "eDrive40"), "eDrive40")

    def test_a_thin_year_falls_back_to_the_model_median(self):
        pool = ([listing(price=p, year="2024") for p in (40000, 42000, 44000)]
                + [listing(price=39000, year="2022")])
        scored = T.score_picks(pool, "M")
        lone = next(p for p in scored if p["price"] == 39000)
        self.assertEqual(lone["pick_year"], "", "one 2022 car is not a cohort")
        self.assertGreater(lone["pick_pct"], 0, "still judged against the model")

    def test_a_pick_must_sit_under_typical(self):
        """A thin drivable pool must not promote above-median cars to picks."""
        scored = T.score_picks([listing(price=p) for p in (40000, 44000, 48000)], "M")
        picks = T.choose_picks(scored, 4)
        self.assertTrue(picks, "the genuinely-under-typical car is still a pick")
        self.assertTrue(all(p["pick_pct"] > 0 for p in picks),
                        "no car at or above its model's typical value may be a pick")

    def test_per_model_cap_spreads_the_picks(self):
        scored = (T.score_picks([listing(price=p) for p in (30000, 40000, 41000, 42000)], "A")
                  + T.score_picks([listing(price=p) for p in (30000, 40000, 41000, 42000)], "B"))
        picks = T.choose_picks(scored, 4, per_model=2)
        counts = Counter(p["model_label"] for p in picks)
        self.assertLessEqual(max(counts.values()), 2)

    def test_picks_split_into_drivable_and_worth_the_ship(self):
        """Every car is scored against the whole model, then split: drivable
        picks on one side, everything else on the other, no overlap."""
        pool = [listing(price=30000, local=True, state="IL"),
                listing(price=40000, local=True, state="WI"),
                listing(price=31000, local=False, state="CA"),
                listing(price=41000, local=False, state="TX")]
        local, ship = T.split_picks(T.score_picks(pool, "M"), 4)
        self.assertTrue(local and ship)
        self.assertTrue(all(p["local"] for p in local))
        self.assertTrue(all(not p["local"] for p in ship))
        both = {id(p) for p in local} & {id(p) for p in ship}
        self.assertFalse(both, "a car must not appear in both lists")

    def test_a_drivable_pick_is_scored_against_the_whole_market(self):
        """The drivable list must not get its own median — a merely-average
        local car scores the same whether or not remote cars exist."""
        locals_ = [listing(price=p, local=True) for p in (40000, 41000, 42000)]
        remotes = [listing(price=p, local=False, ship=1200) for p in (30000, 30500)]
        scored_all = T.score_picks(locals_ + remotes, "M")
        by_price = {p["price"]: p for p in scored_all if p["local"]}
        self.assertLess(by_price[42000]["pick_pct"], 0.05,
                        "an above-median local car is not a bargain just for being local")


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
class TestDelisted(unittest.TestCase):
    def setUp(self):
        self._pw, self._ex = dict(T.PRICE_WINDOW), set(T.EXHAUSTED)
        T.PRICE_WINDOW.clear()
        T.EXHAUSTED.clear()

    def tearDown(self):
        T.PRICE_WINDOW.clear()
        T.PRICE_WINDOW.update(self._pw)
        T.EXHAUSTED.clear()
        T.EXHAUSTED.update(self._ex)

    @staticmethod
    def row(tid, vin, day, price, state="IL", miles=10000):
        r = {k: "" for k in T.FIELDS}
        r.update({"target": tid, "vin": vin, "snapshot_date": day,
                  "price": price, "year": "2024", "miles": miles,
                  "state": state, "city": "Chicago"})
        return r

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
        # IS that day's cheapest-N cut-off.
        d2, d1, tid = self.days_ago(2), self.days_ago(1), "bmw-i5-edrive40"
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

    def test_short_vanish_day_returned_everything_so_no_cutoff(self):
        d2, d1, tid = self.days_ago(2), self.days_ago(1), "bmw-i5-m60"
        fill = [self.row(tid, f"W{i}", d, 40000 + i * 1000)
                for d in (d2, d1) for i in range(5)]
        all_rows = fill + [self.row(tid, "HIGH", d2, 90000)]
        today = [r for r in all_rows if r["snapshot_date"] == d1]
        gone = T.delisted({tid}, all_rows, today, T.build_history(all_rows))
        # the vanish day kept fewer rows than one page, so its queries saw
        # their entire scope — no cut-off exists and the missing car is
        # really gone whatever its price
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

    def test_never_fetched_again_is_not_checked(self):
        d1, tid = self.days_ago(1), "bmw-i5-edrive40"
        all_rows = [self.row(tid, "V1", d1, 45000)]
        gone = T.delisted({tid}, all_rows, [], T.build_history(all_rows))
        self.assertEqual(gone[0]["likely"], "not checked")


# --------------------------------------------------------------------------
# Market stats: the negotiation context — how long cars sit, how often and
# how much they get cut, and each car's staleness within its own model.
# --------------------------------------------------------------------------
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
                                              "median_cut": None})

    def test_days_to_sale_counts_only_real_delistings(self):
        gone = [
            {"likely": "delisted", "listed_since": "2026-08-10",
             "last_seen": "2026-08-20", "first_seen": "2026-08-15"},   # 10d, by listing date
            {"likely": "delisted", "listed_since": "",
             "last_seen": "2026-08-20", "first_seen": "2026-08-16"},   # 4d, by first sighting
            {"likely": "out of window", "listed_since": "2026-07-01",
             "last_seen": "2026-08-20", "first_seen": "2026-07-02"},   # not a sale
            {"likely": "delisted", "listed_since": "garbage",
             "last_seen": "2026-08-20", "first_seen": None},           # unparseable: skipped
        ]
        stats = T.sale_stats(gone)
        self.assertEqual(stats["n_sold"], 2)
        self.assertEqual(stats["median_days_to_sale"], 7)   # median of 10 and 4

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
            self.assertIn("GONE — likely sold or pulled", sec)
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
        self.assertEqual(shopped, ["bmw-i5-cpo", "bmw-i5-edrive40",
                                   "bmw-i7-cpo", "bmw-i7-edrive50"])

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
        for tid in ("bmw-i5-cpo", "bmw-i7-cpo"):
            t = T.TARGETS[tid]
            self.assertEqual(T.sources_for(t), [("National", None)])
            self.assertEqual(T.calls_for(t), 2)     # 1 source x 1 sort x 2 pages
            self.assertEqual(T.window_dim(t), "miles")
        self.assertNotEqual(T.TARGETS["bmw-i5-cpo"]["offset"],
                            T.TARGETS["bmw-i7-cpo"]["offset"],
                            "both watches on the same days doubles the "
                            "worst-day cost for no coverage gain")

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
