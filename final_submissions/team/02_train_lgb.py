# -*- coding: utf-8 -*-
"""Block 8b: Train LGB Hybrid MAE on FULL team train, predict test.

Uses team_final_cache/X_full.parquet (features) + y_full.npy.
avg best_iter from saved models (lgb_hybrid_strong_f0/1/2.pkl) or from exp_023 meta.
"""

import time, json, sys, gc, joblib
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, '.')
from core.metric import WapePlusRbias

metric = WapePlusRbias()
CACHE = Path('team_final_cache')
OUT = Path('team_final_out'); OUT.mkdir(exist_ok=True)
LOG = OUT / 'lgb_full_train.log'

BASE_LGB = dict(
    objective='regression_l1', learning_rate=0.02,
    num_leaves=63, max_depth=9, min_child_samples=80,
    min_child_weight=0.01, min_split_gain=0.05,
    subsample=0.8, subsample_freq=1, colsample_bytree=0.7,
    reg_alpha=0.5, reg_lambda=8.0, subsample_for_bin=200000,
    random_state=42, n_jobs=-1, importance_type='gain', verbosity=-1,
)


def log(msg, fh=None):
    line = f"[B8B_LGB {datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if fh: fh.write(line + '\n'); fh.flush()


def collect_best_iters():
    """Average best_iter across saved fold models (exp_023 style).
    Handles both key conventions: step_N/global_6_10 and sN/g610.
    """
    KEY_MAP = {'s1': 'step_1', 's2': 'step_2', 's3': 'step_3',
               's4': 'step_4', 's5': 'step_5', 'g610': 'global_6_10'}
    iters = {s: [] for s in ['step_1', 'step_2', 'step_3', 'step_4', 'step_5', 'global_6_10']}
    for fi in range(3):
        m = joblib.load(f'saved_models/lgb_hybrid_strong_f{fi}.pkl')
        for key, model in m.items():
            norm_key = KEY_MAP.get(key, key)
            n = model.n_estimators_ if hasattr(model, 'n_estimators_') else model.n_estimators
            iters[norm_key].append(n)
    return {k: int(round(np.mean(v) * 1.05)) for k, v in iters.items()}  # +5% cushion


def main():
    fh = open(LOG, 'w', encoding='utf-8')
    log("=== Block 8b: LGB Hybrid full-train + test predict ===", fh)

    avg_iters = collect_best_iters()
    log(f"Averaged best_iters: {avg_iters}", fh)

    X_full = pd.read_parquet(CACHE / 'X_full.parquet')
    X_test = pd.read_parquet(CACHE / 'X_test.parquet')
    y_full = np.load(CACHE / 'y_full.npy').astype(np.float32)
    with open(CACHE / 'meta.json') as f:
        meta = json.load(f)
    cat_cols = meta['cat_cols']
    log(f"  X_full={X_full.shape}  X_test={X_test.shape}", fh)

    for c in cat_cols:
        if c in X_full.columns:
            X_full[c] = X_full[c].astype('category')
            X_test[c] = X_test[c].astype('category')

    hs_col = 'horizon_step'
    hs_full = X_full[hs_col].astype(int).values
    hs_test = X_test[hs_col].astype(int).values

    X_full_nh = X_full.drop(columns=[hs_col], errors='ignore')
    X_test_nh = X_test.drop(columns=[hs_col], errors='ignore')
    cats_nh = [c for c in cat_cols if c != hs_col]

    yp_test = np.zeros(len(X_test))
    models = {}

    for step in range(1, 6):
        mt = hs_full == step
        mte = hs_test == step
        if not mt.sum() or not mte.sum(): continue
        Xt = X_full_nh[mt].copy()
        Xte = X_test_nh[mte].copy()
        for c in cats_nh:
            if c in Xt.columns:
                Xt[c] = Xt[c].astype('category')
                Xte[c] = Xte[c].astype('category')
        n_iters = avg_iters[f'step_{step}']
        log(f"  step_{step}: n_iter={n_iters} train={Xt.shape}", fh)
        t0 = time.time()
        m = LGBMRegressor(**BASE_LGB, n_estimators=n_iters)
        m.fit(Xt, y_full[mt], categorical_feature=cats_nh)
        yp_test[mte] = np.clip(m.predict(Xte), 0, None)
        log(f"    done [{time.time()-t0:.0f}s]  mean_pred={yp_test[mte].mean():.2f}", fh)
        models[f'step_{step}'] = m
        del Xt, Xte; gc.collect()

    # Global 6-10
    mt_g = hs_full >= 6
    mte_g = hs_test >= 6
    if mt_g.sum() and mte_g.sum():
        Xt = X_full[mt_g].copy()
        Xte = X_test[mte_g].copy()
        for c in cat_cols:
            if c in Xt.columns:
                Xt[c] = Xt[c].astype('category')
                Xte[c] = Xte[c].astype('category')
        n_iters = avg_iters['global_6_10']
        log(f"  global: n_iter={n_iters} train={Xt.shape}", fh)
        t0 = time.time()
        m = LGBMRegressor(**BASE_LGB, n_estimators=n_iters)
        m.fit(Xt, y_full[mt_g], categorical_feature=cat_cols)
        yp_test[mte_g] = np.clip(m.predict(Xte), 0, None)
        log(f"    done [{time.time()-t0:.0f}s]  mean_pred={yp_test[mte_g].mean():.2f}", fh)
        models['global_6_10'] = m
        del Xt, Xte; gc.collect()

    log(f"\nOverall test mean: {yp_test.mean():.2f}", fh)

    joblib.dump(models, OUT / 'lgb_hybrid_mae_full.pkl')
    np.save(OUT / 'lgb_hybrid_mae_full_test_pred.npy', yp_test)
    log(f"Saved models + preds", fh)
    fh.close()


if __name__ == '__main__':
    main()
