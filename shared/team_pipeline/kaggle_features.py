"""Kaggle-style features and BUILD_KWARGS constant.

Extracted from experiments/precompute_features.py. The name "kaggle" is
historical — these features only require (route_id, timestamp, target_2h).
"""

import numpy as np
import pandas as pd

BUILD_KWARGS = dict(
    train_days=7, use_static_aggs=True, use_total_status_features=True,
    use_total_inventory_aggs=True, use_target_mean_hist=True, use_target_std_hist=True,
    use_target_zero_rate_hist=True, use_target_count_hist=True, use_default_ts_features=True,
    static_agg_features=[f'status_{i}' for i in range(1, 9)],
    total_inventory_agg_features=[
        'total_inventory', 'early_inventory', 'mid_inventory', 'late_inventory',
        'early_share', 'mid_share', 'late_share', 'status_entropy'
    ],
    static_group_keys_list=[
        ['route_id'], ['office_from_id'], ['route_id', 'dow'], ['route_id', 'pod'],
    ],
    total_inventory_group_keys_list=[
        ['route_id'], ['office_from_id'],
        ['route_id', 'dow'], ['route_id', 'pod'], ['route_id', 'slot'],
    ],
    target_hist_group_keys_list=[
        ['route_id'], ['route_id', 'pod'], ['route_id', 'dow'],
    ],
    statistics=('mean', 'std'),
)


def add_kaggle_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add same-slot lags, momentum, intermittency, and smoothed target encoding."""
    target = 'target_2h'
    df = df.sort_values(['route_id', 'timestamp']).reset_index(drop=True)
    rg = df.groupby('route_id', sort=False)
    SPD = 48

    for db in [1, 2, 3, 7]:
        df[f'target_same_slot_lag_{db}d'] = rg[target].shift(db * SPD).astype(np.float32)
    for wd in [3, 7]:
        lags = [rg[target].shift(d * SPD) for d in range(1, wd + 1)]
        df[f'target_same_slot_mean_{wd}d'] = pd.concat(lags, axis=1).mean(axis=1).astype(np.float32)
    lags7 = [rg[target].shift(d * SPD) for d in range(1, 8)]
    df['target_same_slot_std_7d'] = pd.concat(lags7, axis=1).std(axis=1).astype(np.float32)

    sh = rg[target].shift(1)
    rc = {}
    for w in [6, 12, 24, 48, 96]:
        rc[w] = sh.groupby(df['route_id'], sort=False).transform(
            lambda s: s.rolling(w, min_periods=1).mean()).astype(np.float32)
    for sw, lw in [(6, 48), (12, 96), (24, 96), (48, 96)]:
        df[f'target_momentum_{sw}_{lw}'] = (rc[sw] / (rc[lw] + 1e-8)).astype(np.float32)
    lag1 = rg[target].shift(1)
    for w in [48, 96]:
        df[f'target_deviation_{w}'] = (lag1 / (rc[w] + 1e-8)).astype(np.float32)

    snz = rg[target].shift(1).gt(0).astype(np.float32)
    for w in [24, 48]:
        df[f'target_nonzero_rate_{w}'] = snz.groupby(df['route_id'], sort=False).transform(
            lambda s: s.rolling(w, min_periods=1).mean()).astype(np.float32)
        rs = sh.groupby(df['route_id'], sort=False).transform(
            lambda s: s.rolling(w, min_periods=2).std()).astype(np.float32)
        df[f'target_cv_{w}'] = (rs / (rc.get(w, rc[48]) + 1e-8)).astype(np.float32)

    if 'slot' not in df.columns:
        df['slot'] = df['timestamp'].dt.hour * 2 + df['timestamp'].dt.minute // 30
    if 'dow' not in df.columns:
        df['dow'] = df['timestamp'].dt.day_name()
    if 'pod' not in df.columns:
        h = df['timestamp'].dt.hour
        df['pod'] = pd.cut(h, bins=[-1, 5, 11, 17, 24], labels=['night', 'morning', 'day', 'evening'])
    alpha = 20
    gm = df[target].mean()
    for gk in [['route_id', 'slot'], ['office_from_id', 'slot']]:
        fn = '_'.join(gk) + '_target_smoothed'
        cs = df.groupby(gk)[target].cumsum() - df[target]
        cc = df.groupby(gk).cumcount()
        gme = cs / cc
        df[fn] = ((cc * gme + alpha * gm) / (cc + alpha)).fillna(gm).astype(np.float32)

    return df


def get_extra_names(df: pd.DataFrame) -> list[str]:
    """Return column names added by add_kaggle_features."""
    kw = ['same_slot', 'momentum', 'deviation', 'nonzero', '_cv_', 'smoothed']
    return [c for c in df.columns if any(k in c for k in kw)]
