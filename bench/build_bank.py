"""Build the benchmark question bank and compute its ground truth with DuckDB.

Each question has one deterministic answer. The same parquet that was loaded into
Hotdata is read here, so a correct agent answer and the truth come from the same rows.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
TRIPS = str(HERE / "yellow_2024-01.parquet")
ZONES = str(HERE / "taxi_zone_lookup.csv")

# The filter every sound answer needs. Discovering it is the point of phase 1.
CLEAN = """
  fare_amount > 0 AND total_amount > 0 AND trip_distance > 0
  AND tpep_pickup_datetime >= TIMESTAMP '2024-01-01'
  AND tpep_pickup_datetime < TIMESTAMP '2024-02-01'
  AND tpep_dropoff_datetime > tpep_pickup_datetime
"""

Q: list[dict] = []


def add(phase: int, ask: str, sql: str, kind: str = "number", tol: float = 0.01) -> None:
    Q.append({"id": f"q{len(Q) + 1:02d}", "phase": phase, "ask": ask, "sql": sql,
              "kind": kind, "tol": tol})


# ---- phase 1: discovery. Schema, scale, and the traps. -----------------------------

add(1, "How many rows are in the trips table?", "SELECT count(*) FROM t", "number", 0)
add(1, "How many columns does the trips table have?",
    "SELECT count(*) FROM (DESCRIBE SELECT * FROM t)", "number", 0)
add(1, "How many trips have a fare_amount of zero or less?",
    "SELECT count(*) FROM t WHERE fare_amount <= 0", "number", 0)
add(1, "How many trips have a tip_amount greater than their fare_amount?",
    "SELECT count(*) FROM t WHERE tip_amount > fare_amount", "number", 0)
add(1, "How many trips have a pickup datetime outside January 2024?",
    "SELECT count(*) FROM t WHERE tpep_pickup_datetime < TIMESTAMP '2024-01-01'"
    " OR tpep_pickup_datetime >= TIMESTAMP '2024-02-01'", "number", 0)
add(1, "How many trips have a trip_distance of zero?",
    "SELECT count(*) FROM t WHERE trip_distance = 0", "number", 0)
add(1, "How many trips remain after removing rows with a non-positive fare_amount, "
       "non-positive total_amount, non-positive trip_distance, a pickup outside "
       "January 2024, or a dropoff at or before the pickup? Call this the clean set; "
       "every later question means the clean set when it says trips.",
    f"SELECT count(*) FROM t WHERE {CLEAN}", "number", 0)
add(1, "How many distinct PULocationID values appear in the clean set?",
    f"SELECT count(DISTINCT \"PULocationID\") FROM t WHERE {CLEAN}", "number", 0)
add(1, "How many rows does the taxi zone lookup table have?",
    "SELECT count(*) FROM z", "number", 0)
add(1, "Which borough does LocationID 132 belong to?",
    "SELECT \"Borough\" FROM z WHERE \"LocationID\" = 132", "string")

# ---- phase 2: one route at a time. -------------------------------------------------

ROUTES = [(132, 230), (138, 161), (161, 237), (237, 236), (186, 79)]
for pu, do in ROUTES:
    add(2, f"In the clean set, how many trips went from LocationID {pu} to {do}?",
        f"SELECT count(*) FROM t WHERE {CLEAN} AND \"PULocationID\"={pu} "
        f"AND \"DOLocationID\"={do}", "number", 0)
    add(2, f"In the clean set, what is the mean total_amount for trips from LocationID "
           f"{pu} to {do}? Give dollars to two decimals.",
        f"SELECT round(avg(total_amount), 2) FROM t WHERE {CLEAN} "
        f"AND \"PULocationID\"={pu} AND \"DOLocationID\"={do}")
    add(2, f"In the clean set, what is the median trip duration in minutes for trips "
           f"from LocationID {pu} to {do}? Give minutes to two decimals.",
        f"SELECT round(median(EXTRACT(EPOCH FROM (tpep_dropoff_datetime "
        f"- tpep_pickup_datetime)) / 60.0), 2) FROM t WHERE {CLEAN} "
        f"AND \"PULocationID\"={pu} AND \"DOLocationID\"={do}")

# ---- phase 3: comparisons across routes. -------------------------------------------

BUSY = f"""
  SELECT "PULocationID" AS pu, "DOLocationID" AS do_id, count(*) AS n,
         avg(total_amount) AS fare,
         avg(EXTRACT(EPOCH FROM (tpep_dropoff_datetime - tpep_pickup_datetime)) / 60.0) AS mins,
         avg(trip_distance) AS miles,
         avg(tip_amount / nullif(fare_amount, 0)) AS tip_rate
  FROM t WHERE {CLEAN}
  GROUP BY 1, 2 HAVING count(*) >= 500
"""
add(3, "Among pickup-dropoff LocationID pairs in the clean set with at least 500 trips, "
       "which pair has the highest mean total_amount? Answer as PU-DO, e.g. 132-230.",
    f"SELECT pu || '-' || do_id FROM ({BUSY}) ORDER BY fare DESC, pu, do_id LIMIT 1", "string")
add(3, "Among pairs with at least 500 trips, which has the lowest mean total_amount? "
       "Answer as PU-DO.",
    f"SELECT pu || '-' || do_id FROM ({BUSY}) ORDER BY fare ASC, pu, do_id LIMIT 1", "string")
add(3, "Among pairs with at least 500 trips, which has the highest mean trip duration "
       "in minutes? Answer as PU-DO.",
    f"SELECT pu || '-' || do_id FROM ({BUSY}) ORDER BY mins DESC, pu, do_id LIMIT 1", "string")
add(3, "Among pairs with at least 500 trips, which has the highest mean tip as a share "
       "of fare_amount? Answer as PU-DO.",
    f"SELECT pu || '-' || do_id FROM ({BUSY}) ORDER BY tip_rate DESC, pu, do_id LIMIT 1", "string")
add(3, "How many pickup-dropoff pairs in the clean set have at least 500 trips?",
    f"SELECT count(*) FROM ({BUSY})", "number", 0)
add(3, "Which pickup zone name has the most clean-set trips?",
    f"SELECT z.\"Zone\" FROM t JOIN z ON t.\"PULocationID\" = z.\"LocationID\" "
    f"WHERE {CLEAN} GROUP BY 1 ORDER BY count(*) DESC LIMIT 1", "string")
add(3, "Which borough has the most clean-set pickups?",
    f"SELECT z.\"Borough\" FROM t JOIN z ON t.\"PULocationID\" = z.\"LocationID\" "
    f"WHERE {CLEAN} GROUP BY 1 ORDER BY count(*) DESC LIMIT 1", "string")
add(3, "What is the mean total_amount of clean-set trips picked up in Manhattan? "
       "Two decimals.",
    f"SELECT round(avg(total_amount), 2) FROM t JOIN z ON t.\"PULocationID\" = "
    f"z.\"LocationID\" WHERE {CLEAN} AND z.\"Borough\" = 'Manhattan'")
add(3, "In the clean set, which hour of the day (0-23, by pickup time) has the most "
       "trips?",
    f"SELECT EXTRACT(HOUR FROM tpep_pickup_datetime) FROM t WHERE {CLEAN} GROUP BY 1 "
    f"ORDER BY count(*) DESC LIMIT 1", "number", 0)
add(3, "In the clean set, which hour of the day has the highest mean total_amount?",
    f"SELECT EXTRACT(HOUR FROM tpep_pickup_datetime) FROM t WHERE {CLEAN} GROUP BY 1 "
    f"ORDER BY avg(total_amount) DESC LIMIT 1", "number", 0)
add(3, "In the clean set, what is the mean fare per mile (total_amount / trip_distance) "
       "over all trips? Two decimals.",
    f"SELECT round(avg(total_amount / trip_distance), 2) FROM t WHERE {CLEAN}")
add(3, "Among pairs with at least 500 trips, which has the highest mean trip_distance? "
       "Answer as PU-DO.",
    f"SELECT pu || '-' || do_id FROM ({BUSY}) ORDER BY miles DESC, pu, do_id LIMIT 1", "string")
add(3, "How many clean-set trips were paid by credit card (payment_type = 1)?",
    f"SELECT count(*) FROM t WHERE {CLEAN} AND payment_type = 1", "number", 0)
add(3, "What share of clean-set trips were paid by credit card? Give a fraction to four "
       "decimals.",
    f"SELECT round(sum(CASE WHEN payment_type = 1 THEN 1 ELSE 0 END)::DOUBLE / count(*), 4)"
    f" FROM t WHERE {CLEAN}", "number", 0.0001)
add(3, "In the clean set, what is the mean Airport_fee over trips where it is greater "
       "than zero? Two decimals.",
    f"SELECT round(avg(\"Airport_fee\"), 2) FROM t WHERE {CLEAN} AND \"Airport_fee\" > 0")

# ---- phase 4: synthesis. The best route under a stated rule. -----------------------

add(4, "Define route efficiency as mean total_amount divided by mean duration in minutes. "
       "Among pairs with at least 500 clean-set trips, which pair has the highest "
       "efficiency? Answer as PU-DO.",
    f"SELECT pu || '-' || do_id FROM ({BUSY}) ORDER BY fare / mins DESC, pu, do_id LIMIT 1",
    "string")
add(4, "Using that same efficiency measure, what is its value for the winning pair? "
       "Two decimals.",
    f"SELECT round(max(fare / mins), 2) FROM ({BUSY})")
add(4, "Among pairs with at least 500 clean-set trips and a mean trip_distance of at "
       "least 3 miles, which has the highest mean total_amount per mile? Answer as PU-DO.",
    f"SELECT pu || '-' || do_id FROM ({BUSY}) WHERE miles >= 3 "
    f"ORDER BY fare / miles DESC, pu, do_id LIMIT 1", "string")
add(4, "Among pairs with at least 1000 clean-set trips, which has the best combination "
       "of high mean total_amount and low mean duration, scored as mean total_amount "
       "minus mean duration in minutes? Answer as PU-DO.",
    f"SELECT pu || '-' || do_id FROM ({BUSY}) WHERE n >= 1000 "
    f"ORDER BY fare - mins DESC, pu, do_id LIMIT 1", "string")
add(4, "Name the pickup and dropoff zones of that last winning pair, as "
       "'<pickup zone> to <dropoff zone>'.",
    f"SELECT zp.\"Zone\" || ' to ' || zd.\"Zone\" FROM ({BUSY}) b "
    f"JOIN z zp ON b.pu = zp.\"LocationID\" JOIN z zd ON b.do_id = zd.\"LocationID\" "
    f"WHERE b.n >= 1000 ORDER BY b.fare - b.mins DESC, b.pu, b.do_id LIMIT 1", "string")
add(4, "Restricting to clean-set trips picked up between 17:00 and 19:59 inclusive, and "
       "to pairs with at least 200 such trips, which pair has the highest mean "
       "total_amount? Answer as PU-DO.",
    f"SELECT \"PULocationID\" || '-' || \"DOLocationID\" FROM t WHERE {CLEAN} "
    f"AND EXTRACT(HOUR FROM tpep_pickup_datetime) BETWEEN 17 AND 19 "
    f"GROUP BY 1, \"PULocationID\", \"DOLocationID\" HAVING count(*) >= 200 "
    f"ORDER BY avg(total_amount) DESC, \"PULocationID\", \"DOLocationID\" LIMIT 1",
    "string")
add(4, "For that evening-peak winner, what is its mean total_amount? Two decimals.",
    f"SELECT round(avg(total_amount), 2) FROM t WHERE {CLEAN} "
    f"AND EXTRACT(HOUR FROM tpep_pickup_datetime) BETWEEN 17 AND 19 "
    f"GROUP BY \"PULocationID\", \"DOLocationID\" HAVING count(*) >= 200 "
    f"ORDER BY avg(total_amount) DESC, \"PULocationID\", \"DOLocationID\" LIMIT 1")
add(4, "Among pairs with at least 500 clean-set trips, how many have a mean total_amount "
       "above 50 dollars?",
    f"SELECT count(*) FROM ({BUSY}) WHERE fare > 50", "number", 0)
add(4, "Among pairs with at least 500 clean-set trips, what is the mean of their mean "
       "fares per mile? Two decimals.",
    f"SELECT round(avg(fare / miles), 2) FROM ({BUSY})")
add(4, "Taking the ten pairs with the most clean-set trips, which of those ten has the "
       "highest mean total_amount? Answer as PU-DO.",
    f"SELECT pu || '-' || do_id FROM (SELECT * FROM ({BUSY}) ORDER BY n DESC, pu, do_id LIMIT 10)"
    f" ORDER BY fare DESC, pu, do_id LIMIT 1", "string")


def main() -> None:
    con = duckdb.connect()
    con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{TRIPS}')")
    con.execute(f"CREATE VIEW z AS SELECT * FROM read_csv_auto('{ZONES}')")
    for q in Q:
        value = con.execute(q["sql"]).fetchone()[0]
        q["answer"] = float(value) if q["kind"] == "number" else str(value)
    out = HERE / "questions.json"
    out.write_text(json.dumps(Q, indent=1) + "\n")
    print(f"{len(Q)} questions -> {out}")
    for q in Q:
        print(f"  {q['id']} p{q['phase']} {q['answer']}")


if __name__ == "__main__":
    main()
