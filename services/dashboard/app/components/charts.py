"""Plotly chart builders for the dashboard."""

import json

import pandas as pd
import plotly.graph_objects as go

# Consistent color palette for professional look
COLORS = [
    "#636EFA", "#EF553B", "#00CC96", "#AB63FA", "#FFA15A",
    "#19D3F3", "#FF6692", "#B6E880", "#FF97FF", "#FECB52",
]

_LAYOUT_DEFAULTS = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Inter, sans-serif", size=13),
    margin=dict(l=40, r=20, t=50, b=40),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)


def forecast_timeseries(df: pd.DataFrame, warehouse_id: int) -> go.Figure:
    """Line chart of predicted target_2h over time, grouped by route.

    Expects DataFrame with columns: route_id, anchor_ts, forecasts (JSONB string or list).
    """
    fig = go.Figure()

    if df.empty:
        fig.update_layout(
            **_LAYOUT_DEFAULTS,
            title=f"Warehouse {warehouse_id} -- Forecasts",
            annotations=[dict(
                text="No forecast data available",
                xref="paper", yref="paper", x=0.5, y=0.5,
                showarrow=False, font=dict(size=18, color="#888"),
            )],
        )
        return fig

    for i, route_id in enumerate(df["route_id"].unique()):
        route_df = df[df["route_id"] == route_id].copy()
        timestamps = []
        values = []

        for _, row in route_df.iterrows():
            forecasts_raw = row["forecasts"]
            if isinstance(forecasts_raw, str):
                forecasts_raw = json.loads(forecasts_raw)
            for step in forecasts_raw:
                timestamps.append(step["timestamp"])
                values.append(step["predicted_value"])

        fig.add_trace(go.Scatter(
            x=timestamps,
            y=values,
            mode="lines+markers",
            name=f"Route {route_id}",
            line=dict(color=COLORS[i % len(COLORS)], width=2),
            marker=dict(size=4),
        ))

    fig.update_layout(
        **_LAYOUT_DEFAULTS,
        title=f"Warehouse {warehouse_id} -- Forecast Timeline",
        xaxis_title="Time",
        yaxis_title="Predicted Containers (target_2h)",
        hovermode="x unified",
    )
    return fig


def dispatch_timeline(requests: pd.DataFrame) -> go.Figure:
    """Horizontal bar chart showing trucks_needed per time slot."""
    fig = go.Figure()

    if requests.empty:
        fig.update_layout(
            **_LAYOUT_DEFAULTS,
            title="Dispatch Timeline",
            annotations=[dict(
                text="No dispatch requests yet",
                xref="paper", yref="paper", x=0.5, y=0.5,
                showarrow=False, font=dict(size=18, color="#888"),
            )],
        )
        return fig

    sorted_df = requests.sort_values("time_slot_start")

    labels = [
        f"{row['time_slot_start']}"
        for _, row in sorted_df.iterrows()
    ]

    status_colors = {
        "planned": "#636EFA",
        "dispatched": "#00CC96",
        "completed": "#B6E880",
        "cancelled": "#EF553B",
    }

    for status_val in sorted_df["status"].unique():
        mask = sorted_df["status"] == status_val
        fig.add_trace(go.Bar(
            y=[labels[i] for i, m in enumerate(mask) if m],
            x=sorted_df.loc[mask, "trucks_needed"],
            orientation="h",
            name=status_val.capitalize(),
            marker_color=status_colors.get(status_val, "#FFA15A"),
            text=sorted_df.loc[mask, "trucks_needed"],
            textposition="auto",
        ))

    fig.update_layout(
        **_LAYOUT_DEFAULTS,
        title="Dispatch Timeline -- Trucks per Time Slot",
        xaxis_title="Trucks Needed",
        yaxis_title="Time Slot",
        barmode="stack",
        height=max(300, len(labels) * 35),
    )
    return fig


def warehouse_load_chart(warehouses: pd.DataFrame) -> go.Figure:
    """Bar chart of total upcoming trucks per warehouse."""
    fig = go.Figure()

    if warehouses.empty:
        fig.update_layout(
            **_LAYOUT_DEFAULTS,
            title="Warehouse Load",
            annotations=[dict(
                text="No warehouse data available",
                xref="paper", yref="paper", x=0.5, y=0.5,
                showarrow=False, font=dict(size=18, color="#888"),
            )],
        )
        return fig

    sorted_df = warehouses.sort_values("upcoming_trucks", ascending=False)

    fig.add_trace(go.Bar(
        x=sorted_df["warehouse_id"].astype(str),
        y=sorted_df["upcoming_trucks"],
        marker=dict(
            color=sorted_df["upcoming_trucks"],
            colorscale="Viridis",
            showscale=True,
            colorbar=dict(title="Trucks"),
        ),
        text=sorted_df["upcoming_trucks"],
        textposition="auto",
    ))

    fig.update_layout(
        **_LAYOUT_DEFAULTS,
        title="Upcoming Trucks by Warehouse",
        xaxis_title="Warehouse ID",
        yaxis_title="Trucks Needed",
    )
    return fig


def status_history_chart(history: pd.DataFrame) -> go.Figure:
    """Multi-line chart of status_1..8 over time for a route."""
    fig = go.Figure()

    if history.empty:
        fig.update_layout(
            **_LAYOUT_DEFAULTS,
            title="Status History",
            annotations=[dict(
                text="No status history available",
                xref="paper", yref="paper", x=0.5, y=0.5,
                showarrow=False, font=dict(size=18, color="#888"),
            )],
        )
        return fig

    status_cols = [f"status_{i}" for i in range(1, 9)]

    for i, col in enumerate(status_cols):
        if col in history.columns:
            fig.add_trace(go.Scatter(
                x=history["timestamp"],
                y=history[col],
                mode="lines",
                name=col.replace("_", " ").title(),
                line=dict(color=COLORS[i % len(COLORS)], width=2),
            ))

    if "target_2h" in history.columns and history["target_2h"].notna().any():
        fig.add_trace(go.Scatter(
            x=history["timestamp"],
            y=history["target_2h"],
            mode="lines+markers",
            name="Target 2h (Actual)",
            line=dict(color="#FECB52", width=3, dash="dash"),
            marker=dict(size=5),
        ))

    fig.update_layout(
        **_LAYOUT_DEFAULTS,
        title="Route Status History",
        xaxis_title="Time",
        yaxis_title="Value",
        hovermode="x unified",
    )
    return fig
