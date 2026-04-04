"""
Seed route_status_history table from training data.
Run: python scripts/seed_status_history.py

Loads the last N days of status observations per route from train_team_track.parquet
into PostgreSQL route_status_history table. Also populates the warehouses table.
"""
import os
import pandas as pd
from sqlalchemy import create_engine, text

DB_URL = os.getenv("DATABASE_URL", "postgresql://wildhack:wildhack_dev@localhost:5432/wildhack")
DATA_PATH = os.getenv("DATA_PATH", "Data/raw/train_team_track.parquet")
HISTORY_DAYS = 7  # Keep last 7 days of history (enough for 288 observations = 6 days)

def main():
    print(f"Loading data from {DATA_PATH}...")
    df = pd.read_parquet(DATA_PATH)

    # Keep only the last HISTORY_DAYS of data
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    max_ts = df['timestamp'].max()
    cutoff = max_ts - pd.Timedelta(days=HISTORY_DAYS)
    df_recent = df[df['timestamp'] >= cutoff].copy()

    print(f"Total rows: {len(df)}, Recent rows ({HISTORY_DAYS}d): {len(df_recent)}")

    engine = create_engine(DB_URL)

    # Seed route_status_history
    status_cols = ['route_id', 'office_from_id', 'timestamp',
                   'status_1', 'status_2', 'status_3', 'status_4',
                   'status_5', 'status_6', 'status_7', 'status_8', 'target_2h']

    # Rename office_from_id to warehouse_id for the table
    history_df = df_recent[status_cols].copy()
    history_df = history_df.rename(columns={'office_from_id': 'warehouse_id'})

    # Seed warehouses first (routes FK depends on it)
    warehouse_stats = df.groupby('office_from_id').agg(
        route_count=('route_id', 'nunique'),
        first_seen=('timestamp', 'min'),
        last_seen=('timestamp', 'max')
    ).reset_index()
    warehouse_stats = warehouse_stats.rename(columns={'office_from_id': 'warehouse_id'})

    routes_df = df[['route_id', 'office_from_id']].drop_duplicates('route_id')
    routes_df = routes_df.rename(columns={'office_from_id': 'warehouse_id'})

    with engine.begin() as conn:
        print(f"Inserting {len(warehouse_stats)} warehouses...")
        for _, row in warehouse_stats.iterrows():
            conn.execute(text(
                "INSERT INTO warehouses (warehouse_id, route_count, first_seen, last_seen) "
                "VALUES (:wid, :rc, :fs, :ls) ON CONFLICT DO NOTHING"
            ), {"wid": int(row.warehouse_id), "rc": int(row.route_count),
                "fs": row.first_seen, "ls": row.last_seen})

        print(f"Inserting {len(routes_df)} routes...")
        for _, row in routes_df.iterrows():
            conn.execute(text(
                "INSERT INTO routes (route_id, warehouse_id) "
                "VALUES (:rid, :wid) ON CONFLICT DO NOTHING"
            ), {"rid": int(row.route_id), "wid": int(row.warehouse_id)})

        insert_sql = text(
            "INSERT INTO route_status_history "
            "(route_id, warehouse_id, timestamp, "
            "status_1, status_2, status_3, status_4, "
            "status_5, status_6, status_7, status_8, target_2h) "
            "VALUES (:route_id, :warehouse_id, :timestamp, "
            ":status_1, :status_2, :status_3, :status_4, "
            ":status_5, :status_6, :status_7, :status_8, :target_2h) "
            "ON CONFLICT (route_id, timestamp) DO NOTHING"
        )
        print(f"Inserting {len(history_df)} rows into route_status_history...")
        for start in range(0, len(history_df), 5000):
            chunk = history_df.iloc[start:start + 5000]
            conn.execute(insert_sql, chunk.to_dict('records'))
            print(f"  ... {min(start + 5000, len(history_df))}/{len(history_df)}")

    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM route_status_history")).scalar()
        wh_count = conn.execute(text("SELECT COUNT(*) FROM warehouses")).scalar()
        rt_count = conn.execute(text("SELECT COUNT(*) FROM routes")).scalar()
        print(f"route_status_history: {count} rows")
        print(f"warehouses: {wh_count} warehouses")
        print(f"routes: {rt_count} routes")

    print("Done!")

if __name__ == "__main__":
    main()
