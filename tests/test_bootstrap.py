"""The first-fetch path must add coverage without rebuying or losing a day."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
os.environ.setdefault('AUTODEV_API_KEY', 'test-key-not-used')
import Tracking as T

class BootstrapTests(unittest.TestCase):
    def setUp(self):
        # Exercise the retained legacy bootstrap path explicitly. Fair mode
        # seeds targets during the regular collector and has its own tests.
        for name, value in [('FAIR', {'enabled': False}), ('COMPARISON_CADENCE', T.COMPARISON_CADENCE)]:
            p = patch.object(T, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(T, 'TARGETS', T.build_targets()); p.start(); self.addCleanup(p.stop)
        self.target = next(t for t in T.TARGETS.values() if T.calls_for(t) == 2 and len(T.sources_for(t)) == 2)
        self.tid = self.target['id']
        self.sources = [name for name, _ in T.sources_for(self.target)]
        self.complete = {name: {'failed': False, 'raw': 0, 'window': None, 'dim': 'price', 'exhausted': True} for name in self.sources}

    def test_successful_empty_market_is_not_fetched_again(self):
        with patch.object(T, 'TARGETS', {self.tid: self.target}):
            self.assertEqual(T.missing_targets([], {T.TODAY: {self.tid: self.complete}}), [])
            self.assertEqual(T.missing_targets([], {}), [self.target])

    def test_partial_first_fetch_is_retryable(self):
        facts = {self.sources[0]: self.complete[self.sources[0]]}
        with patch.object(T, 'TARGETS', {self.tid: self.target}):
            self.assertEqual(T.missing_targets([{'target': self.tid}], {T.TODAY: {self.tid: facts}}), [self.target])

    def test_bootstrap_keeps_other_targets_and_failed_partial_rows(self):
        old = [{'snapshot_date': T.TODAY, 'target': self.tid, 'vin': 'OLD'},
               {'snapshot_date': T.TODAY, 'target': 'daily', 'vin': 'KEEP'}]
        new = [{'snapshot_date': T.TODAY, 'target': self.tid, 'vin': 'NEW'}]
        complete = {self.tid: self.complete}
        self.assertEqual({r['vin'] for r in T.merge_bootstrap_rows(old, new, complete)}, {'NEW', 'KEEP'})
        failed = {self.tid: {name: {**f, 'failed': True} for name, f in self.complete.items()}}
        self.assertEqual({r['vin'] for r in T.merge_bootstrap_rows(old, [], failed)}, {'OLD', 'KEEP'})
        self.assertEqual({r['vin'] for r in T.merge_bootstrap_rows(old, new, failed)}, {'OLD', 'NEW', 'KEEP'})

    def test_targeted_log_preserves_daily_observations(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / 'fetch.json'
            log.write_text(json.dumps({T.TODAY: {'daily': self.complete}}))
            result = T.save_fetch_log({self.tid: self.complete}, path=log, merge_targets=True)
            self.assertEqual(result[T.TODAY]['daily'], self.complete)
            self.assertEqual(result[T.TODAY][self.tid], self.complete)

    def test_remaining_day_and_month_both_limit_bootstrap(self):
        with patch.object(T, 'TODAY', '2026-09-30'), patch.object(T, 'TODAY_ORD', T.date(2026,9,30).toordinal()):
            self.assertEqual(T.bootstrap_allowance({'2026-09-30': {'actual': 16}}), 24)
            self.assertEqual(T.bootstrap_allowance({'2026-09-01': {'actual': 980}, '2026-09-30': {'actual': 16}}), 4)
            self.assertEqual(T.bootstrap_allowance({'2026-09-30': {'actual': 41}}), 0)

    def test_retry_cannot_exceed_request_allowance(self):
        response = Mock(status_code=503, text='temporary error')
        with patch.object(T, 'REQUEST_ALLOWANCE', 1), patch.object(T, 'CALLS', 0), patch.object(T, 'SPENT', {}), patch.object(T, 'FAILED_SCOPES', set()), patch.object(T.requests, 'get', return_value=response) as get, patch.object(T.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(T.fetch(self.sources[0], None, 'price.asc', 1, self.target))
            self.assertEqual(get.call_count, 1)
            self.assertEqual(T.CALLS, 1)
            self.assertIn((self.tid, self.sources[0]), T.FAILED_SCOPES)

    def test_bootstrap_cannot_replace_the_first_regular_run_of_a_day(self):
        with patch.dict(os.environ, {'FILL_MISSING': '1'}), patch.object(T, 'load_history', return_value=[]), patch.object(T.requests, 'get') as get, patch.object(T, 'write_rows') as write, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, 'regular daily tracker first'):
                T.main()
            get.assert_not_called()
            write.assert_not_called()

    def test_bootstrap_spend_accumulates(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'spend.json'
            path.write_text(json.dumps({T.TODAY: {'actual': 16, 'planned': 24, 'unrun': 0, 'failed': 0}}))
            row = {'actual': 24, 'planned': 24, 'unrun': 0, 'failed': 0}
            result = T.save_spend_history(row, path=path, add_plan=True)[T.TODAY]
            self.assertEqual(result['actual'], 40)
            self.assertEqual(result['planned'], 48)
            self.assertEqual(result['banked'], 8)
