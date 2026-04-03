"""Warehouse Overview -- summary of all warehouses, routes, and trucks."""

import streamlit as st

from app.components.charts import warehouse_load_chart
from app.components.metrics import display_kpi_row
from app.components.tables import styled_warehouses_table
from app.data.db_client import get_warehouses


def render() -> None:
    """Render the Overview page."""
    st.header("Warehouse Overview")

    warehouses_df = get_warehouses()

    if warehouses_df.empty:
        st.info(
            "No warehouses found in the database. "
            "The system will populate this view once predictions and dispatches are running."
        )
        return

    # --- KPI cards ---
    total_warehouses = len(warehouses_df)
    total_routes = int(warehouses_df["route_count"].sum())
    total_upcoming_trucks = int(warehouses_df["upcoming_trucks"].sum())

    has_forecast = warehouses_df["latest_forecast_at"].notna().sum()

    display_kpi_row({
        "Total Warehouses": total_warehouses,
        "Total Routes": total_routes,
        "Upcoming Trucks": total_upcoming_trucks,
        "Warehouses with Forecasts": f"{has_forecast}/{total_warehouses}",
    })

    st.divider()

    # --- Load chart ---
    fig = warehouse_load_chart(warehouses_df)
    st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # --- Warehouses table ---
    st.subheader("All Warehouses")
    styler = styled_warehouses_table(warehouses_df)
    st.dataframe(styler, use_container_width=True, hide_index=True)
