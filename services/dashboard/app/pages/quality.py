"""Quality Metrics -- WAPE, RBias, model info, accuracy analysis."""

import asyncio
import json

import numpy as np
import streamlit as st

from app.components.charts import status_history_chart
from app.components.metrics import calculate_wape_rbias, display_kpi_row
from app.data.api_client import get_model_info
from app.data.db_client import get_forecast_history, get_route_status_history, get_warehouses


def render() -> None:
    """Render the Quality Metrics page."""
    st.header("Model Quality")

    # --- Model info section ---
    st.subheader("Model Information")

    model_info = asyncio.run(get_model_info())

    if model_info is not None:
        display_kpi_row({
            "Model Version": model_info.get("model_version", "N/A"),
            "Model Type": model_info.get("model_type", "N/A"),
            "Objective": model_info.get("objective", "N/A"),
            "CV Score": (
                f"{model_info['cv_score']:.4f}"
                if model_info.get("cv_score") is not None
                else "N/A"
            ),
            "Feature Count": model_info.get("feature_count", "N/A"),
        })

        col1, col2 = st.columns(2)
        col1.info(
            f"Forecast horizon: **{model_info.get('forecast_horizon', 'N/A')} steps** "
            f"x **{model_info.get('step_interval_minutes', 'N/A')} min**"
        )
        training_date = model_info.get("training_date", "N/A")
        col2.info(f"Training date: **{training_date}**")
    else:
        st.warning(
            "Could not connect to prediction service. "
            "Make sure it is running to see model info."
        )

    st.divider()

    # --- Prediction accuracy section ---
    st.subheader("Prediction Accuracy")

    warehouses_df = get_warehouses()
    if warehouses_df.empty:
        st.info("No warehouses available for accuracy analysis.")
        return

    warehouse_options = sorted(warehouses_df["warehouse_id"].tolist())

    col_wh, col_route = st.columns(2)
    selected_warehouse = col_wh.selectbox(
        "Warehouse",
        options=warehouse_options,
        format_func=lambda x: f"Warehouse {x}",
        key="quality_warehouse",
    )

    # Find routes with status history for this warehouse
    forecast_df = get_forecast_history(warehouse_id=selected_warehouse, limit=200)

    if forecast_df.empty:
        st.info("No forecasts available for this warehouse.")
        return

    route_ids = sorted(forecast_df["route_id"].unique().tolist())
    selected_route = col_route.selectbox(
        "Route",
        options=route_ids,
        format_func=lambda x: f"Route {x}",
        key="quality_route",
    )

    # --- Status history chart ---
    history_df = get_route_status_history(selected_route, limit=288)

    if not history_df.empty:
        fig = status_history_chart(history_df)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info(f"No status history for Route {selected_route}.")

    # --- WAPE + RBias calculation ---
    st.subheader("WAPE + |Relative Bias|")

    if history_df.empty or "target_2h" not in history_df.columns:
        st.info("Need actual target values (target_2h) to compute accuracy metrics.")
        return

    actuals = history_df["target_2h"].dropna()
    if actuals.empty:
        st.info("No actual target_2h values recorded yet for this route.")
        return

    # Match forecasts with actuals
    route_forecasts = forecast_df[forecast_df["route_id"] == selected_route]
    if route_forecasts.empty:
        st.info("No forecast predictions to compare against actuals.")
        return

    predicted_values = []
    for _, row in route_forecasts.iterrows():
        forecasts_raw = row["forecasts"]
        if isinstance(forecasts_raw, str):
            forecasts_raw = json.loads(forecasts_raw)
        for step in forecasts_raw:
            predicted_values.append(step.get("predicted_value", 0))

    # Use available data for metric calculation (truncate to matching lengths)
    y_true = np.array(actuals.values[: len(predicted_values)])
    y_pred = np.array(predicted_values[: len(y_true)])

    if len(y_true) == 0:
        st.info("Not enough matching data points for metric computation.")
        return

    result = calculate_wape_rbias(y_true, y_pred)

    if result["combined"] is not None:
        display_kpi_row({
            "WAPE": f"{result['wape']:.4f}",
            "| Relative Bias |": f"{result['rbias']:.4f}",
            "Combined (WAPE + RBias)": f"{result['combined']:.4f}",
            "Data Points": len(y_true),
        })

        st.caption(
            "**WAPE** = SUM(|pred - actual|) / SUM(actual)  |  "
            "**RBias** = |SUM(pred) / SUM(actual) - 1|  |  "
            "**Lower is better.**"
        )
    else:
        st.warning("Cannot compute metrics (sum of actuals is zero).")
