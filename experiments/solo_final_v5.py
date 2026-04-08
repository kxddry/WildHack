# -*- coding: utf-8 -*-
"""Solo Final v5: Train all 4 models on full data + per-horizon Ridge + submissions.

Models:
- LGB_K: LightGBM with Kaggle features (CPU)
- XGB_K: XGBoost with Kaggle features (GPU)
- MSeed: 5-seed ResMLP average (GPU)
- WL1: Weighted-L1 ResMLP (GPU)

Stacking:
- Per-horizon Ridge (8 Ridge models, one per horizon_step)
- Coefs learned from OOF predictions
- Optional per-horizon scale (from postprocess_v3 results)
"""

import time, gc, sys, math, json, joblib
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import xgboost as xgb
from sklearn.linear_model import Ridge
from lightgbm import LGBMRegressor

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, '.')
from core.metric import WapePlusRbias
from core.data import DatasetBuilder

TRAIN_PATH = '../Data/raw/train_solo_track.parquet'
TEST_PATH = '../Data/raw/test_solo_track.parquet'
DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
metric = WapePlusRbias()
SUB_DIR = Path('../Submissions'); SUB_DIR.mkdir(exist_ok=True)
OUT_DIR = Path('solo_final_v5_out'); OUT_DIR.mkdir(exist_ok=True)

TARGET = 'target_1h'
STATUS_COLS = [f'status_{i}' for i in range(1, 7)]
SPD = 48
FORECAST_POINTS = 8
N_FOLDS = 3

LGB_PARAMS = dict(
    objective='regression_l1', boosting_type='gbdt', n_estimators=5000,
    learning_rate=0.025, num_leaves=127, max_depth=9,
    min_child_samples=60, min_child_weight=0.05, min_split_gain=0.02,
    subsample=0.85, subsample_freq=1, colsample_bytree=0.85,
    reg_alpha=0.2, reg_lambda=5.0, subsample_for_bin=300000,
    random_state=42, n_jobs=-1, verbosity=-1,
)

XGB_PARAMS = {
    'objective': 'reg:absoluteerror',
    'device': 'cuda', 'tree_method': 'hist',
    'max_depth': 12, 'max_leaves': 255,
    'learning_rate': 0.025, 'subsample': 0.85, 'colsample_bytree': 0.85,
    'reg_alpha': 0.2, 'reg_lambda': 5.0, 'min_child_weight': 60,
    'seed': 42, 'verbosity': 0,
}

SOLO_BUILD_KWARGS = dict(
    train_days=7, use_static_aggs=True, use_total_inventory_aggs=True,
    use_target_mean_hist=True, use_target_std_hist=False,
    use_target_zero_rate_hist=True, use_target_count_hist=True,
    use_default_ts_features=True, encode_cat_features=False,
    statistics=('mean', 'std'),
    static_group_keys_list=[['route_id'], ['dow'], ['pod'], ['route_id', 'dow'], ['route_id', 'pod']],
    total_inventory_group_keys_list=[['route_id'], ['route_id', 'dow'], ['route_id', 'pod'], ['route_id', 'slot']],
    target_hist_group_keys_list=[['route_id'], ['route_id', 'pod'], ['route_id', 'dow']],
)


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ================================================================
# Kaggle features
# ================================================================

def add_kaggle_features(df):
    df = df.sort_values(['route_id', 'timestamp']).reset_index(drop=True)
    rg = df.groupby('route_id', sort=False)

    for db in [1, 2, 3, 7]:
        df[f'target_same_slot_lag_{db}d'] = rg[TARGET].shift(db * SPD).astype(np.float32)
    for wd in [3, 7]:
        lags = [rg[TARGET].shift(d * SPD) for d in range(1, wd + 1)]
        df[f'target_same_slot_mean_{wd}d'] = pd.concat(lags, axis=1).mean(axis=1).astype(np.float32)
    sh = rg[TARGET].shift(1)
    rc = {}
    for w in [6, 12, 24, 48, 96]:
        rc[w] = sh.groupby(df['route_id'], sort=False).transform(
            lambda s: s.rolling(w, min_periods=1).mean()).astype(np.float32)
    for sw, lw in [(6, 48), (12, 96), (24, 96), (48, 96)]:
        df[f'target_momentum_{sw}_{lw}'] = (rc[sw] / (rc[lw] + 1e-8)).astype(np.float32)
    snz = rg[TARGET].shift(1).gt(0).astype(np.float32)
    for w in [24, 48]:
        df[f'target_nonzero_rate_{w}'] = snz.groupby(df['route_id'], sort=False).transform(
            lambda s: s.rolling(w, min_periods=1).mean()).astype(np.float32)

    if 'slot' not in df.columns:
        df['slot'] = df['timestamp'].dt.hour * 2 + df['timestamp'].dt.minute // 30
    alpha = 20; gm = df[TARGET].mean()
    for gk in [['route_id', 'slot']]:
        fn = '_'.join(str(g) for g in gk) + '_target_smoothed'
        cs = df.groupby(gk)[TARGET].cumsum() - df[TARGET]
        cc = df.groupby(gk).cumcount()
        gme = cs / cc
        df[fn] = ((cc * gme + alpha * gm) / (cc + alpha)).fillna(gm).astype(np.float32)

    extra_cols = [c for c in df.columns if any(k in c for k in
                  ['same_slot', 'momentum', 'nonzero_rate', 'smoothed'])]
    return df, extra_cols


# ================================================================
# MLP features (80 feats)
# ================================================================

def build_mlp_features(df):
    df = df.sort_values(['route_id', 'timestamp']).reset_index(drop=True)
    rg = df.groupby('route_id', sort=False)
    feats = {}

    feats['hour'] = df['timestamp'].dt.hour.astype(np.float32) / 23.0
    feats['dow'] = df['timestamp'].dt.dayofweek.astype(np.float32) / 6.0
    feats['slot'] = (df['timestamp'].dt.hour * 2 + df['timestamp'].dt.minute // 30).astype(np.float32) / 47.0
    feats['is_weekend'] = (df['timestamp'].dt.dayofweek >= 5).astype(np.float32)
    feats['sin_hour'] = np.sin(2 * np.pi * df['timestamp'].dt.hour / 24).astype(np.float32)
    feats['cos_hour'] = np.cos(2 * np.pi * df['timestamp'].dt.hour / 24).astype(np.float32)
    feats['sin_dow'] = np.sin(2 * np.pi * df['timestamp'].dt.dayofweek / 7).astype(np.float32)
    feats['cos_dow'] = np.cos(2 * np.pi * df['timestamp'].dt.dayofweek / 7).astype(np.float32)

    total_inv = df[STATUS_COLS].sum(axis=1).astype(np.float32)
    feats['total_inv'] = total_inv
    feats['log_total_inv'] = np.log1p(total_inv).astype(np.float32)
    for i, sc in enumerate(STATUS_COLS):
        feats[f'share_{i+1}'] = (df[sc] / (total_inv + 1)).astype(np.float32)
    feats['early_inv'] = df[['status_1', 'status_2', 'status_3']].sum(axis=1).astype(np.float32)
    feats['late_inv'] = df[['status_5', 'status_6']].sum(axis=1).astype(np.float32)
    feats['late_share'] = (feats['late_inv'] / (total_inv + 1)).astype(np.float32)

    for lag in [1, 2, 3, 4, 5, 6, 10, 12, 24, 48]:
        feats[f'lag_{lag}'] = rg[TARGET].shift(lag).astype(np.float32)
        feats[f'log_lag_{lag}'] = np.log1p(feats[f'lag_{lag}']).astype(np.float32)
    for db in [1, 2, 3, 7]:
        feats[f'ss_lag_{db}d'] = rg[TARGET].shift(db * SPD).astype(np.float32)
        feats[f'log_ss_lag_{db}d'] = np.log1p(feats[f'ss_lag_{db}d']).astype(np.float32)
    for wd in [3, 7]:
        lags = [rg[TARGET].shift(d * SPD) for d in range(1, wd + 1)]
        feats[f'ss_mean_{wd}d'] = pd.concat(lags, axis=1).mean(axis=1).astype(np.float32)

    sh = rg[TARGET].shift(1)
    for w in [6, 12, 24, 48, 96]:
        rm = sh.groupby(df['route_id'], sort=False).transform(
            lambda s: s.rolling(w, min_periods=1).mean()).astype(np.float32)
        feats[f'rm_{w}'] = rm
        feats[f'log_rm_{w}'] = np.log1p(rm).astype(np.float32)
        if w >= 24:
            rs = sh.groupby(df['route_id'], sort=False).transform(
                lambda s: s.rolling(w, min_periods=2).std()).astype(np.float32)
            feats[f'rs_{w}'] = rs
            feats[f'cv_{w}'] = (rs / (rm + 1e-8)).astype(np.float32)
    for sw, lw in [(6, 48), (12, 96), (24, 96)]:
        feats[f'mom_{sw}_{lw}'] = (feats[f'rm_{sw}'] / (feats[f'rm_{lw}'] + 1e-8)).astype(np.float32)
    for w in [48, 96]:
        feats[f'dev_{w}'] = (feats['lag_1'] / (feats[f'rm_{w}'] + 1e-8)).astype(np.float32)
    for p in [1, 2, 3, 6, 48]:
        feats[f'diff_{p}'] = (df[TARGET] - rg[TARGET].shift(p)).astype(np.float32)

    snz = rg[TARGET].shift(1).gt(0).astype(np.float32)
    for w in [24, 48]:
        feats[f'nzr_{w}'] = snz.groupby(df['route_id'], sort=False).transform(
            lambda s: s.rolling(w, min_periods=1).mean()).astype(np.float32)

    alpha = 20; gm = df[TARGET].mean()
    cs = df.groupby('route_id')[TARGET].cumsum() - df[TARGET]
    cc = df.groupby('route_id').cumcount()
    gme = cs / cc
    feats['te_route'] = ((cc * gme + alpha * gm) / (cc + alpha)).fillna(gm).astype(np.float32)
    feats['log_te_route'] = np.log1p(feats['te_route']).astype(np.float32)

    slot_col = df['timestamp'].dt.hour * 2 + df['timestamp'].dt.minute // 30
    gk = [df['route_id'], slot_col]
    cs2 = df[TARGET].groupby(gk).cumsum() - df[TARGET]
    cc2 = df.groupby(gk).cumcount()
    gme2 = cs2 / cc2
    feats['te_route_slot'] = ((cc2 * gme2 + alpha * gm) / (cc2 + alpha)).fillna(gm).astype(np.float32)

    feat_names = list(feats.keys())
    X = np.column_stack([feats[k].values if hasattr(feats[k], 'values') else feats[k] for k in feat_names])
    return X.astype(np.float32), feat_names, df


# ================================================================
# ResMLP
# ================================================================

class ResBlock(nn.Module):
    def __init__(self, dim, drop=0.2):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(dim, dim), nn.BatchNorm1d(dim), nn.GELU(), nn.Dropout(drop),
            nn.Linear(dim, dim), nn.BatchNorm1d(dim))
        self.act = nn.GELU()
    def forward(self, x): return self.act(x + self.net(x))

class ResMLP(nn.Module):
    def __init__(self, d_in, hidden=256, n_blocks=5, drop=0.2):
        super().__init__()
        self.proj = nn.Sequential(nn.Linear(d_in, hidden), nn.BatchNorm1d(hidden), nn.GELU())
        self.blocks = nn.Sequential(*[ResBlock(hidden, drop) for _ in range(n_blocks)])
        self.head = nn.Linear(hidden, 1)
    def forward(self, x): return self.head(self.blocks(self.proj(x))).squeeze(-1)


# ================================================================
# MAIN
# ================================================================

def main():
    log("=== Solo Final v5: Full training + per-horizon Ridge ===")
    log(f"Device: {DEVICE}")

    train_df = pd.read_parquet(TRAIN_PATH)
    test_df = pd.read_parquet(TEST_PATH)
    for c in train_df.select_dtypes('int64').columns: train_df[c] = train_df[c].astype(np.int32)
    for c in train_df.select_dtypes('float64').columns: train_df[c] = train_df[c].astype(np.float32)
    log(f"Train: {train_df.shape}, Test: {test_df.shape}")

    # Load CV metadata
    v4_meta = joblib.load('solo_stack_v4_out/meta_v4.pkl')
    v3_meta = joblib.load('solo_stack_v3_out/meta_v3.pkl')
    log(f"LGB iters (v4): {v4_meta['lgb_iters']}")
    log(f"WL1 eps (v3): {v3_meta['wl1_best_eps']}")

    # ============================================================
    # Phase 1: LGB+Kaggle final training on all data
    # ============================================================
    log("\n=== Phase 1: LGB+Kaggle on full data ===")
    t0 = time.time()
    train_kaggle, extra_cols = add_kaggle_features(train_df.copy())
    log(f"  Kaggle features: {len(extra_cols)}")
    builder = DatasetBuilder(train=train_kaggle, test=test_df, config='solo')
    X_train_full, y_train_full, X_test_lgb, meta_test = builder.build_train_test(
        return_meta_test=True, extra_numeric_features=extra_cols, **SOLO_BUILD_KWARGS)
    cat_features = [c for c in builder.cat_features if c in X_train_full.columns]
    log(f"  X_train: {X_train_full.shape}, X_test: {X_test_lgb.shape}")

    for c in cat_features:
        X_train_full[c] = X_train_full[c].astype('category')
        X_test_lgb[c] = X_test_lgb[c].astype('category')

    avg_iter = int(np.mean(v4_meta['lgb_iters']))
    log(f"  Training LGB with {avg_iter} iterations...")
    lgb_final = LGBMRegressor(**{**LGB_PARAMS, 'n_estimators': avg_iter})
    lgb_final.fit(X_train_full, y_train_full, categorical_feature=cat_features)
    yp_lgb_test = np.clip(lgb_final.predict(X_test_lgb), 0, None)
    log(f"  LGB test: mean={yp_lgb_test.mean():.2f}, elapsed={time.time()-t0:.0f}s")

    joblib.dump({'model': lgb_final, 'cat_features': cat_features, 'n_estimators': avg_iter},
                str(OUT_DIR / 'lgb_kaggle_final.pkl'))

    # ============================================================
    # Phase 2: XGB+Kaggle on full data
    # ============================================================
    log("\n=== Phase 2: XGB+Kaggle on full data ===")
    t0 = time.time()
    # Label-encode cat features for XGBoost
    X_train_xgb = X_train_full.copy()
    X_test_xgb_df = X_test_lgb.copy()
    for c in cat_features:
        cats = pd.Categorical(pd.concat([X_train_xgb[c], X_test_xgb_df[c]]))
        X_train_xgb[c] = cats.codes[:len(X_train_xgb)].astype(np.int32)
        X_test_xgb_df[c] = cats.codes[len(X_train_xgb):].astype(np.int32)

    # XGB iters from OOF
    xgb_meta = joblib.load('solo_xgb_out/meta_xgb.pkl')
    avg_xgb_iter = int(np.mean(xgb_meta['iters']))
    log(f"  Training XGB with {avg_xgb_iter} iterations (avg from {xgb_meta['iters']})...")

    dtrain = xgb.DMatrix(X_train_xgb, label=y_train_full)
    dtest = xgb.DMatrix(X_test_xgb_df)
    bst = xgb.train(XGB_PARAMS, dtrain, num_boost_round=avg_xgb_iter)
    yp_xgb_test = np.clip(bst.predict(dtest), 0, None)
    log(f"  XGB test: mean={yp_xgb_test.mean():.2f}, elapsed={time.time()-t0:.0f}s")

    bst.save_model(str(OUT_DIR / 'xgb_kaggle_final.json'))
    del dtrain, dtest, bst, X_train_full, X_train_xgb, X_test_xgb_df, X_test_lgb
    gc.collect()

    # ============================================================
    # Phase 3: MLP features build (for all 4 MLPs)
    # ============================================================
    log("\n=== Phase 3: MLP features ===")
    t0 = time.time()
    X_mlp_all, feat_names, train_df_feat = build_mlp_features(train_df)
    log(f"  MLP features: {X_mlp_all.shape}")

    # Build train data (last 5 days, same as v3)
    rg = train_df_feat.groupby('route_id', sort=False)
    has_future = np.ones(len(train_df_feat), dtype=bool)
    for step in range(1, FORECAST_POINTS + 1):
        has_future &= rg[TARGET].shift(-step).notna().values
    max_ts_f = train_df_feat['timestamp'].max()
    train_start = max_ts_f - pd.Timedelta(days=5)
    mask = has_future & (train_df_feat['timestamp'].values >= train_start.to_numpy())
    idx_tr = np.where(mask)[0]
    y_col = train_df_feat[TARGET].values.astype(np.float32)
    Xb, yb = [], []
    for step in range(1, FORECAST_POINTS + 1):
        Xb.append(X_mlp_all[idx_tr])
        yb.append(y_col[idx_tr + step])
    X_full_mlp = np.hstack([np.vstack(Xb), np.concatenate([
        np.full(len(idx_tr), s / FORECAST_POINTS, dtype=np.float32)
        for s in range(1, FORECAST_POINTS + 1)]).reshape(-1, 1)])
    y_full_mlp = np.concatenate(yb)
    log(f"  MLP train: {X_full_mlp.shape}")

    mean_f = np.nanmean(X_full_mlp, axis=0).astype(np.float32)
    std_f = np.nanstd(X_full_mlp, axis=0).astype(np.float32); std_f[std_f < 1e-8] = 1.0
    Xfs = np.nan_to_num((X_full_mlp - mean_f) / std_f, nan=0).astype(np.float32)
    y_log = np.log1p(np.clip(y_full_mlp, 0, None)).astype(np.float32)
    d_in = Xfs.shape[1]

    # Test features
    test_anc_idx = train_df_feat.sort_values(['route_id', 'timestamp']).groupby('route_id').tail(1).index
    test_anc_mask = np.zeros(len(train_df_feat), dtype=bool); test_anc_mask[test_anc_idx] = True
    X_test_parts = []
    for step in range(1, FORECAST_POINTS + 1):
        bx = X_mlp_all[test_anc_mask]
        hs = np.full((bx.shape[0], 1), step / FORECAST_POINTS, dtype=np.float32)
        X_test_parts.append(np.hstack([bx, hs]))
    X_test_mlp = np.vstack(X_test_parts)
    Xtes = np.nan_to_num((X_test_mlp - mean_f) / std_f, nan=0).astype(np.float32)
    mlp_route_ids = train_df_feat.loc[test_anc_mask, 'route_id'].sort_values().values
    n_routes = len(mlp_route_ids)
    log(f"  MLP test: {Xtes.shape}, elapsed={time.time()-t0:.0f}s")
    del X_mlp_all, train_df_feat; gc.collect()

    def predict_mlp_test(model):
        model.eval()
        with torch.no_grad():
            raw = np.clip(np.expm1(model(torch.from_numpy(Xtes).to(DEVICE)).cpu().numpy()), 0, None)
        pred_map = {}
        for si, step in enumerate(range(1, FORECAST_POINTS + 1)):
            for ri, rid in enumerate(mlp_route_ids):
                pred_map[(rid, step)] = raw[si * n_routes + ri]
        return np.array([pred_map.get((rid, hs), 0.0)
                         for rid, hs in zip(meta_test['route_id'].values, meta_test['horizon_step'].values)])

    def train_mlp_full(epochs, weighted=False, seed=42, bs=16000, save_path=None):
        """Train ResMLP on full MLP data."""
        torch.manual_seed(seed); np.random.seed(seed)
        Xt = torch.from_numpy(Xfs).to(DEVICE)
        yt = torch.from_numpy(y_log).to(DEVICE)
        model = ResMLP(d_in, 256, 5, 0.2).to(DEVICE)
        opt = torch.optim.AdamW(model.parameters(), lr=8e-4, weight_decay=1e-4)
        sched = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(opt, T_0=30, T_mult=2, eta_min=8e-6)

        if weighted:
            weights = np.sqrt(np.clip(y_full_mlp, 1, None)).astype(np.float32)
            weights = weights / weights.mean()
            wt = torch.from_numpy(weights).to(DEVICE)

        n = Xt.shape[0]
        for ep in range(epochs):
            model.train()
            perm = torch.randperm(n, device=DEVICE)
            for i in range(0, n, bs):
                idx = perm[i:i+bs]
                pred = model(Xt[idx])
                if weighted:
                    loss = (torch.abs(pred - yt[idx]) * wt[idx]).mean()
                else:
                    loss = torch.nn.functional.l1_loss(pred, yt[idx])
                opt.zero_grad(set_to_none=True); loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
            sched.step()
            if (ep + 1) % 20 == 0:
                log(f"    ep {ep+1}/{epochs}")

        test_preds = predict_mlp_test(model)

        # Save model state if path provided
        if save_path:
            torch.save({
                'state': model.state_dict(),
                'mean': mean_f, 'std': std_f, 'd_in': d_in,
                'epochs': epochs, 'weighted': weighted, 'seed': seed, 'bs': bs,
            }, str(save_path))
            log(f"    Saved model to {save_path}")

        del Xt, yt, model
        if weighted: del wt
        gc.collect(); torch.cuda.empty_cache()
        return test_preds

    # ============================================================
    # Phase 4: Train MultiSeed (5 seeds)
    # ============================================================
    log("\n=== Phase 4: MultiSeed ResMLP (5 seeds) ===")
    mseed_epochs = 50
    mseed_test_list = []
    for seed in [42, 123, 456, 789, 2024]:
        t0 = time.time()
        log(f"  Seed {seed}...")
        preds = train_mlp_full(mseed_epochs, weighted=False, seed=seed, bs=16000,
                                save_path=OUT_DIR / f'mseed_s{seed}.pt')
        mseed_test_list.append(preds)
        log(f"    Seed {seed} done: mean={preds.mean():.2f} [{time.time()-t0:.0f}s]")
    yp_mseed_test = np.mean(mseed_test_list, axis=0)
    log(f"  MSeed avg test: mean={yp_mseed_test.mean():.2f}")

    # ============================================================
    # Phase 5: Train Weighted-L1
    # ============================================================
    log("\n=== Phase 5: Weighted-L1 ResMLP ===")
    t0 = time.time()
    wl1_epochs = int(np.mean(v3_meta['wl1_best_eps'])) if v3_meta.get('wl1_best_eps') else 50
    log(f"  Training WL1 with {wl1_epochs} epochs...")
    yp_wl1_test = train_mlp_full(wl1_epochs, weighted=True, seed=42, bs=8000,
                                    save_path=OUT_DIR / 'wl1_final.pt')
    log(f"  WL1 test: mean={yp_wl1_test.mean():.2f} [{time.time()-t0:.0f}s]")

    # ============================================================
    # Phase 6: Per-horizon Ridge stacking
    # ============================================================
    log("\n=== Phase 6: Per-horizon Ridge stacking ===")

    # Load OOF predictions
    v4 = np.load('solo_stack_v4_out/oof_v4.npz')
    v3_oof = np.load('solo_stack_v3_out/oof_v3.npz')
    xgb_oof = np.load('solo_xgb_out/oof_xgb.npz')
    y_all = v4['y_true']
    lgb_oof = v4['lgb_kaggle']
    mseed_oof = v3_oof['mseed']
    wl1_oof = v3_oof['wl1']
    xgb_oof_arr = xgb_oof['xgb_kaggle']

    routes_sorted = sorted(train_df['route_id'].unique())
    n_routes_sorted = len(routes_sorted)
    step_ids_oof = np.tile(np.repeat(np.arange(1, FORECAST_POINTS + 1), n_routes_sorted), N_FOLDS)

    # Train per-horizon Ridge models
    per_horizon_ridges = {}
    for step in range(1, FORECAST_POINTS + 1):
        mask = step_ids_oof == step
        X_step = np.column_stack([lgb_oof[mask], mseed_oof[mask], xgb_oof_arr[mask], wl1_oof[mask]])
        y_step = y_all[mask]
        r = Ridge(alpha=1.0); r.fit(X_step, y_step)
        per_horizon_ridges[step] = r
        log(f"  Step {step}: coefs={[round(float(c),3) for c in r.coef_]}, intercept={r.intercept_:.2f}")

    # Apply per-horizon Ridge to test predictions
    test_steps = meta_test['horizon_step'].values
    yp_stack = np.zeros(len(meta_test))
    for step in range(1, FORECAST_POINTS + 1):
        mask_t = test_steps == step
        X_t = np.column_stack([yp_lgb_test[mask_t], yp_mseed_test[mask_t], yp_xgb_test[mask_t], yp_wl1_test[mask_t]])
        yp_stack[mask_t] = np.clip(per_horizon_ridges[step].predict(X_t), 0, None)
    log(f"  Stack test: mean={yp_stack.mean():.2f}")

    # Save meta
    joblib.dump({
        'per_horizon_ridges': per_horizon_ridges,
        'mseed_epochs': mseed_epochs,
        'wl1_epochs': wl1_epochs,
        'lgb_n_est': avg_iter,
        'xgb_iters': avg_xgb_iter,
    }, str(OUT_DIR / 'meta_v5.pkl'))

    # ============================================================
    # Phase 7: Post-processing + submissions
    # ============================================================
    log("\n=== Phase 7: Submissions ===")

    # Per-route min/max clip
    max_ts = train_df['timestamp'].max()
    recent = train_df[train_df['timestamp'] >= max_ts - pd.Timedelta(days=14)]
    stats = recent.groupby('route_id')[TARGET].agg(['min', 'max']).reset_index()
    stats_dict = dict(zip(stats['route_id'], zip(stats['min'], stats['max'])))
    yp_pp = yp_stack.copy()
    for i, rid in enumerate(meta_test['route_id'].values):
        if rid in stats_dict:
            mn, mx = stats_dict[rid]
            yp_pp[i] = np.clip(yp_pp[i], mn, mx)

    # Save all predictions for analysis
    np.savez(str(OUT_DIR / 'test_preds.npz'),
             lgb=yp_lgb_test, xgb=yp_xgb_test, mseed=yp_mseed_test, wl1=yp_wl1_test,
             stack=yp_stack, stack_pp=yp_pp, meta_test_route=meta_test['route_id'].values,
             meta_test_step=meta_test['horizon_step'].values, meta_test_id=meta_test['id'].values)

    # Generate submissions with various scales
    for scale in [0.98, 0.99, 1.0, 1.005, 1.01, 1.011, 1.0125, 1.014, 1.015, 1.0175, 1.02, 1.03]:
        sub = meta_test[['id']].copy()
        sub['y_pred'] = np.clip(yp_pp * scale, 0, None)
        sub.sort_values('id').to_csv(str(SUB_DIR / f'solo_v5_{scale}.csv'), index=False)

    log(f"\n  Saved 12 submissions: solo_v5_0.98 .. solo_v5_1.03")
    log(f"  Recommended: solo_v5_1.015.csv (match previous best scale)")
    log("\nDONE!")


if __name__ == '__main__':
    main()
