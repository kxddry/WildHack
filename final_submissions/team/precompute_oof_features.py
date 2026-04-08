# -*- coding: utf-8 -*-
"""Precompute full DatasetBuilder features and save to disk.

This solves the OOM problem: build once, save as numpy, reuse everywhere.
Uses the same config as exp_015 (best LB=0.2589).
"""

import gc, sys, time
import numpy as np
import pandas as pd

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, '.')
from core.data import DatasetBuilder, OOTValidator

TRAIN_PATH = '../Data/raw/train_team_track.parquet'
OUT_DIR = 'precomputed'

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


def add_kaggle_features(df):
    """Same Kaggle features as exp_015."""
    target = 'target_2h'
    df = df.sort_values(['route_id', 'timestamp']).reset_index(drop=True)
    rg = df.groupby('route_id', sort=False)
    SPD = 48

    print("  Same-slot lags...", flush=True)
    for db in [1, 2, 3, 7]:
        df[f'target_same_slot_lag_{db}d'] = rg[target].shift(db * SPD).astype(np.float32)
    for wd in [3, 7]:
        lags = [rg[target].shift(d * SPD) for d in range(1, wd + 1)]
        df[f'target_same_slot_mean_{wd}d'] = pd.concat(lags, axis=1).mean(axis=1).astype(np.float32)
    lags7 = [rg[target].shift(d * SPD) for d in range(1, 8)]
    df['target_same_slot_std_7d'] = pd.concat(lags7, axis=1).std(axis=1).astype(np.float32)

    print("  Momentum...", flush=True)
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

    print("  Intermittency...", flush=True)
    snz = rg[target].shift(1).gt(0).astype(np.float32)
    for w in [24, 48]:
        df[f'target_nonzero_rate_{w}'] = snz.groupby(df['route_id'], sort=False).transform(
            lambda s: s.rolling(w, min_periods=1).mean()).astype(np.float32)
        rs = sh.groupby(df['route_id'], sort=False).transform(
            lambda s: s.rolling(w, min_periods=2).std()).astype(np.float32)
        df[f'target_cv_{w}'] = (rs / (rc.get(w, rc[48]) + 1e-8)).astype(np.float32)

    print("  Smoothed encoding...", flush=True)
    if 'slot' not in df.columns:
        df['slot'] = df['timestamp'].dt.hour * 2 + df['timestamp'].dt.minute // 30
    if 'dow' not in df.columns:
        df['dow'] = df['timestamp'].dt.day_name()
    if 'pod' not in df.columns:
        h = df['timestamp'].dt.hour
        df['pod'] = pd.cut(h, bins=[-1, 5, 11, 17, 24], labels=['night', 'morning', 'day', 'evening'])
    alpha = 20; gm = df[target].mean()
    for gk in [['route_id', 'slot'], ['office_from_id', 'slot']]:
        fn = '_'.join(gk) + '_target_smoothed'
        cs = df.groupby(gk)[target].cumsum() - df[target]
        cc = df.groupby(gk).cumcount()
        gme = cs / cc
        df[fn] = ((cc * gme + alpha * gm) / (cc + alpha)).fillna(gm).astype(np.float32)

    return df


def get_extra_names(df):
    kw = ['same_slot', 'momentum', 'deviation', 'nonzero', '_cv_', 'smoothed']
    return [c for c in df.columns if any(k in c for k in kw)]


def main():
    import os
    os.makedirs(OUT_DIR, exist_ok=True)

    print("=" * 60, flush=True)
    print("Precomputing full features...", flush=True)

    # Load
    print("\n[1] Loading raw data...", flush=True)
    train_df = pd.read_parquet(TRAIN_PATH)
    for c in train_df.select_dtypes('int64').columns:
        train_df[c] = train_df[c].astype(np.int32)
    for c in train_df.select_dtypes('float64').columns:
        train_df[c] = train_df[c].astype(np.float32)
    print(f"  Raw: {train_df.shape}", flush=True)

    # Add Kaggle features to raw data
    print("\n[2] Adding Kaggle features...", flush=True)
    train_df = add_kaggle_features(train_df)
    extra_feats = get_extra_names(train_df)
    print(f"  Extra features: {len(extra_feats)}", flush=True)

    # Build 3 OOT folds
    print("\n[3] Building OOT folds with DatasetBuilder...", flush=True)
    fold_gap_days = 2

    for fold_i in range(3):
        print(f"\n  --- Fold {fold_i} ---", flush=True)
        t0 = time.time()

        # Shift validation window
        offset_days = fold_i * fold_gap_days
        # Trim data to simulate different val windows
        max_ts = train_df['timestamp'].max()
        if offset_days > 0:
            fold_df = train_df[train_df['timestamp'] <= max_ts - pd.Timedelta(days=offset_days)].copy()
        else:
            fold_df = train_df.copy()

        bk = {**BUILD_KWARGS, 'extra_numeric_features': extra_feats}
        validator = OOTValidator(df=fold_df, builder_cls=DatasetBuilder, config='team')
        X_train, y_train, X_val, y_val = validator.make_oot_split(val_points=10, **bk)

        cat_cols = validator.builder.cat_features.copy()
        feat_cols = list(X_train.columns)

        # Convert cats to float32 for NN (target encoding)
        X_tr_nn = X_train.copy()
        X_va_nn = X_val.copy()
        gm = y_train.mean()
        for col in cat_cols:
            if col in X_tr_nn.columns:
                stats = pd.DataFrame({'cat': X_tr_nn[col], 'y': y_train})
                agg = stats.groupby('cat')['y'].agg(['mean', 'count'])
                agg['sm'] = (agg['count'] * agg['mean'] + 20 * gm) / (agg['count'] + 20)
                m = agg['sm'].to_dict()
                X_tr_nn[col] = X_tr_nn[col].map(m).fillna(gm).astype(np.float32)
                X_va_nn[col] = X_va_nn[col].map(m).fillna(gm).astype(np.float32)

        # Force float32
        for c in X_train.columns:
            if X_train[c].dtype == np.float64:
                X_train[c] = X_train[c].astype(np.float32)
                X_val[c] = X_val[c].astype(np.float32)
            if X_tr_nn[c].dtype == np.float64:
                X_tr_nn[c] = X_tr_nn[c].astype(np.float32)
                X_va_nn[c] = X_va_nn[c].astype(np.float32)

        # Save for LGB (with categoricals as-is)
        # LGB needs category dtype, save as parquet
        X_train_lgb = X_train.copy()
        X_val_lgb = X_val.copy()
        for c in cat_cols:
            if c in X_train_lgb.columns:
                X_train_lgb[c] = X_train_lgb[c].astype('category')
                X_val_lgb[c] = X_val_lgb[c].astype('category')

        X_train_lgb.to_parquet(f'{OUT_DIR}/X_train_lgb_f{fold_i}.parquet')
        X_val_lgb.to_parquet(f'{OUT_DIR}/X_val_lgb_f{fold_i}.parquet')

        # Save for NN (target-encoded, all float32)
        np.savez_compressed(f'{OUT_DIR}/nn_f{fold_i}.npz',
                            X_train=X_tr_nn.fillna(0).values.astype(np.float32),
                            X_val=X_va_nn.fillna(0).values.astype(np.float32),
                            y_train=y_train.values.astype(np.float32),
                            y_val=y_val.values.astype(np.float32))

        # Save metadata
        import json
        meta = {
            'fold': fold_i,
            'feat_cols': feat_cols,
            'cat_cols': cat_cols,
            'X_train_shape': list(X_train.shape),
            'X_val_shape': list(X_val.shape),
        }
        with open(f'{OUT_DIR}/meta_f{fold_i}.json', 'w') as f:
            json.dump(meta, f, indent=2)

        dur = time.time() - t0
        print(f"  Fold {fold_i}: X_train={X_train.shape}, X_val={X_val.shape}, [{dur:.0f}s]", flush=True)

        del X_train, X_val, X_tr_nn, X_va_nn, X_train_lgb, X_val_lgb, y_train, y_val
        del fold_df, validator
        gc.collect()

    print("\nDONE! Files saved in precomputed/", flush=True)
    print(f"  LGB: X_train_lgb_fN.parquet, X_val_lgb_fN.parquet", flush=True)
    print(f"  NN:  nn_fN.npz (X_train, X_val, y_train, y_val)", flush=True)
    print(f"  Meta: meta_fN.json", flush=True)


if __name__ == '__main__':
    main()
