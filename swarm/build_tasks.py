"""Build the swarm task bank and its truth.

Each task asks about one slice of trips — JFK pickups, Midtown in the cold-snap week,
rides at night — and needs the complaints on those trips classified to answer. Slices
overlap, so tasks share classification work: that overlap is what a shared label table
can save and an isolated agent cannot.

A task is kept only if its answer survives a careless reader: with 5% of labels
flipped at random, the answer must still be within tolerance in 95% of 200 trials.
Otherwise a task would grade luck, not understanding.

  python3 build_tasks.py   ->  tasks.json
"""

from __future__ import annotations

import json
import random
import statistics
from pathlib import Path

import duckdb

from build_data import AIRPORTS, CATEGORIES, CLEAN, MIDTOWN

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
TASKS = HERE / "tasks.json"
N_TASKS = 60
MIN_SLICE, MAX_SLICE = 40, 260
PER_SLICE = 4
FLIP, TRIALS, SURVIVE = 0.05, 200, 0.95

WEEKS = {1: (1, 7), 2: (8, 14), 3: (15, 21), 4: (22, 31)}
NAMES = {"fare_dispute": "fare disputes", "detour": "detours or bad routes",
         "driver_conduct": "driver conduct", "vehicle_condition": "vehicle condition",
         "lost_item": "lost items", "safety": "safety"}


def slices() -> list[tuple[str, str, callable]]:
    """(key, how a task describes it, predicate over a complaint's trip)."""
    night = lambda r: r["hour"] >= 22 or r["hour"] < 5
    air = lambda r: r["pu"] in AIRPORTS or r["do"] in AIRPORTS
    out = [
        ("jfk_pu", "trips picked up at JFK Airport (zone 132)", lambda r: r["pu"] == 132),
        ("jfk_do", "trips dropped off at JFK Airport (zone 132)", lambda r: r["do"] == 132),
        ("jfk", "trips to or from JFK Airport (zone 132)", lambda r: 132 in (r["pu"], r["do"])),
        ("lga", "trips to or from LaGuardia Airport (zone 138)", lambda r: 138 in (r["pu"], r["do"])),
        ("ewr", "trips to or from Newark Airport (zone 1)", lambda r: 1 in (r["pu"], r["do"])),
        ("air", "trips to or from any of the three airports (zones 1, 132, 138)", air),
        ("tsq", "trips picked up in Times Sq/Theatre District (zone 230)", lambda r: r["pu"] == 230),
        ("midtown", f"trips picked up in Midtown (zones {', '.join(map(str, MIDTOWN))})",
         lambda r: r["pu"] in MIDTOWN),
        ("night", "trips picked up between 22:00 and 04:59", night),
        ("night_midtown", f"trips picked up in Midtown (zones {', '.join(map(str, MIDTOWN))}) "
                          "between 22:00 and 04:59", lambda r: night(r) and r["pu"] in MIDTOWN),
        ("night_air", "airport trips (to or from zones 1, 132, 138) picked up between 22:00 "
                      "and 04:59", lambda r: night(r) and air(r)),
        ("long", "trips longer than 8 miles that neither start nor end at an airport "
                 "(zones 1, 132, 138)", lambda r: r["miles"] > 8 and not air(r)),
    ]
    for w, (a, b) in WEEKS.items():
        span = f"picked up from January {a} to January {b} inclusive"
        out.append((f"wk{w}", f"trips {span}", lambda r, a=a, b=b: a <= r["day"] <= b))
        out.append((f"air_wk{w}", f"airport trips (to or from zones 1, 132, 138) {span}",
                    lambda r, a=a, b=b: air(r) and a <= r["day"] <= b))
        out.append((f"jfk_pu_wk{w}", f"trips picked up at JFK Airport (zone 132) {span}",
                    lambda r, a=a, b=b: r["pu"] == 132 and a <= r["day"] <= b))
        out.append((f"night_wk{w}", f"trips {span}, picked up between 22:00 and 04:59",
                    lambda r, a=a, b=b: night(r) and a <= r["day"] <= b))
        out.append((f"midtown_wk{w}", f"Midtown pickups (zones {', '.join(map(str, MIDTOWN))}) "
                                      f"{span}", lambda r, a=a, b=b: r["pu"] in MIDTOWN
                                      and a <= r["day"] <= b))
    for a, b in ((1, 15), (16, 31)):
        out.append((f"lga_{a}_{b}", f"trips to or from LaGuardia Airport (zone 138) picked up "
                                    f"from January {a} to January {b} inclusive",
                    lambda r, a=a, b=b: 138 in (r["pu"], r["do"]) and a <= r["day"] <= b))
    return out


# Each template returns (ask, answer function over labels, kind, tolerance) or None.
def share(desc, cat):
    def f(rows, lab):
        return round(100 * sum(lab[r["cid"]] == cat for r in rows) / len(rows), 1)
    return (f"Among complaints about {desc}, what percentage are mainly about "
            f"{NAMES[cat]}? One decimal.", f, "number", 3.0)


def count(desc, cat):
    def f(rows, lab):
        return sum(lab[r["cid"]] == cat for r in rows)
    return (f"How many complaints about {desc} are mainly about {NAMES[cat]}?",
            f, "count", 0.10)


def top(desc, _):
    def f(rows, lab):
        n = {c: 0 for c in CATEGORIES}
        for r in rows:
            n[lab[r["cid"]]] += 1
        return max(CATEGORIES, key=lambda c: (n[c], -CATEGORIES.index(c)))
    return (f"Which category of complaint is most common among complaints about {desc}? "
            f"Answer with one of: {', '.join(CATEGORIES)}.", f, "string", 0)


def median_total(desc, cat):
    def f(rows, lab):
        xs = [r["total"] for r in rows if lab[r["cid"]] == cat]
        return round(statistics.median(xs), 2) if xs else None
    return (f"What is the median total_amount of {desc} that drew a complaint mainly "
            f"about {NAMES[cat]}? Two decimals.", f, "number_rel", 0.05)


TEMPLATES = [share, count, top, median_total]


def within(kind, tol, truth, got) -> bool:
    if truth is None or got is None:
        return truth == got
    if kind == "string":
        return truth == got
    if kind == "number":
        return abs(got - truth) <= tol
    if kind == "count":
        return abs(got - truth) <= max(2, tol * truth)
    return abs(got - truth) <= tol * abs(truth)                      # number_rel


def main() -> None:
    con = duckdb.connect()
    rows = con.execute(f"""
        SELECT c.complaint_id, t."PULocationID", t."DOLocationID",
               EXTRACT(HOUR FROM t.tpep_pickup_datetime), EXTRACT(DAY FROM t.tpep_pickup_datetime),
               t.trip_distance, t.total_amount
        FROM read_parquet('{DATA / "complaints.parquet"}') c
        JOIN read_parquet('{DATA / "trips.parquet"}') t USING (trip_id)
        WHERE {CLEAN}""").fetchall()
    rows = [dict(zip(["cid", "pu", "do", "hour", "day", "miles", "total"], r)) for r in rows]
    truth = {json.loads(l)["complaint_id"]: json.loads(l)["category"]
             for l in (DATA / "truth.jsonl").read_text().splitlines()}
    rng = random.Random(7)

    candidates = []
    for key, desc, pred in slices():
        sl = [r for r in rows if pred(r)]
        if not MIN_SLICE <= len(sl) <= MAX_SLICE:
            continue
        for template in TEMPLATES:
            for cat in (CATEGORIES if template is not top else [None]):
                ask, f, kind, tol = template(desc, cat)
                answer = f(sl, truth)
                if answer is None or (kind in ("count", "number") and answer < 5):
                    continue                         # too rare to ask about fairly
                ok = 0
                for _ in range(TRIALS):
                    noisy = dict(truth)
                    for r in sl:
                        if rng.random() < FLIP:
                            noisy[r["cid"]] = rng.choice([c for c in CATEGORIES if c != truth[r["cid"]]])
                    ok += within(kind, tol, answer, f(sl, noisy))
                if ok / TRIALS >= SURVIVE:
                    candidates.append({"slice": key, "ask": ask, "kind": kind, "tol": tol,
                                       "answer": answer, "needs": sorted(r["cid"] for r in sl),
                                       "survival": ok / TRIALS})

    # One task per slice-and-template first, so the bank spreads across slices, then fill.
    rng.shuffle(candidates)
    seen, picked = set(), []
    for c in candidates:
        k = (c["slice"], c["ask"].split()[0])
        if k not in seen:
            seen.add(k)
            picked.append(c)
    picked += [c for c in candidates if c not in picked]
    per: dict[str, int] = {}
    capped = []
    for c in picked:
        if per.get(c["slice"], 0) < PER_SLICE:
            per[c["slice"]] = per.get(c["slice"], 0) + 1
            capped.append(c)
    picked = capped[:N_TASKS]
    rng.shuffle(picked)
    for i, t in enumerate(picked, 1):
        t["id"] = f"t{i:02d}"

    TASKS.write_text(json.dumps(picked, indent=1) + "\n")
    needed = [len(t["needs"]) for t in picked]
    distinct = len({c for t in picked for c in t["needs"]})
    print(f"{len(candidates)} robust candidates, {len(picked)} tasks over "
          f"{len({t['slice'] for t in picked})} slices -> {TASKS.name}")
    print(f"complaints per task: min {min(needed)}, median {statistics.median(needed)}, "
          f"max {max(needed)}; {sum(needed)} needed in all, {distinct} distinct "
          f"({sum(needed) / distinct:.1f}x reuse available)")
    for kind in ("number", "count", "string", "number_rel"):
        print(f"  {kind:10} {sum(t['kind'] == kind for t in picked)}")


if __name__ == "__main__":
    main()
