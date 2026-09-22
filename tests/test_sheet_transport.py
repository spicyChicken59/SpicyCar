"""Transport changes no evidence: fixtures and the whole actual snapshot."""
import copy
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("AUTODEV_API_KEY", "test-key-not-used")
import Tracking as T
from sheet_transport import encode_sheet, decode_sheet, parse_sheet


def assert_typed_equal(test, left, right, path="$"):
    test.assertIs(type(left), type(right), path)
    if isinstance(left, dict):
        test.assertEqual(left.keys(), right.keys(), path)
        for key in left:
            assert_typed_equal(test, left[key], right[key], path + "." + key)
    elif isinstance(left, list):
        test.assertEqual(len(left), len(right), path)
        for i, (a, b) in enumerate(zip(left, right)):
            assert_typed_equal(test, a, b, f"{path}[{i}]")
    else:
        test.assertEqual(left, right, path)
        if isinstance(left, float) and left == 0:
            test.assertEqual(math.copysign(1, left), math.copysign(1, right), path)


FIXTURE = {"a": [None, False, True, 0, 1, 0.0, -0.0, -1.25, "", "0", "café 雪 🐔",
                 [], {}, [0, 1, [1, 0]], {"__proto__": {"polluted": True}, "constructor": "literal"}],
           "live": [{"vin": "FIXTUREVIN00000001", "owners": None, "notes": "two  spaces\nnext"},
                    {"vin": "FIXTUREVIN00000002", "owners": 0}, {"vin": "FIXTUREVIN00000003"}],
           "gone": [{"series": [["2026-09-01", 42000], ["2026-09-02", 0]],
                     "url": "https://example.invalid/?a=1&b=2", "unknown": ""}]}


class SheetTransportTests(unittest.TestCase):
    def test_deterministic_round_trip_is_type_sensitive(self):
        wire = encode_sheet(FIXTURE)
        assert_typed_equal(self, FIXTURE, parse_sheet(json.dumps(wire)))
        self.assertEqual(wire, encode_sheet(dict(reversed(list(FIXTURE.items())))))
        self.assertEqual(T.sheet_text(FIXTURE), T.sheet_text(copy.deepcopy(FIXTURE)))

    def test_plain_snapshot_compatibility(self):
        assert_typed_equal(self, FIXTURE, parse_sheet(json.dumps(FIXTURE)))

    def test_schema_references_do_not_alias_records(self):
        source = {"cars": [{"vin": "A", "history": [1]}, {"vin": "B", "history": [1]}]}
        decoded = decode_sheet(encode_sheet(source))
        decoded["cars"][0]["history"].append(2)
        self.assertEqual(decoded["cars"][1]["history"], [1])
        self.assertEqual(source["cars"][0]["history"], [1])

    def test_empty_root_and_containers(self):
        for value in ({}, {"a": [], "b": {}, "c": [[], {}]}):
            assert_typed_equal(self, value, decode_sheet(encode_sheet(value)))

    def test_encoder_rejects_invalid_values_at_any_depth(self):
        for value in (float("nan"), float("inf"), -float("inf"), set(), object(), {1: "key"}):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                encode_sheet({"last": [value]})

    def test_encoder_rejects_nonobject_root(self):
        for value in ([], None, False, 1, ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                encode_sheet(value)

    def test_unsupported_version_and_format(self):
        for field, value in (("version", 2), ("version", True), ("version", "1"),
                             ("format", "other"), ("format", None)):
            wire = encode_sheet(FIXTURE); wire[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                decode_sheet(wire)

    def test_incomplete_or_extra_envelope(self):
        for field in ("format", "version", "schemas", "data"):
            wire = encode_sheet(FIXTURE); del wire[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                decode_sheet(wire)
        wire = encode_sheet(FIXTURE); wire["extra"] = None
        with self.assertRaises(ValueError):
            decode_sheet(wire)

    def test_invalid_schema_table(self):
        for schemas in (None, {}, [None], [[1]], [["a", "a"]]):
            wire = encode_sheet(FIXTURE); wire["schemas"] = schemas
            with self.subTest(schemas=schemas), self.assertRaises(ValueError):
                decode_sheet(wire)

    def test_bad_tags_references_and_row_widths(self):
        for data in ([], [2], [True, 0], [1], [1, -1], [1, 999], [1, True], [1, "0"],
                     [1, 0], [1, 0, "a", "extra"], {"untagged": True}, [0], None):
            wire = encode_sheet({"a": 0}); wire["data"] = data
            with self.subTest(data=data), self.assertRaises(ValueError):
                decode_sheet(wire)

    def test_late_corruption_never_returns_partial_records(self):
        wire = encode_sheet({"first": [1, 2], "last": {"vin": "LAST"}})
        wire["data"][-1][-1] = {"untagged": "corrupt final value"}
        with self.assertRaises(ValueError):
            decode_sheet(wire)

    def test_truncated_json_is_not_empty_data(self):
        with self.assertRaises(ValueError):
            parse_sheet(T.sheet_text(FIXTURE)[:-8])

    def test_failed_fsync_preserves_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "data.json"
            T.write_sheet(FIXTURE, path); before = path.read_bytes()
            with patch.object(T.os, "fsync", side_effect=OSError("fixture disk failure")):
                with self.assertRaises(OSError):
                    T.write_sheet({"new": True}, path)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_failed_write_preserves_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "data.json"
            T.write_sheet(FIXTURE, path); before = path.read_bytes()
            original_open = Path.open
            class PartialWrite:
                def __init__(self, out):
                    self.out = out
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    self.out.close()
                def write(self, text):
                    self.out.write(text[:12])
                    self.out.flush()
                    raise OSError("fixture partial write failure")
            def fail_write(p, *args, **kwargs):
                out = original_open(p, *args, **kwargs)
                return PartialWrite(out) if p.suffix == ".tmp" else out
            with patch.object(Path, "open", fail_write), self.assertRaises(OSError):
                T.write_sheet({"new": True}, path)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_invalid_input_preserves_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "data.json"
            T.write_sheet(FIXTURE, path); before = path.read_bytes()
            for invalid in ({"a": [float("nan")]}, {"a": object()}, {1: "not a JSON key"}):
                with self.subTest(invalid=repr(invalid)), self.assertRaises(ValueError):
                    T.write_sheet(invalid, path)
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_actual_snapshot_all_values_match_same_day_rebuild(self):
        actual = parse_sheet(Path("docs/data.json").read_text())
        day = actual["generated"]
        with patch.object(T, "TODAY", day), patch.object(T, "TODAY_ORD", T.date.fromisoformat(day).toordinal()), \
                patch.object(T, "PRICE_WINDOW", {}), patch.object(T, "EXHAUSTED", set()), \
                patch.object(T, "FAILED_SCOPES", set()), patch.object(T.requests, "get", side_effect=AssertionError("provider request")), \
                patch.object(T.requests, "post", side_effect=AssertionError("external write")):
            rows = T.load_history()
            latest = max(row["snapshot_date"] for row in rows)
            _, logical, _ = T.build_outputs([row for row in rows if row["snapshot_date"] == latest], rows, T.build_history(rows))
        # Normalize only Python tuples to their existing JSON-array meaning.
        expected = json.loads(json.dumps(logical, allow_nan=False))
        assert_typed_equal(self, expected, actual)

    def test_javascript_decoder_matches_python_for_fixture_and_whole_snapshot(self):
        self.assertIsNotNone(shutil.which("node"), "Node is required to verify the shipped decoder")
        actual = parse_sheet(Path("docs/data.json").read_text())
        source = """
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {decode} = require('./docs/sheet-transport.js');
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
for (const [plain, wire] of cases) {
  assert.deepStrictEqual(decode(wire), plain);
  assert.deepStrictEqual(decode(plain), plain);
}
assert.equal({}.polluted, undefined);
"""
        result = subprocess.run(["node", "-e", source], input=json.dumps([
            [FIXTURE, encode_sheet(FIXTURE)], [actual, json.loads(Path("docs/data.json").read_text())]
        ]), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_javascript_rejects_malformed_without_partial_result(self):
        source = """
const assert = require('node:assert/strict');
const {decode, parse} = require('./docs/sheet-transport.js');
const good = {format:'spicycar-sheet', version:1, schemas:[['a']], data:[1,0,0]};
const bad = [null, [], {...good,version:true}, {...good,version:2}, {...good,format:'other'},
  {...good,schemas:[['a','a']]}, {...good,schemas:[[0]]}, {...good,schemas:{}},
  ...[[],[2],[true,0],[1],[1,-1],[1,99],[1,true],[1,'0'],[1,0],[1,0,1,2],{},[0],null,
      [1,0,[0,1,{untagged:'late corruption'}]]].map(data=>({...good,data}))];
for(const key of Object.keys(good)){const value={...good};delete value[key];bad.push(value);}
for(const value of bad) assert.throws(()=>decode(value), /snapshot transport/);
assert.throws(()=>parse('{'));
"""
        result = subprocess.run(["node", "-e", source], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
