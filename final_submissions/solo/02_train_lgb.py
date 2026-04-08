# -*- coding: utf-8 -*-
"""Train LGB Poisson on FULL train data (no val), fixed n_estimators.

Uses cached features from full_cache/. Saves model + test predictions.
"""

import time, json, sys, pickle, gc
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

CACHE = Path('full_cache')
OUT = Path('solo_full_train_out'); OUT.mkdir(exist_ok=True)

# n_estimators: avg of fold OOF best_iters (3687, 3264, 3616) * 1.05 rounded
N_EST = 3700

LGB_PARAMS = dict(
    objective='poisson', boosting_type='gbdt',
    n_estimators=N_EST,
    learning_rate=0.025, num_leaves=127, max_depth=9,
    min_child_samples=60, subsample=0.85, subsample_freq=1, colsample_bytree=0.85,
    reg_alpha=0.2, reg_lambda=5.0, random_state=42, n_jobs=-1, verbosity=-1,
)


def log(msg):
    print(f"[LGB_FULL {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    log("=== LGB Poisson — FULL TRAIN ===")
    log(f"n_estimators={N_EST}")

    X_full = pd.read_parquet(CACHE / 'X_full.parquet')
    y_full = np.load(CACHE / 'y_full.npy')
    X_test = pd.read_parquet(CACHE / 'X_test.parquet')
    with open(CACHE / 'cat_features.json') as f:
        cat_features = json.load(f)

    log(f"  X_full={X_full.shape}  X_test={X_test.shape}  y_full={y_full.shape}")

    for c in cat_features:
        if c in X_full.columns:
            X_full[c] = X_full[c].astype('category')
            X_test[c] = X_test[c].astype('category')

    t0 = time.time()
    m = LGBMRegressor(**LGB_PARAMS)
    m.fit(X_full, y_full, categorical_feature=cat_features)
    log(f"  trained in {time.time()-t0:.0f}s")

    t0 = time.time()
    test_pred = np.clip(m.predict(X_test), 0, None)
    log(f"  test predict in {time.time()-t0:.1f}s  mean={test_pred.mean():.1f} std={test_pred.std():.1f}")

    with open(OUT / 'lgb_poisson_full.pkl', 'wb') as f:
        pickle.dump(m, f)
    np.save(OUT / 'lgb_poisson_full_test_pred.npy', test_pred)
    log(f"  Saved model + preds to {OUT}/")


if __name__ == '__main__':
    main()
