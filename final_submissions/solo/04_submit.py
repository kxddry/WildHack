# -*- coding: utf-8 -*-
"""Final submission: Ridge stack of full-train LGB_P + 5-seed MLP_P + XGB_Heavy_P.

Loads test predictions produced by:
  - solo_train_full_lgb.py   -> solo_full_train_out/lgb_poisson_full_test_pred.npy
  - solo_train_full_gpu.py   -> solo_full_train_out/xgb_heavy_poisson_full_test_pred.npy
                                solo_full_train_out/mlp_poisson_full_test_pred.npy

Ridge meta weights are learned from the OOF OOF predictions
(LGB_Poisson + MLP_MSeed + XGB_Heavy_Poisson -> y_true).
"""

import json, sys
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, '.')
from core.metric import WapePlusRbias

metric = WapePlusRbias()
CACHE = Path('full_cache')
OUT = Path('solo_full_train_out')
SUB_DIR = Path('../Submissions'); SUB_DIR.mkdir(exist_ok=True)


def log(msg):
    print(f"[SUBMIT {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    log("=== Final submission: Ridge stack (full-trained) ===")

    # ---- Load test predictions ----
    lgb_test = np.load(OUT / 'lgb_poisson_full_test_pred.npy')
    xgb_test = np.load(OUT / 'xgb_heavy_poisson_full_test_pred.npy')
    mlp_test = np.load(OUT / 'mlp_poisson_full_test_pred.npy')
    log(f"  lgb_test: mean={lgb_test.mean():.1f} std={lgb_test.std():.1f}")
    log(f"  xgb_test: mean={xgb_test.mean():.1f} std={xgb_test.std():.1f}")
    log(f"  mlp_test: mean={mlp_test.mean():.1f} std={mlp_test.std():.1f}")

    # ---- Train Ridge meta on full OOF ----
    d_lgb = np.load('lgb_poisson_oof_out/oof.npz')
    d_mlp = np.load('mlp_poisson_mseed_out/oof.npz')
    d_xgb = np.load('xgb_poisson_heavy_oof_out/oof.npz')
    y_oof = d_lgb['y_true'].astype(np.float64)
    X_oof = np.column_stack([
        d_lgb['lgb_poisson'].astype(np.float64),
        d_mlp['oof_mseed'].astype(np.float64),
        d_xgb['xgb_poisson_heavy'].astype(np.float64),
    ])
    meta = Ridge(alpha=1.0).fit(X_oof, y_oof)
    stacked_oof = np.clip(meta.predict(X_oof), 0, None)
    sc_oof = metric.calculate(y_oof, stacked_oof)
    sc_oof_r = metric.calculate(y_oof, np.round(stacked_oof))
    log(f"  meta coefs: lgb={meta.coef_[0]:.4f} mlp={meta.coef_[1]:.4f} xgb={meta.coef_[2]:.4f}")
    log(f"  meta intercept: {meta.intercept_:.2f}")
    log(f"  OOF stack (raw)   : {sc_oof:.6f}")
    log(f"  OOF stack (round) : {sc_oof_r:.6f}")

    # ---- Apply to test ----
    X_test_meta = np.column_stack([lgb_test, mlp_test, xgb_test])
    test_pred = np.clip(meta.predict(X_test_meta), 0, None)
    test_pred_round = np.round(test_pred).astype(np.int64)
    log(f"  test stacked: mean={test_pred.mean():.1f} std={test_pred.std():.1f}")

    # ---- Build submission ----
    test_ids = np.load(CACHE / 'test_ids.npy')
    sub = pd.DataFrame({'id': test_ids.astype(int), 'y_pred': test_pred_round})
    sub = sub.sort_values('id').reset_index(drop=True)
    sub_path = SUB_DIR / 'solo_stack3_poisson_fulltrain_rounded.csv'
    sub.to_csv(sub_path, index=False)
    log(f"\n  Saved: {sub_path}")
    log(f"  rows={len(sub)} mean={sub['y_pred'].mean():.1f}")
    log(f"  head:\n{sub.head(3).to_string()}")

    # Save metadata
    np.savez(OUT / 'final_test_preds.npz',
             lgb=lgb_test, mlp=mlp_test, xgb=xgb_test,
             stacked=test_pred, stacked_round=test_pred_round,
             meta_coefs=meta.coef_, meta_intercept=meta.intercept_)
    with open(OUT / 'final_submission_meta.json', 'w') as f:
        json.dump({
            'meta_coefs': [float(c) for c in meta.coef_],
            'meta_intercept': float(meta.intercept_),
            'oof_stack_raw': float(sc_oof),
            'oof_stack_round': float(sc_oof_r),
            'test_mean': float(test_pred_round.mean()),
            'submission': str(sub_path),
        }, f, indent=2)
    log(f"  Saved meta: {OUT / 'final_submission_meta.json'}")


if __name__ == '__main__':
    main()
