# -*- coding: utf-8 -*-
"""Block 8d: Final submission for team track.

Loads:
  - team_final_out/lgb_hybrid_mae_full_test_pred.npy  (LGB Hybrid MAE full-train)
  - team_final_out/mlp_pyramid_full_test_pred.npy     (MLP pyramid 5-seed full-train)
  - Optionally: team_final_out/xgb_*_full_test_pred.npy

Applies:
  - Ridge meta trained on honest OOF (lgb_h, mlp_pyramid[, xgb])
  - Zero-routes mask (16 routes where train 14d max = 0)
  - Scale calibration (best constant from OOF)
  - Rounding to nearest int

Saves:
  - ../Submissions/team_final_stack_rounded.csv
  - Additional scaled variants (0.97 .. 1.02)
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
CACHE = Path('team_final_cache')
OUT = Path('team_final_out')
SUB_DIR = Path('../Submissions'); SUB_DIR.mkdir(exist_ok=True)


def log(msg):
    print(f"[B8D {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def compute_zero_routes():
    """Same definition as original LB-best: train 14d max == 0."""
    train = pd.read_parquet('../Data/raw/train_team_track.parquet')
    max_ts = train['timestamp'].max()
    recent = train[train['timestamp'] >= max_ts - pd.Timedelta(days=14)]
    rstats = recent.groupby('route_id')['target_2h'].agg(['max'])
    zero_routes = set(rstats[rstats['max'] == 0].index.tolist())
    return zero_routes


def main():
    log("=== Block 8d: Final submission (LGB+MLP[+XGB] Ridge stack) ===")

    # ---- Load test predictions ----
    lgb_test = np.load(OUT / 'lgb_hybrid_mae_full_test_pred.npy').astype(np.float64)
    mlp_test = np.load(OUT / 'mlp_pyramid_full_test_pred.npy').astype(np.float64)
    log(f"  lgb_test: mean={lgb_test.mean():.2f} std={lgb_test.std():.2f}")
    log(f"  mlp_test: mean={mlp_test.mean():.2f} std={mlp_test.std():.2f}")

    use_xgb = (OUT / 'xgb_winner_full_test_pred.npy').exists()
    if use_xgb:
        xgb_test = np.load(OUT / 'xgb_winner_full_test_pred.npy').astype(np.float64)
        log(f"  xgb_test: mean={xgb_test.mean():.2f} std={xgb_test.std():.2f}")

    # ---- Train Ridge meta on OOF ----
    # exp_023_oof_final.npz has y_true, lgb_g, lgb_h, mlp
    base_oof = np.load('exp_023_oof_final.npz')
    y_oof = base_oof['y_true'].astype(np.float64)

    lgb_oof = base_oof['lgb_h'].astype(np.float64)

    # NEW MLP OOF from Block 4 (pyramid 5-seed)
    new_mlp_oof_file = Path('team_block4_out/v10_pyramid_h1536_oof.npz')
    if new_mlp_oof_file.exists():
        d = np.load(new_mlp_oof_file)
        mlp_oof = d['preds'].astype(np.float64)
        log(f"  Using NEW MLP OOF (Block 4 multi-seed): mean={mlp_oof.mean():.2f} "
            f"score={metric.calculate(y_oof, mlp_oof):.6f}")
    else:
        mlp_oof = base_oof['mlp'].astype(np.float64)
        log(f"  Using old exp_023 MLP OOF: mean={mlp_oof.mean():.2f}")

    X_oof_parts = [lgb_oof, mlp_oof]
    test_parts = [lgb_test, mlp_test]
    names = ['lgb_h', 'mlp_pyramid']

    if use_xgb:
        xgb_oof_file = Path('team_block6_full_oof/xgb_hybrid_winner_oof.npy')
        if xgb_oof_file.exists():
            xgb_oof = np.load(xgb_oof_file).astype(np.float64)
            X_oof_parts.append(xgb_oof)
            test_parts.append(xgb_test)
            names.append('xgb_h')
            log(f"  Added XGB as 3rd stack model")

    X_oof = np.column_stack(X_oof_parts)
    X_test_meta = np.column_stack(test_parts)

    meta = Ridge(alpha=1.0).fit(X_oof, y_oof)
    log(f"  Ridge meta coefs: {dict(zip(names, [round(float(c), 4) for c in meta.coef_]))}")
    log(f"  Ridge meta intercept: {meta.intercept_:.4f}")
    stacked_oof = np.clip(meta.predict(X_oof), 0, None)
    sc_oof = metric.calculate(y_oof, stacked_oof)
    log(f"  OOF stack (in-sample, indicative): {sc_oof:.6f}")

    # Honest CV stack score (leave-one-fold-out)
    n_per_fold = len(y_oof) // 3
    honest = np.zeros_like(y_oof)
    for fi in range(3):
        s, e = fi * n_per_fold, (fi + 1) * n_per_fold
        mask_tr = np.r_[0:s, e:len(y_oof)]
        r = Ridge(alpha=1.0).fit(X_oof[mask_tr], y_oof[mask_tr])
        honest[s:e] = np.clip(r.predict(X_oof[s:e]), 0, None)
    sc_honest = metric.calculate(y_oof, honest)
    log(f"  OOF stack (honest LOFO): {sc_honest:.6f}")

    # ---- Apply meta to test ----
    test_stacked = np.clip(meta.predict(X_test_meta), 0, None)
    log(f"  test stacked: mean={test_stacked.mean():.2f} std={test_stacked.std():.2f}")

    # ---- Zero-routes post-processing ----
    zero_routes = compute_zero_routes()
    log(f"  zero_routes: {len(zero_routes)} routes")
    test_route_ids_path = CACHE / 'test_route_ids.npy'
    if test_route_ids_path.exists():
        test_rids = np.load(test_route_ids_path)
    else:
        # Fallback: read from raw test
        test_df = pd.read_parquet('../Data/raw/test_team_track.parquet').sort_values('id').reset_index(drop=True)
        test_rids = test_df['route_id'].values
    test_stacked_pp = test_stacked.copy()
    n_zeroed = 0
    for i, rid in enumerate(test_rids):
        if rid in zero_routes:
            test_stacked_pp[i] = 0
            n_zeroed += 1
    log(f"  zeroed {n_zeroed} test rows")

    # ---- Scale calibration from honest OOF ----
    # Find best constant scale that minimizes honest OOF after applying it
    best_s, best_sc = 1.0, sc_honest
    for s in np.arange(0.95, 1.06, 0.0025):
        scaled = np.clip(honest * s, 0, None)
        sc = metric.calculate(y_oof, scaled)
        if sc < best_sc:
            best_sc, best_s = sc, float(s)
    log(f"  Best OOF scale: {best_s:.4f}  -> {best_sc:.6f}")

    test_ids = np.load(CACHE / 'test_ids.npy').astype(int)

    # ---- Save submissions ----
    for label, preds_raw in [('raw_round', test_stacked_pp)]:
        for scale in [1.0, best_s, 0.97, 0.975, 0.98, 0.985, 0.99, 0.9925, 0.995, 0.9975, 1.0025, 1.005, 1.01, 1.015]:
            yp = np.round(np.clip(preds_raw * scale, 0, None)).astype(np.int64)
            sub = pd.DataFrame({'id': test_ids, 'y_pred': yp}).sort_values('id').reset_index(drop=True)
            tag = f'{scale:.5f}'.rstrip('0').rstrip('.')
            fname = SUB_DIR / f'team_stack_lgb_mlp_pyr_{tag}.csv'
            sub.to_csv(fname, index=False)

    log(f"\n  Saved submissions to {SUB_DIR}/team_stack_lgb_mlp_pyr_*.csv")

    # Save predictions archive
    np.savez(OUT / 'final_test_preds.npz',
             lgb_test=lgb_test, mlp_test=mlp_test,
             stacked=test_stacked, stacked_pp=test_stacked_pp,
             meta_coefs=meta.coef_, meta_intercept=meta.intercept_,
             best_scale=best_s, sc_honest=sc_honest)
    log(f"  Saved meta to final_test_preds.npz")


if __name__ == '__main__':
    main()
