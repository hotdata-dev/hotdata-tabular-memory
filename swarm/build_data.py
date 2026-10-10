"""Build the swarm benchmark's data: trips with a key, and which trips get a complaint.

The trips are the January 2024 TLC file with `trip_id` added (its row number in the
file), so a complaint can name the trip it is about and a label can be joined back.

Each complaint is assigned its true category here, before any text exists. The
category depends on the trip — airport rides draw fare disputes, late-night rides
draw safety complaints — so the share of each category differs by zone and time,
and a question about one slice has a different answer from a question about another.

Run once:  python3 build_data.py  ->  data/trips.parquet, data/complaint_specs.jsonl
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SOURCE = DATA / "yellow_tripdata_2024-01.parquet"
ZONES = DATA / "taxi_zone_lookup.csv"
TRIPS = DATA / "trips.parquet"
SPECS = DATA / "complaint_specs.jsonl"

CATEGORIES = ["fare_dispute", "detour", "driver_conduct", "vehicle_condition",
              "lost_item", "safety"]

AIRPORTS = (1, 132, 138)                                   # Newark, JFK, LaGuardia
MIDTOWN = (100, 161, 162, 163, 164, 170, 186, 230, 234, 48)

CLEAN = """
  fare_amount > 0 AND total_amount > 0 AND trip_distance > 0
  AND tpep_pickup_datetime >= TIMESTAMP '2024-01-01'
  AND tpep_pickup_datetime < TIMESTAMP '2024-02-01'
  AND tpep_dropoff_datetime > tpep_pickup_datetime
"""

# How many complaints each slice gets. Tasks filter on these slices, so they overlap.
STRATA = {
    "airport": (900, f'("PULocationID" IN {AIRPORTS} OR "DOLocationID" IN {AIRPORTS})'),
    "midtown": (700, f'"PULocationID" IN {MIDTOWN}'),
    "night": (400, "(EXTRACT(HOUR FROM tpep_pickup_datetime) >= 22 "
                   "OR EXTRACT(HOUR FROM tpep_pickup_datetime) < 5)"),
    "elsewhere": (400, "TRUE"),
}


def weights(trip: dict) -> list[float]:
    """The odds of each category for this trip."""
    w = dict.fromkeys(CATEGORIES, 1.0)
    airport = trip["pu"] in AIRPORTS or trip["do"] in AIRPORTS
    hour = trip["pickup"].hour
    if airport:
        w["fare_dispute"] *= 3
        w["lost_item"] *= 2
    if hour >= 22 or hour < 5:
        w["safety"] *= 3
        w["driver_conduct"] *= 2
    if trip["miles"] > 8 and not airport:
        w["detour"] *= 3
    if 15 <= trip["pickup"].day <= 21:                     # the week of the cold snap
        w["vehicle_condition"] *= 2
    return [w[c] for c in CATEGORIES]


def main() -> None:
    con = duckdb.connect()
    con.execute(f"""
        COPY (SELECT file_row_number + 1 AS trip_id, * EXCLUDE (file_row_number)
              FROM read_parquet('{SOURCE}', file_row_number = true)
              ORDER BY trip_id)
        TO '{TRIPS}' (FORMAT parquet)""")
    con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{TRIPS}')")
    con.execute(f"CREATE VIEW z AS SELECT * FROM read_csv_auto('{ZONES}')")

    taken: set[int] = set()
    picked: list[tuple[str, dict]] = []
    for stratum, (n, where) in STRATA.items():
        # hash() orders deterministically, so a rebuild picks the same trips.
        rows = con.execute(f"""
            SELECT t.trip_id, t."PULocationID", t."DOLocationID", pz."Zone", dz."Zone",
                   t.tpep_pickup_datetime, t.trip_distance, t.fare_amount, t.total_amount,
                   t.tip_amount, t.passenger_count, t.payment_type
            FROM t JOIN z pz ON pz."LocationID" = t."PULocationID"
                   JOIN z dz ON dz."LocationID" = t."DOLocationID"
            WHERE {CLEAN} AND {where}
            ORDER BY hash(t.trip_id) LIMIT {n + len(taken)}""").fetchall()
        fresh = [r for r in rows if r[0] not in taken][:n]
        for r in fresh:
            taken.add(r[0])
            picked.append((stratum, {
                "trip_id": r[0], "pu": r[1], "do": r[2], "pickup_zone": r[3],
                "dropoff_zone": r[4], "pickup": r[5], "miles": r[6], "fare": r[7],
                "total": r[8], "tip": r[9], "passengers": r[10], "card": r[11] == 1}))

    rng = random.Random(42)
    with SPECS.open("w") as out:
        for i, (stratum, trip) in enumerate(picked, 1):
            category = rng.choices(CATEGORIES, weights(trip))[0]
            out.write(json.dumps({
                "complaint_id": i, "category": category, "stratum": stratum,
                **{k: v for k, v in trip.items() if k != "pickup"},
                "pickup": trip["pickup"].isoformat(sep=" ")}) + "\n")

    counts = dict.fromkeys(CATEGORIES, 0)
    for line in SPECS.read_text().splitlines():
        counts[json.loads(line)["category"]] += 1
    print(f"{con.execute('SELECT count(*) FROM t').fetchone()[0]:,} trips -> {TRIPS.name}")
    print(f"{len(picked)} complaint specs -> {SPECS.name}")
    for c, n in counts.items():
        print(f"  {c:18} {n}")


if __name__ == "__main__":
    main()
