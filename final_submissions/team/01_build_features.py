# -*- coding: utf-8 -*-
"""Block 8a: Build TEST features for team track using same pipeline as precompute_features.py.

Generates:
  team_final_cache/X_full.parquet     — full training features (whole train, no cutoff)
  team_final_cache/y_full.npy
  team_final_cache/X_test.parquet     — aligned test features
  team_final_cache/test_ids.npy
  team_final_cache/meta.json          — feat_cols, cat_cols

This is needed because precomputed/ only has fold OOT splits (train cut to <=cutoff),
not full train + test. For the final submission we need train = all + test features.
"""

import time, json, sys, gc
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, '.')
from core.data import DatasetBuilder
from precompute_features import add_kaggle_features, get_extra_names, BUILD_KWARGS

TRAIN_PATH = '../Data/raw/train_team_track.parquet'
TEST_PATH = '../Data/raw/test_team_track.parquet'
OUT = Path('team_final_cache'); OUT.mkdir(exist_ok=True)


def log(msg):
    print(f"[B8A {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    log("=== Block 8a: Build full team train + test features ===")

    needed = ['X_full.parquet', 'y_full.npy', 'X_test.parquet', 'test_ids.npy', 'meta.json']
    if all((OUT / f).exists() for f in needed):
        log("Cache already exists, skipping")
        return

    train_df = pd.read_parquet(TRAIN_PATH)
    test_df = pd.read_parquet(TEST_PATH)
    for c in train_df.select_dtypes('int64').columns:
        train_df[c] = train_df[c].astype(np.int32)
    for c in train_df.select_dtypes('float64').columns:
        train_df[c] = train_df[c].astype(np.float32)
    log(f"  train={train_df.shape}  test={test_df.shape}")

    log("Adding kaggle features...")
    t0 = time.time()
    train_df = add_kaggle_features(train_df)
    extra_feats = get_extra_names(train_df)
    log(f"  kaggle: {len(extra_feats)} extras [{time.time()-t0:.0f}s]")

    log("DatasetBuilder.build_train_test...")
    t0 = time.time()
    builder = DatasetBuilder(train=train_df, test=test_df, config='team')
    bk = {**BUILD_KWARGS, 'extra_numeric_features': extra_feats}
    X_full, y_full, X_test, meta_test = builder.build_train_test(
        return_meta_test=True, **bk)
    cat_cols = builder.cat_features.copy()
    feat_cols = list(X_full.columns)
    log(f"  X_full={X_full.shape} X_test={X_test.shape} cats={len(cat_cols)} [{time.time()-t0:.0f}s]")

    # Align test columns
    X_test = X_test[feat_cols]

    # Cast to save space
    for c in X_full.select_dtypes('int64').columns:
        X_full[c] = X_full[c].astype(np.int32)
        X_test[c] = X_test[c].astype(np.int32)
    for c in X_full.select_dtypes('float64').columns:
        X_full[c] = X_full[c].astype(np.float32)
        X_test[c] = X_test[c].astype(np.float32)

    log("Saving...")
    X_full.to_parquet(OUT / 'X_full.parquet')
    X_test.to_parquet(OUT / 'X_test.parquet')
    y_full_np = y_full.to_numpy() if hasattr(y_full, 'to_numpy') else np.asarray(y_full)
    np.save(OUT / 'y_full.npy', y_full_np.astype(np.float32))

    ids = meta_test['id'].astype(np.int64).values if hasattr(meta_test, 'columns') else np.arange(len(X_test))
    np.save(OUT / 'test_ids.npy', ids)
    # Also save route_id for zero-routes post-processing
    if hasattr(meta_test, 'columns') and 'route_id' in meta_test.columns:
        np.save(OUT / 'test_route_ids.npy', meta_test['route_id'].astype(np.int64).values)

    with open(OUT / 'meta.json', 'w') as f:
        json.dump({'feat_cols': feat_cols, 'cat_cols': cat_cols,
                   'X_full_shape': list(X_full.shape),
                   'X_test_shape': list(X_test.shape)}, f, indent=2)
    log(f"Saved to {OUT}/")
    log(f"  X_full: {(OUT/'X_full.parquet').stat().st_size/1e9:.2f} GB")
    log(f"  X_test: {(OUT/'X_test.parquet').stat().st_size/1e6:.2f} MB")


if __name__ == '__main__':
    main()
