"""Styled DataFrame renderers for the dashboard."""

import pandas as pd


_STATUS_COLORS = {
    "planned": "background-color: #1a237e; color: #90caf9",
    "dispatched": "background-color: #004d40; color: #80cbc4",
    "completed": "background-color: #1b5e20; color: #a5d6a7",
    "cancelled": "background-color: #b71c1c; color: #ef9a9a",
}


def _color_status(val: str) -> str:
    """Return CSS styling for a status cell."""
    return _STATUS_COLORS.get(str(val).lower(), "")


def styled_requests_table(df: pd.DataFrame) -> pd.io.formats.style.Styler:
    """Transport requests table with color-coded status column."""
    if df.empty:
        return df.style

    display_cols = [
        col for col in [
            "warehouse_id", "time_slot_start", "time_slot_end",
            "total_containers", "truck_capacity", "buffer_pct",
            "trucks_needed", "status", "created_at",
        ]
        if col in df.columns
    ]
    display_df = df[display_cols].copy()

    if "total_containers" in display_df.columns:
        display_df["total_containers"] = display_df["total_containers"].round(2)
    if "buffer_pct" in display_df.columns:
        display_df["buffer_pct"] = (display_df["buffer_pct"] * 100).round(1).astype(str) + "%"

    styler = display_df.style

    if "status" in display_df.columns:
        styler = styler.map(_color_status, subset=["status"])

    if "trucks_needed" in display_df.columns:
        styler = styler.background_gradient(
            subset=["trucks_needed"], cmap="YlOrRd", vmin=0,
        )

    column_labels = {
        "warehouse_id": "Warehouse",
        "time_slot_start": "Slot Start",
        "time_slot_end": "Slot End",
        "total_containers": "Containers",
        "truck_capacity": "Truck Cap.",
        "buffer_pct": "Buffer",
        "trucks_needed": "Trucks",
        "status": "Status",
        "created_at": "Created",
    }
    styler = styler.format(precision=2).relabel_index(
        [column_labels.get(c, c) for c in display_cols], axis="columns",
    )
    return styler


def styled_warehouses_table(df: pd.DataFrame) -> pd.io.formats.style.Styler:
    """Warehouse overview table with gradient on numeric columns."""
    if df.empty:
        return df.style

    display_cols = [
        col for col in [
            "warehouse_id", "route_count", "latest_forecast_at", "upcoming_trucks",
        ]
        if col in df.columns
    ]
    display_df = df[display_cols].copy()

    styler = display_df.style

    gradient_cols = [c for c in ["route_count", "upcoming_trucks"] if c in display_df.columns]
    if gradient_cols:
        styler = styler.background_gradient(subset=gradient_cols, cmap="Blues", vmin=0)

    column_labels = {
        "warehouse_id": "Warehouse ID",
        "route_count": "Routes",
        "latest_forecast_at": "Latest Forecast",
        "upcoming_trucks": "Upcoming Trucks",
    }
    styler = styler.format(precision=0).relabel_index(
        [column_labels.get(c, c) for c in display_cols], axis="columns",
    )
    return styler
