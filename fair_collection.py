"""Model-fair collection primitives. No network calls or import-time writes."""
import calendar
import json
import math
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import median


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w") as out:
        json.dump(value, out, indent=1, sort_keys=True, allow_nan=False)
        out.write("\n")
        out.flush()
        import os
        os.fsync(out.fileno())
    temp.replace(path)


def read_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else ({} if default is None else default)


def model_key(t):
    return t["brand"] + "/" + t["model_key"]


def groups(targets):
    out = defaultdict(list)
    for t in targets.values():
        out[model_key(t)].append(t)
    return {k: sorted(v, key=lambda t: t["id"]) for k, v in sorted(out.items())}


def baseline(targets, calls_for, day_cap, month_cap):
    """A stable model turn; different trims occupy successive turns.

    Budget against a 31-day month, and check every residue of the model turn.
    Weights never move baseline offsets. Adding a model can change the plan;
    elapsed-success priority in the collector recovers any overdue target.
    """
    models = groups(targets)
    if not models:
        return 1
    for interval in range(1, 91):
        loads = [0] * interval
        for key, trims in sorted(models.items(), key=lambda kv: (-max(calls_for(t) for t in kv[1]), kv[0])):
            slot = min(range(interval), key=lambda i: (loads[i], i))
            loads[slot] += max(calls_for(t) for t in trims)
            for i, t in enumerate(trims):
                t.update(model_cadence=interval, cadence=interval * len(trims),
                         offset=(-(slot + interval * i)) % (interval * len(trims)))
        month_peak = max(sum(loads[(start + d) % interval] for d in range(31))
                         for start in range(interval))
        if max(loads) <= day_cap and month_peak <= month_cap:
            return interval
    raise ValueError("The guaranteed model turns cannot fit these API budgets.")


class RequestBudget:
    """Absolute daily charges, journalled before HTTP. Workflow serializes writers.

    max(legacy ledger, journal) prevents double counting and lets old spend
    seed the journal. A crash between reservation and HTTP may overcount one
    request; it cannot refund a request that might have reached the provider.
    """
    def __init__(self, day, ledger_path, journal_path, day_cap, month_cap):
        self.day, self.path = day, Path(journal_path)
        self.day_cap, self.month_cap = day_cap, month_cap
        ledger = read_json(ledger_path)
        self.charges = read_json(journal_path)
        if not isinstance(ledger, dict) or not isinstance(self.charges, dict):
            raise ValueError("Cannot verify API spend: malformed ledger or request journal")
        for d, row in ledger.items():
            n = row.get("actual") if isinstance(row, dict) else None
            self._validate(d, n)
            self.charges[d] = max(self.charges.get(d, 0), n)
        for d, n in self.charges.items():
            self._validate(d, n)
        self.start = self.charges.get(day, 0)

    @staticmethod
    def _validate(day, n):
        date.fromisoformat(day)
        if type(n) is not int or n < 0:
            raise ValueError("Cannot verify API spend: charges must be nonnegative integers")

    @property
    def day_spent(self):
        return self.charges.get(self.day, 0)

    @property
    def month_spent(self):
        return sum(n for d, n in self.charges.items() if d[:7] == self.day[:7])

    @property
    def remaining(self):
        return max(0, min(self.day_cap - self.day_spent, self.month_cap - self.month_spent))

    def charge(self):
        if not self.remaining:
            return False
        self.charges[self.day] = self.day_spent + 1
        atomic_json(self.path, self.charges)
        return True

    def bonus_remaining(self, future_baseline, reserve, planned_today, baseline_actual):
        """Pace windfalls over the remaining month; recycle today's early stops."""
        d = date.fromisoformat(self.day)
        remaining_days = calendar.monthrange(d.year, d.month)[1] - d.day + 1
        banked_today = max(0, planned_today - baseline_actual)
        # Recover the post-baseline starting balance even after bonus charges.
        bonus_spent = max(0, self.day_spent - self.start - baseline_actual)
        surplus = max(0, self.month_cap - self.month_spent + bonus_spent
                      - future_baseline - reserve)
        daily_share = min(surplus, banked_today + max(0, surplus - banked_today) // remaining_days)
        return max(0, min(self.remaining, daily_share - bonus_spent))


def valid_total(n):
    return type(n) is int and n >= 0


def signature(t):
    # National market count covers these API filters, before local row filters.
    return {"make": t["make"], "model": t["model"], "years": sorted(map(str, t["years"]))}


def fresh_market(state, key, t, day, age=14):
    m = state.get("markets", {}).get(key, {})
    try:
        elapsed = (date.fromisoformat(day) - date.fromisoformat(m["as_of"])).days
    except (KeyError, TypeError, ValueError):
        return None
    return m if (0 <= elapsed <= age and valid_total(m.get("total"))
                 and m.get("query") == signature(t)) else None


def bonus_order(targets, state, day, completed, latest):
    """Cold-start neutral; bounded volume and smoothed useful VINs per request.

    Only bonus allocation learns. Baseline service cannot be lost to a large
    market or a temporarily poor return. Divide by recent bonus spend to
    avoid repeatedly serving the first high-volume model.
    """
    by_model = groups(targets)
    markets = {k: fresh_market(state, k, ts[0], day) for k, ts in by_model.items()}
    known = [m["total"] for m in markets.values() if m is not None and m["total"] > 0]
    neutral = median(known) if known else 100
    weights, spent = {}, defaultdict(int)
    recent = [(d, obs) for d, rows in state.get("days", {}).items()
              if 0 <= (date.fromisoformat(day) - date.fromisoformat(d)).days < 7
              for obs in rows.values()]
    for key in by_model:
        observations = [r for d, r in recent if r.get("model") == key]
        calls = sum(r.get("calls", 0) for r in observations)
        useful = sum(r.get("useful", 0) for r in observations if r.get("complete"))
        # Two prior calls at ten useful VINs/call prevent single-run overfitting.
        efficiency = max(.25, min(2, (useful + 20) / (calls + 2) / 10))
        size = markets[key]["total"] if markets[key] is not None else neutral
        volume = max(.25, min(4, math.sqrt(max(1, size) / max(1, neutral))))
        weights[key] = volume * efficiency
        spent[key] = sum(r.get("bonus_calls", r.get("calls", 0) if r.get("bonus") else 0)
                         for d, r in recent if r.get("model") == key)
    week = date.fromisoformat(day)
    epoch = (week - timedelta(days=week.weekday())).isoformat()
    fingerprint = {k: signature(ts[0]) for k, ts in by_model.items()}
    frozen = state.setdefault("weights", {})
    if frozen.get("epoch") != epoch or frozen.get("queries") != fingerprint:
        frozen.clear()
        frozen.update(epoch=epoch, queries=fingerprint, values=weights)
    weights = frozen["values"]
    def rank(t):
        prev = latest.get(t["id"])
        elapsed = (date.fromisoformat(day) - date.fromisoformat(prev)).days if prev else 9999
        overdue = elapsed / t["cadence"]
        return (-(overdue >= 1), -overdue if overdue >= 1 else 0,
                -weights[model_key(t)] / (1 + spent[model_key(t)]), -elapsed, t["id"])
    return sorted((t for t in targets.values() if t["id"] not in completed
                   and latest.get(t["id"]) != day), key=rank)


def report(targets, state, day, budget, calls_for):
    result = {"strategy": "fair-model-turns", "as_of": day, "reserve": 50,
              "exploration_share": .5,
              "daily_cap": budget.day_cap, "monthly_cap": budget.month_cap,
              "month_spent": budget.month_spent, "models": []}
    for key, ts in groups(targets).items():
        m = fresh_market(state, key, ts[0], day)
        observations = [r for d, rows in state.get("days", {}).items()
                        if 0 <= (date.fromisoformat(day) - date.fromisoformat(d)).days < 7
                        for r in rows.values() if r.get("model") == key]
        deep = [q for r in observations for q in r.get("queries", []) if q.get("exploration")]
        result["models"].append({"id": key, "label": ts[0]["brand_label"] + " " + ts[0]["model_label"],
            "model_cadence": ts[0]["model_cadence"], "trim_cadence": ts[0]["cadence"],
            "market_total": m["total"] if m else None, "market_as_of": m["as_of"] if m else None,
            "calls_7d": sum(r.get("calls", 0) for r in observations),
            "useful_7d": sum(r.get("useful", 0) for r in observations),
            "exploration_calls_7d": sum(q.get("calls", 0) for q in deep),
            "exploration_useful_7d": sum(q.get("new", 0) + q.get("changed", 0) for q in deep),
            "deepest_page_7d": max((q["page"] for q in deep if not q.get("failed")), default=None),
            "baseline_calls_per_day": sum(calls_for(t) / t["cadence"] for t in ts)})
    return result


def exploration_identity(t, name, source):
    return {**signature(t), "trim": t.get("trim_query", ""), "source": name,
            "region": source, "sort": "price.asc"}


def exploration_candidates(targets, state, day, observations, sources_for, per_page, attempted):
    """Eligible same-run scopes, ranked by least recent exploration then yield.

    Breadth first across models, then progress further where allowance remains.
    Each page is attempted once per run; cursors belong to their exact query.
    """
    possible = []
    for tid, obs in observations.items():
        if not obs.get("complete") or tid not in targets:
            continue
        t = targets[tid]
        for name, source in sources_for(t):
            key = tid + "|" + name
            first = [q for q in obs.get("queries", []) if q.get("source") == name
                     and q.get("page") == 1 and not q.get("census") and not q.get("exploration")]
            # A short first page under any sort covers that entire raw scope.
            if not first or any(q.get("failed") or q.get("raw", 0) < per_page for q in first):
                continue
            total = next((q["total"] for q in reversed(first) if valid_total(q.get("total"))), None)
            if total is not None and total <= per_page:
                continue
            identity = exploration_identity(t, name, source)
            saved = state.get("exploration", {}).get(key, {})
            if saved.get("query") != identity:
                saved = {}
            page = saved.get("next_page", 2)
            if type(page) is not int or page < 2:
                page = 2
            cursor = saved.get("cursor")
            if ((total is not None and page > math.ceil(total / per_page))
                    or (page > 50 and not cursor)):
                page, cursor = 2, None
            if (key, page) in attempted:
                continue
            possible.append({"key": key, "target": t, "source_name": name, "source": source,
                             "page": page, "cursor": cursor, "query": identity,
                             "last_attempt": saved.get("last_attempt", "")})
    # Reuse bounded weekly model weights while allowing per-request fair debt
    # to include this run's exploration. Older unserved scopes always get a turn.
    view = {**state, "days": {**state.get("days", {}), day: observations}}
    bonus_order(targets, view, day, set(), {})
    weights = view.get("weights", {}).get("values", {})
    recent = [r for d, rows in view["days"].items()
              if 0 <= (date.fromisoformat(day) - date.fromisoformat(d)).days < 7
              for r in rows.values()]
    def rank(c):
        mk = model_key(c["target"])
        spent = sum(r.get("bonus_calls", r.get("calls", 0) if r.get("bonus") else 0)
                    for r in recent if r.get("model") == mk)
        return (c["last_attempt"], -weights.get(mk, 1) / (1 + spent), c["key"])
    return sorted(possible, key=rank)


def advance_exploration(state, candidate, day, q, next_cursor, per_page):
    """Commit progress only after a successful page; wrap a completed sweep."""
    record = {"query": candidate["query"], "last_attempt": day,
              "next_page": candidate["page"], "cursor": candidate["cursor"]}
    if q["failed"] and candidate["cursor"]:
        record.update(next_page=2, cursor=None)  # expired/invalid opaque cursors recover
    if not q["failed"]:
        page = candidate["page"]
        total = q.get("total")
        ended = q["raw"] < per_page or (valid_total(total) and page * per_page >= total)
        record.update(next_page=2 if ended or (page >= 50 and not next_cursor) else page + 1,
                      cursor=None if ended else next_cursor, last_success=day)
    state.setdefault("exploration", {})[candidate["key"]] = record


def run(T):
    """Collect whole target observations, then recycle allowance in this run."""
    from collections import Counter
    import sys
    state_path = T.DATA / "collection.json"
    state = read_json(state_path)
    if not isinstance(state, dict):
        raise ValueError("Malformed collection state")
    state.setdefault("days", {})
    state.setdefault("markets", {})
    state.setdefault("weights", {})
    state.setdefault("exploration", {})
    history = T.load_history()
    facts = T.load_fetch_log()
    budget = RequestBudget(T.TODAY, T.SPEND_LOG, T.DATA / "requests.json", T.BUDGET, T.MONTHLY)
    T.REQUEST_JOURNAL = budget
    T.REQUEST_ALLOWANCE = budget.remaining
    # A completed empty observation is a real run too; snapshot-only guards
    # would otherwise charge for it again on every dispatch.
    if (T.TODAY in state["days"] or any(r["snapshot_date"] == T.TODAY for r in history)) and not T.os.environ.get("ALLOW_REFETCH"):
        print("Today already has observations. Rebuilding outputs without API calls.")
        sys.exit(T.ALREADY_FETCHED)
    if T.os.environ.get("FILL_MISSING") == "1" or not budget.remaining:
        print("The fair collector handles first coverage in its regular run; no separate paid pass.")
        sys.exit(T.ALREADY_FETCHED)

    latest = {}
    for d, per in sorted(facts.items()):
        for tid, sources in per.items():
            if tid in T.TARGETS and all(name in sources and not sources[name].get("failed")
                                       for name, _ in T.sources_for(T.TARGETS[tid])):
                latest[tid] = d
    fallback = {}
    for r in history:
        fallback[r["target"]] = max(r["snapshot_date"], fallback.get(r["target"], ""))
    for tid, d in fallback.items():
        latest.setdefault(tid, d)
    known = {}
    for r in sorted(history, key=lambda r: r["snapshot_date"]):
        known[r["vin"]] = (T.to_int(r["price"]), T.to_int(r["miles"]))
    prior_observations = state["days"].get(T.TODAY, {})
    seen_useful = {vin for obs in prior_observations.values() for vin in obs.get("useful_vins", [])}
    rows, via = {}, defaultdict(set)
    dropped = Counter()
    observations, completed = {}, set()
    scheduled = [t for t in T.TARGETS.values() if T.due_on(t, T.TODAY_ORD)]
    scheduled.sort(key=lambda t: (latest.get(t["id"], ""), t["id"]))
    planned = sum(T.calls_for(t) for t in scheduled)
    end = date.fromisoformat(T.TODAY).replace(day=calendar.monthrange(int(T.TODAY[:4]), int(T.TODAY[5:7]))[1]).toordinal()
    future = sum(T.calls_for(t) for ordinal in range(T.TODAY_ORD + 1, end + 1)
                 for t in T.TARGETS.values() if T.due_on(t, ordinal))

    def query(t, source_name, source, sort, page=1, census=False, exploration=False, cursor=None):
        tid, key = t["id"], model_key(t)
        request_target = ({**t, "id": "census:" + key, "trim_query": ""} if census else
                          {**t, "id": "explore:" + tid + ":" + source_name, "_cursor": cursor} if exploration else t)
        before = T.CALLS
        batch = T.fetch(source_name, source, sort, page, request_target)
        q = {"source": source_name, "sort": sort, "page": page, "census": census,
             "exploration": exploration,
             "calls": T.CALLS - before, "raw": len(batch) if batch is not None else 0,
             "kept": 0, "new": 0, "changed": 0, "failed": batch is None}
        observations[tid]["queries"].append(q)
        if batch is None:
            if not census and not exploration:
                T.FAILED_SCOPES.add((tid, source_name))
            return q
        total = T.TOTALS.get((request_target["id"], source_name))
        q["total"] = total
        if source_name == "National" and not request_target.get("trim_query") and valid_total(total):
            state["markets"][key] = {"total": total, "as_of": T.TODAY, "query": signature(t)}
        if not census and not exploration:
            T.RAW_N[(tid, source_name)] += len(batch)
            T.SOURCE_VINS.setdefault((tid, source_name), set())
            if len(batch) < T.PER_PAGE:
                T.EXHAUSTED.add((tid, source_name))
        accepted = set()
        for rec in batch:
            n = T.normalize(rec, t, dropped)
            if not n:
                continue
            vin = n["vin"]
            accepted.add(vin)
            if not census and not exploration:
                T.SOURCE_VINS[(tid, source_name)].add(vin)
                if sort == "price.asc":
                    T.PRICE_WINDOW[(tid, source_name)] = max(T.PRICE_WINDOW.get((tid, source_name), 0), n["price"])
            via[(tid, vin)].add(f"{source_name}:{sort}" + (f":page={page}" if exploration else ""))
            cur = rows.get((tid, vin))
            if cur is None or n["price"] < T.to_int(cur["price"]):
                rows[(tid, vin)] = n
            if vin not in seen_useful:
                if vin not in known:
                    q["new"] += 1
                    seen_useful.add(vin)
                    observations[tid].setdefault("useful_vins", []).append(vin)
                elif known[vin] != (T.to_int(n["price"]), T.to_int(n["miles"])):
                    q["changed"] += 1
                    seen_useful.add(vin)
                    observations[tid].setdefault("useful_vins", []).append(vin)
        q["kept"] = len(accepted)
        q["rejected"] = len(batch) - len(accepted)
        return q

    def observe(t, bonus=False):
        tid = t["id"]
        start = T.CALLS
        observations[tid] = {"model": model_key(t), "bonus": bonus, "queries": []}
        for name, source in T.sources_for(t):
            query(t, name, source, "price.asc")
        if (tid, "National") not in T.EXHAUSTED and (tid, "National") not in T.FAILED_SCOPES:
            query(t, "National", None, T.NEWEST_SORT)
        completed.add(tid)
        obs = observations[tid]
        obs.update(calls=T.CALLS - start, bonus_calls=T.CALLS - start if bonus else 0,
                   useful=sum(q["new"] + q["changed"] for q in obs["queries"]),
                   complete=not any(q["failed"] for q in obs["queries"]))
        T.KEPT_N[tid] = sum(k[0] == tid for k in rows)
        print(f"{tid}: {obs['calls']} calls, {T.KEPT_N[tid]} unique kept, {obs['useful']} new/changed" + (" (extra turn)" if bonus else ""))

    for t in scheduled:
        if budget.remaining < T.calls_for(t):
            break
        observe(t)
    baseline_actual = T.CALLS
    reserve = int(T.FAIR.get("reserve", 50))
    extra = lambda: budget.bonus_remaining(future, reserve, planned, baseline_actual)
    attempted_deep = set()

    def explore(allowance):
        """Single-page requests can use an otherwise stranded final call."""
        start = T.CALLS
        while extra() > 0 and T.CALLS - start < allowance:
            choices = exploration_candidates(T.TARGETS, state, T.TODAY, observations,
                                             T.sources_for, T.PER_PAGE, attempted_deep)
            if not choices:
                break
            c = choices[0]
            attempted_deep.add((c["key"], c["page"]))
            t = c["target"]
            before = T.CALLS
            # Retries share both the exploration allocation and the spare cap.
            T.REQUEST_ALLOWANCE = T.CALLS + min(extra(), allowance - (T.CALLS - start))
            q = query(t, c["source_name"], c["source"], "price.asc", c["page"],
                      exploration=True, cursor=c["cursor"])
            obs = observations[t["id"]]
            obs["calls"] += T.CALLS - before
            obs["bonus_calls"] += T.CALLS - before
            obs["useful"] = sum(x["new"] + x["changed"] for x in obs["queries"])
            T.KEPT_N[t["id"]] = sum(k[0] == t["id"] for k in rows)
            next_cursor = T.NEXT_CURSORS.get(("explore:" + t["id"] + ":" + c["source_name"], c["source_name"]))
            advance_exploration(state, c, T.TODAY, q, next_cursor, T.PER_PAGE)

    # Reserve depth first, so census requests cannot consume its allocation.
    explore(math.ceil(extra() * .5))
    # Broad counts for trim-split models must be measured separately. Summing
    # overlapping trim/CPO totals fabricates a market size. At most one dated
    # census per model per week; retained matching rows join this observation.
    for t in scheduled:
        if t["id"] not in observations or not observations[t["id"]]["complete"] or not t.get("trim_query"):
            continue
        if extra() < 2:  # one request plus its possible retry
            break
        if fresh_market(state, model_key(t), t, T.TODAY, age=6) is None:
            before = T.CALLS
            T.REQUEST_ALLOWANCE = T.CALLS + extra()
            query(t, "National", None, "price.asc", census=True)
            obs = observations[t["id"]]
            obs["calls"] += T.CALLS - before
            obs["bonus_calls"] += T.CALLS - before
            obs["useful"] = sum(q["new"] + q["changed"] for q in obs["queries"])
    # Re-rank after each completed extra turn using this run's observations.
    while extra() >= 3:
        view = {**state, "days": {**state["days"], T.TODAY: observations}}
        candidates = bonus_order(T.TARGETS, view, T.TODAY, completed, latest)
        if not candidates:
            break
        t = candidates[0]
        if extra() < T.calls_for(t):
            break
        T.REQUEST_ALLOWANCE = T.CALLS + extra()
        observe(t, bonus=True)

    # Release unused census/whole-turn capacity to depth, including 1–2 calls.
    explore(extra())

    if not observations:
        print("No complete observation fits the remaining budget.")
        sys.exit(T.ALREADY_FETCHED)
    for (tid, vin), row in rows.items():
        row["via"] = "|".join(sorted(via[(tid, vin)]))
    # An interrupted target is unknown, so preserve its prior snapshot; no
    # partial newest/price slice can replace a complete inventory observation.
    successful = {tid for tid, obs in observations.items() if obs["complete"]}
    new_rows = [r for (tid, _), r in rows.items() if tid in successful]
    all_rows = [r for r in history if not (r["snapshot_date"] == T.TODAY and r["target"] in successful)] + new_rows
    T.write_rows(all_rows)
    fetch_row = T.fetch_log_row()
    for tid, sources in fetch_row.items():
        for fact in sources.values():
            fact["observation_complete"] = tid in successful
    # Failed refetches retain the earlier successful observation, including an
    # empty one. Replacing its reach facts would resurrect stale inventory.
    fetch_row = {tid: sources for tid, sources in fetch_row.items()
                 if tid in successful or not any(f.get("observation_complete")
                     for f in facts.get(T.TODAY, {}).get(tid, {}).values())}
    T.save_fetch_log(fetch_row, merge_targets=True)
    T.OVERLAP.update(T.source_overlap(rows))
    T.save_overlap_history(T.OVERLAP, merge_targets=True)
    merged = {**prior_observations}
    for tid, obs in observations.items():
        prior = prior_observations.get(tid, {})
        merged[tid] = {**obs, "calls": prior.get("calls", 0) + obs["calls"],
                       "useful": prior.get("useful", 0) + (obs["useful"] if obs["complete"] else 0),
                       "bonus_calls": prior.get("bonus_calls", prior.get("calls", 0) if prior.get("bonus") else 0)
                           + obs["bonus_calls"],
                       "complete": obs["complete"] or prior.get("complete", False),
                       "queries": prior.get("queries", []) + obs["queries"],
                       "useful_vins": sorted(set(prior.get("useful_vins", [])) | set(obs.get("useful_vins", [])))}
    state["days"][T.TODAY] = merged
    state["days"] = {d: v for d, v in state["days"].items() if d >= (date.fromisoformat(T.TODAY) - timedelta(days=35)).isoformat()}
    atomic_json(state_path, state)
    # Journal is authoritative, including a request from an earlier crashed run.
    ledger = read_json(T.SPEND_LOG)
    page_requests = sum(1 for o in observations.values() for q in o["queries"]
                        if q.get("census") or q.get("exploration"))
    row = T.spend_report(planned + page_requests + sum(T.calls_for(T.TARGETS[tid]) for tid, o in observations.items() if o["bonus"]),
                         targets=[T.TARGETS[tid] for tid in observations])
    row.update(actual=budget.day_spent, runs=(ledger.get(T.TODAY, {}).get("runs", 0) + 1))
    row["banked"] = row["planned"] - row["actual"] - row["unrun"]
    ledger[T.TODAY] = row
    atomic_json(T.SPEND_LOG, ledger)
    T.save_zip_cache()
    today_rows = [r for r in all_rows if r["snapshot_date"] == T.TODAY]
    report_text, site, _ = T.build_outputs(today_rows, all_rows, T.build_history(all_rows))
    Path("REPORT.md").write_text(report_text)
    atomic_json(T.DOCS / "data.json", site)
    T.update_sheet_size(site)
    print(f"Fair collection: {len(successful)} complete observations, {T.CALLS} requests, {budget.month_spent}/{T.MONTHLY} recorded this month.")
