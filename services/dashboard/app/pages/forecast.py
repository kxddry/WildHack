"""Forecast View -- time series of predictions per warehouse."""

import json

import pandas as pd
import streamlit as st

from app.components.charts import forecast_timeseries
from app.components.metrics import display_kpi_row
from app.data.db_client import get_forecast_history, get_warehouses


def render() -> None:
    """Render the Forecasts page."""
    st.header("Forecast Explorer")

    # --- Sidebar controls ---
    warehouses_df = get_warehouses()

    if warehouses_df.empty:
        warehouse_options = []
    else:
        warehouse_options = sorted(warehouses_df["warehouse_id"].tolist())

    selected_warehouse = st.sidebar.selectbox(
        "Warehouse",
        options=warehouse_options if warehouse_options else [0],
        format_func=lambda x: f"Warehouse {x}" if warehouse_options else "No warehouses",
        disabled=not warehouse_options,
    )

    date_range = st.sidebar.date_input(
        "Date range",
        value=[],
        help="Filter forecasts by creation date (optional)",
    )

    # --- Fetch data ---
    if not warehouse_options:
        st.info("No warehouses found in the database. Run predictions first to populate data.")
        return

    forecast_df = get_forecast_history(warehouse_id=selected_warehouse, limit=500)

    if forecast_df.empty:
        st.info(
            f"No forecasts yet for Warehouse {selected_warehouse}. "
            "Trigger predictions via the API to see data here."
        )
        return

    # Apply date filter if provided
    if len(date_range) == 2:
        start_date, end_date = date_range
        forecast_df["created_at"] = pd.to_datetime(forecast_df["created_at"])
        forecast_df = forecast_df[
            (forecast_df["created_at"].dt.date >= start_date)
            & (forecast_df["created_at"].dt.date <= end_date)
        ]
        if forecast_df.empty:
            st.warning("No forecasts in the selected date range.")
            return

    # --- KPI cards ---
    total_predictions = len(forecast_df)

    all_predicted_values = []
    for _, row in forecast_df.iterrows():
        forecasts_raw = row["forecasts"]
        if isinstance(forecasts_raw, str):
            forecasts_raw = json.loads(forecasts_raw)
        for step in forecasts_raw:
            all_predicted_values.append(step.get("predicted_value", 0))

    avg_predicted = sum(all_predicted_values) / len(all_predicted_values) if all_predicted_values else 0
    total_containers = sum(all_predicted_values)
    unique_routes = forecast_df["route_id"].nunique()
    model_version = forecast_df["model_version"].iloc[0] if "model_version" in forecast_df.columns else "N/A"

    display_kpi_row({
        "Total Forecasts": total_predictions,
        "Unique Routes": unique_routes,
        "Avg Predicted Containers": f"{avg_predicted:.2f}",
        "Total Containers": f"{total_containers:.0f}",
        "Model Version": model_version,
    })

    st.divider()

    # --- Chart ---
    fig = forecast_timeseries(forecast_df, selected_warehouse)
    st.plotly_chart(fig, use_container_width=True)

    # --- Table ---
    st.subheader("Forecast Records")

    table_df = forecast_df[["route_id", "anchor_ts", "model_version", "created_at"]].copy()
    table_df.columns = ["Route ID", "Anchor Time", "Model", "Created At"]
    st.dataframe(table_df, use_container_width=True, hide_index=True)
