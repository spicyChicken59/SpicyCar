"""Offline, full-record preservation proof and bounded synthetic growth.

Usage: python tools/verify_sheet_capacity.py --baseline /path/to/plain.json
       --node /path/to/node --out /path/to/evidence
Only --out is written. No provider calls, source edits, or rebuild-date change.
"""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sheet_transport import encode_sheet, parse_sheet


def typed_equal(a, b, path="$"):
    if type(a) is not type(b):
        raise AssertionError(f"Type mismatch at {path}: {type(a)} != {type(b)}")
    if isinstance(a, dict):
        assert a.keys() == b.keys(), path
        return 1 + sum(typed_equal(a[k], b[k], path + "." + k) for k in a)
    if isinstance(a, list):
        assert len(a) == len(b), path
        return 1 + sum(typed_equal(x, y, f"{path}[{i}]") for i, (x, y) in enumerate(zip(a, b)))
    assert a == b, path
    if isinstance(a, float) and a == 0:
        assert math.copysign(1, a) == math.copysign(1, b), path
    return 1


def models(site):
    return [m for b in site["brands"].values() for m in b["models"].values()]


def counts(site):
    return {field: sum(len(m[field]) for m in models(site)) for field in ("listings", "gone")}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", required=True, type=Path)
    ap.add_argument("--node", default="node")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    original = json.loads(args.baseline.read_bytes())
    current_path = ROOT / "docs/data.json"
    decoded = parse_sheet(current_path.read_bytes())
    nodes = typed_equal(original, decoded)
    cases = [{"label": "actual snapshot", "path": str(current_path), **counts(decoded)}]
    # Extra records sampled evenly through EVERY model. Hash-derived VINs and
    # source-link suffixes prevent identical duplicate records inflating gains.
    # Dates/series length stay fixed. This is record-count stress, NOT days of
    # collection, a retention forecast, or evidence of real additional cars.
    for percent in (5, 10, 20):
        grown = copy.deepcopy(original)
        for model_index, model in enumerate(models(grown)):
            for field in ("listings", "gone"):
                pool = model[field][:]
                extra = math.ceil(len(pool) * percent / 100)
                for i in range(extra):
                    car = copy.deepcopy(pool[(i * len(pool)) // extra])
                    old = car["vin"]
                    vin = hashlib.sha256(f"capacity-fixture:{model_index}:{field}:{i}".encode()).hexdigest()[:17].upper()
                    car["vin"] = vin
                    for link in ("url", "image", "carfax"):
                        if car.get(link):
                            car[link] = car[link].replace(old, vin)
                            car[link] += ("&" if "?" in car[link] else "?") + "capacity_fixture=" + vin
                    model[field].append(car)
        path = args.out / f"synthetic-plus-{percent}-percent.json"
        path.write_text(json.dumps(encode_sheet(grown), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
        typed_equal(grown, parse_sheet(path.read_bytes()))
        cases.append({"label": f"SYNTHETIC +{percent}% records per model (rounded up)", "path": str(path), **counts(grown)})
    js = """
const fs = require('node:fs'), zlib = require('node:zlib'), assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0,'utf8'));
const {decode} = require(input.codec);
assert.deepStrictEqual(decode(JSON.parse(fs.readFileSync(input.current,'utf8'))), JSON.parse(fs.readFileSync(input.baseline,'utf8')));
const gzip = path => zlib.gzipSync(fs.readFileSync(path), {level:9}).length;
const loader = gzip(input.codec), index = gzip(input.index);
const result = {node:process.version, gzip:'gzipSync(buffer, { level: 9 })', javascriptDeepStrictEqual:true,
  baseline:{raw:fs.statSync(input.baseline).size,gzip:gzip(input.baseline)}, decoderGzip:loader,
  indexGzip:index, pageWithDecoder:index+loader, pageBudget:204800, pageHeadroom:204800-index-loader,
  cases:input.cases.map(({path,...data})=>({...data,raw:fs.statSync(path).size,gzip:gzip(path),
     totalWithDecoder:gzip(path)+loader,budget:409600,headroom:409600-gzip(path)-loader}))};
process.stdout.write(JSON.stringify(result,null,2));
"""
    result = subprocess.run([args.node, "-e", js], input=json.dumps({
        "codec": str(ROOT / "docs/sheet-transport.js"), "current": str(current_path),
        "baseline": str(args.baseline.resolve()), "index": str(ROOT / "docs/index.html"), "cases": cases
    }), text=True, capture_output=True, check=True)
    proof = json.loads(result.stdout)
    proof.update({"as_of": original["generated"], "data_through": original["data_through"],
                  "pythonTypeSensitiveEquality": True, "valuesAndContainersCompared": nodes,
                  "plainSha256": hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
                  "wireSha256": hashlib.sha256(current_path.read_bytes()).hexdigest(),
                  "growthLimit": "Synthetic record-count stress only; dates and series lengths fixed. Not a longevity or retention forecast.",
                  "payloadLimit": "gzip estimates, not captured HTTP transfer. Decoder charged against both unchanged budgets; fetched once."})
    (args.out / "capacity-proof.json").write_text(json.dumps(proof, indent=2) + "\n")
    print(json.dumps(proof, indent=2))


if __name__ == "__main__":
    main()
