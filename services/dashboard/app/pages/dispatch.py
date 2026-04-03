"""Dispatch View -- transport requests and dispatch triggering."""

import asyncio
from datetime import datetime, timedelta

import streamlit as st

from app.components.charts import dispatch_timeline
from app.components.tables import styled_requests_table
from app.data.api_client import dispatch as api_dispatch
from app.data.db_client import get_transport_requests, get_warehouses


def render() -> None:
    """Render the Dispatch page."""
    st.header("Dispatch Manager")

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
        key="dispatch_warehouse",
    )

    st.sidebar.divider()
    st.sidebar.subheader("Trigger Dispatch")

    hours_ahead = st.sidebar.slider(
        "Forecast window (hours ahead)",
        min_value=1,
        max_value=24,
        value=6,
        key="dispatch_hours",
    )

    dispatch_button = st.sidebar.button(
        "Run Dispatch",
        type="primary",
        disabled=not warehouse_options,
        use_container_width=True,
    )

    # --- Handle dispatch trigger ---
    if dispatch_button and warehouse_options:
        now = datetime.utcnow()
        time_start = now.isoformat()
        time_end = (now + timedelta(hours=hours_ahead)).isoformat()

        with st.spinner("Running dispatch calculation..."):
            result = asyncio.run(
                api_dispatch(
                    warehouse_id=selected_warehouse,
                    time_range=(time_start, time_end),
                )
            )

        if result is not None:
            n_requests = len(result.get("dispatch_requests", []))
            st.success(f"Dispatch complete: {n_requests} transport request(s) created.")
            st.rerun()
        else:
            st.warning(
                "Dispatch returned no results. "
                "Ensure forecasts exist for this warehouse and time range."
            )

    # --- Main content ---
    if not warehouse_options:
        st.info("No warehouses found. Run predictions first to populate data.")
        return

    requests_df = get_transport_requests(warehouse_id=selected_warehouse)

    if requests_df.empty:
        st.info(
            f"No transport requests for Warehouse {selected_warehouse}. "
            "Use the sidebar to trigger a dispatch."
        )
        return

    # --- KPI summary ---
    total_trucks = int(requests_df["trucks_needed"].sum())
    planned = len(requests_df[requests_df["status"] == "planned"])
    dispatched = len(requests_df[requests_df["status"] == "dispatched"])
    total_containers = requests_df["total_containers"].sum()

    cols = st.columns(4)
    cols[0].metric("Total Trucks", total_trucks)
    cols[1].metric("Planned", planned)
    cols[2].metric("Dispatched", dispatched)
    cols[3].metric("Total Containers", f"{total_containers:.0f}")

    st.divider()

    # --- Dispatch timeline chart ---
    fig = dispatch_timeline(requests_df)
    st.plotly_chart(fig, use_container_width=True)

    # --- Requests table ---
    st.subheader("Transport Requests")
    styler = styled_requests_table(requests_df)
    st.dataframe(styler, use_container_width=True, hide_index=True)
