"""KPI card helpers and metric calculations."""

from typing import Any

import numpy as np
import streamlit as st


def display_kpi_row(metrics: dict[str, Any]) -> None:
    """Render a row of st.metric() cards from a dict of {label: value} or {label: (value, delta)}.

    Each entry can be:
      - label: value           -> metric with no delta
      - label: (value, delta)  -> metric with delta indicator
    """
    cols = st.columns(len(metrics))
    for col, (label, data) in zip(cols, metrics.items()):
        if isinstance(data, tuple) and len(data) == 2:
            value, delta = data
            col.metric(label=label, value=value, delta=delta)
        else:
            col.metric(label=label, value=data)


def calculate_wape_rbias(
    y_true: np.ndarray,
    y_pred: np.ndarray,
) -> dict[str, float | None]:
    """Compute WAPE + |Relative Bias| metric.

    Mirrors experiments/core/metric.py WapePlusRbias logic.
    Returns dict with 'wape', 'rbias', and 'combined' keys.
    Returns None values when computation is not possible.
    """
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)

    total_true = y_true.sum()

    if total_true == 0 or len(y_true) == 0:
        return {"wape": None, "rbias": None, "combined": None}

    wape = float(np.abs(y_pred - y_true).sum() / total_true)
    rbias = float(np.abs(y_pred.sum() / total_true - 1))
    combined = wape + rbias

    return {"wape": wape, "rbias": rbias, "combined": combined}
