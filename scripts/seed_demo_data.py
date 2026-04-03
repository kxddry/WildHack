"""
Seed demo forecasts and transport requests for dashboard demo.
Run: python scripts/seed_demo_data.py
"""
import os
import json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text

DB_URL = os.getenv("DATABASE_URL", "postgresql://wildhack:wildhack_dev@localhost:5432/wildhack")

def main():
    engine = create_engine(DB_URL)

    # Get warehouses
    with engine.connect() as conn:
        warehouses = pd.read_sql("SELECT warehouse_id, route_count FROM warehouses", conn)

    if warehouses.empty:
        print("No warehouses found. Run seed_status_history.py first!")
        return

    base_time = datetime.now().replace(minute=0, second=0, microsecond=0)

    # Generate demo forecasts for top 5 warehouses
    top_warehouses = warehouses.nlargest(5, 'route_count')

    forecast_rows = []
    for _, wh in top_warehouses.iterrows():
        wh_id = int(wh['warehouse_id'])
        for route_offset in range(min(3, int(wh['route_count']))):
            route_id = wh_id * 100 + route_offset  # synthetic route_id
            anchor = base_time - timedelta(hours=1)

            forecasts_json = []
            for step in range(1, 11):
                ts = anchor + timedelta(minutes=30 * step)
                value = max(0, np.random.normal(15, 5))
                forecasts_json.append({
                    "step": step,
                    "ts": ts.isoformat(),
                    "value": round(value, 2)
                })

            forecast_rows.append({
                'route_id': route_id,
                'warehouse_id': wh_id,
                'anchor_ts': anchor,
                'forecasts': json.dumps(forecasts_json),
                'model_version': 'v1'
            })

    # Insert forecasts
    df_forecasts = pd.DataFrame(forecast_rows)
    df_forecasts.to_sql('forecasts', engine, if_exists='append', index=False, method='multi')
    print(f"Inserted {len(df_forecasts)} demo forecasts")

    # Generate demo transport requests
    request_rows = []
    for _, wh in top_warehouses.iterrows():
        wh_id = int(wh['warehouse_id'])
        for slot in range(5):  # 5 time slots
            slot_start = base_time + timedelta(hours=slot * 2)
            slot_end = slot_start + timedelta(hours=2)
            total = max(0, np.random.normal(100, 30))
            trucks = max(1, int(np.ceil(total * 1.1 / 33)))

            request_rows.append({
                'warehouse_id': wh_id,
                'time_slot_start': slot_start,
                'time_slot_end': slot_end,
                'total_containers': round(total, 1),
                'truck_capacity': 33,
                'buffer_pct': 0.10,
                'trucks_needed': trucks,
                'status': 'planned'
            })

    df_requests = pd.DataFrame(request_rows)
    df_requests.to_sql('transport_requests', engine, if_exists='append', index=False, method='multi')
    print(f"Inserted {len(df_requests)} demo transport requests")

    print("Done! Dashboard should now show data.")

if __name__ == "__main__":
    main()
