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

import json
import os
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

    def test_newest_pages_are_budgeted(self):
        """The newest-first fetch must be counted, or the plan lies."""
        i5 = target("bmw-i5-edrive40")
        self.assertEqual(i5["newest"], 1)
        self.assertEqual(T.calls_for(i5), 10,
                         "2 sources x (2 sorts x 2 pages + 1 newest page)")
        self.assertEqual(T.calls_for(target("bmw-i7")), 10)
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
# Drivable: the state list plus the drive radius. A Benton Harbor car 90
# miles from Chicago must not pay shipping just because Michigan is not on
# the state list.
# --------------------------------------------------------------------------
class TestDrivable(unittest.TestCase):
    def test_wisconsin_is_a_buyer_state(self):
        self.assertIn("WI", T.STATES)
        self.assertTrue(T.in_scope({"state": "WI"}))

    def test_drive_radius_comes_from_drive_hours(self):
        self.assertEqual(T.DRIVE_RADIUS, int(round(T.DRIVE_HOURS * 55)))
        self.assertGreater(T.DRIVE_RADIUS, 0, "buyer.drive_hours should be set")

    def test_within_the_radius_is_drivable_and_ships_free(self):
        near_mi = {"state": "MI", "distance": 90}       # Benton Harbor-ish
        self.assertTrue(T.in_scope(near_mi))
        self.assertEqual(T.ship_for(near_mi), 0)

    def test_beyond_the_radius_pays_shipping(self):
        msp = {"state": "MN", "distance": 400}          # Twin Cities-ish
        self.assertFalse(T.in_scope(msp))
        self.assertGreater(T.ship_for(msp), 0)

    def test_the_radius_boundary_is_inclusive(self):
        self.assertTrue(T.in_scope({"state": "MO", "distance": T.DRIVE_RADIUS}))
        self.assertFalse(T.in_scope({"state": "MO", "distance": T.DRIVE_RADIUS + 25}))

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
    def row(tid, vin, day, price, state="IL"):
        r = {k: "" for k in T.FIELDS}
        r.update({"target": tid, "vin": vin, "snapshot_date": day,
                  "price": price, "year": "2024", "miles": 10000,
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
        self.assertIn("bmw-i7", T.TARGETS)
        self.assertEqual(T.TARGETS["bmw-i7"]["trim_key"], "all")

    def test_parsers_survive_dirty_input(self):
        self.assertEqual(T.to_int("$46,590"), 46590)
        self.assertEqual(T.to_int(""), None)
        self.assertEqual(T.to_int(None), None)
        self.assertEqual(T.to_float("39.77"), 39.77)
        self.assertEqual(T.dig({"a": {"b": 1}}, "a.b"), 1)
        self.assertIsNone(T.dig({"a": "string"}, "a.b"),
                          "digging into a string must not raise")
        self.assertEqual(T.first({"a": "", "b": "x"}, ["a", "b"]), "x")


if __name__ == "__main__":
    unittest.main(verbosity=2)
