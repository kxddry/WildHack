# -*- coding: utf-8 -*-
"""Build full-train kaggle features + test features, cache to parquet.

Run ONCE before the full-train LGB/XGB/MLP scripts.
Cache layout:
  full_cache/
    X_full.parquet       # full train features
    y_full.npy
    X_test.parquet       # aligned test features
    test_ids.npy
    cat_features.json
"""

import time, json, sys
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, '.')
from core.data import DatasetBuilder
from solo_final_v5 import add_kaggle_features, SOLO_BUILD_KWARGS

TRAIN_PATH = '../Data/raw/train_solo_track.parquet'
TEST_PATH = '../Data/raw/test_solo_track.parquet'
CACHE = Path('full_cache'); CACHE.mkdir(exist_ok=True)


def log(msg):
    print(f"[BUILD {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    log("=== Build full kaggle features (train + test) ===")

    # Short-circuit if cache exists
    needed = ['X_full.parquet', 'y_full.npy', 'X_test.parquet', 'test_ids.npy', 'cat_features.json']
    if all((CACHE / f).exists() for f in needed):
        log("Cache already exists:")
        for f in needed:
            p = CACHE / f
            log(f"  {f}: {p.stat().st_size/1e6:.1f} MB")
        return

    train_df = pd.read_parquet(TRAIN_PATH)
    test_df = pd.read_parquet(TEST_PATH)
    for c in train_df.select_dtypes('int64').columns: train_df[c] = train_df[c].astype(np.int32)
    for c in train_df.select_dtypes('float64').columns: train_df[c] = train_df[c].astype(np.float32)
    log(f"  train={train_df.shape}  test={test_df.shape}")

    t0 = time.time()
    train_kaggle, extra_cols = add_kaggle_features(train_df.copy())
    log(f"  add_kaggle_features: {len(extra_cols)} extras [{time.time()-t0:.0f}s]")

    t0 = time.time()
    builder = DatasetBuilder(train=train_kaggle, test=test_df, config='solo')
    X_full, y_full, X_test, meta_test = builder.build_train_test(
        return_meta_test=True, extra_numeric_features=extra_cols, **SOLO_BUILD_KWARGS)
    cat_features = [c for c in builder.cat_features if c in X_full.columns]
    log(f"  builder.build_train_test: X_full={X_full.shape} X_test={X_test.shape} "
        f"cats={len(cat_features)} [{time.time()-t0:.0f}s]")

    # Align test columns to train column order
    X_test = X_test[list(X_full.columns)]

    t0 = time.time()
    X_full.to_parquet(CACHE / 'X_full.parquet')
    X_test.to_parquet(CACHE / 'X_test.parquet')
    y_full_np = y_full.to_numpy() if hasattr(y_full, 'to_numpy') else np.asarray(y_full)
    np.save(CACHE / 'y_full.npy', y_full_np)
    ids = meta_test['id'].astype(np.int64).values if hasattr(meta_test, 'columns') else np.arange(len(X_test))
    np.save(CACHE / 'test_ids.npy', ids)
    with open(CACHE / 'cat_features.json', 'w') as f:
        json.dump(cat_features, f)
    log(f"  Saved to {CACHE}/ [{time.time()-t0:.0f}s]")
    log(f"  X_full: {(CACHE/'X_full.parquet').stat().st_size/1e9:.2f} GB")
    log(f"  X_test: {(CACHE/'X_test.parquet').stat().st_size/1e6:.2f} MB")


if __name__ == '__main__':
    main()
