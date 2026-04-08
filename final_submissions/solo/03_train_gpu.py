# -*- coding: utf-8 -*-
"""Train HEAVY XGB Poisson + 5-seed ResMLP Poisson on FULL train data.

Both use cached features from full_cache/. Sequential on GPU.
Saves models + test predictions.
"""

import time, json, sys, gc
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import xgboost as xgb
import torch
import torch.nn as nn

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

CACHE = Path('full_cache')
OUT = Path('solo_full_train_out'); OUT.mkdir(exist_ok=True)
DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

SEEDS = [42, 123, 456, 789, 2024]
MLP_EPOCHS = 15   # based on OOF best_ep: avg ~11, 15 gives a small cushion

# XGB: avg iters (4735, 3528, 3255) * 1.05 = 4000
XGB_N_ROUND = 4000
XGB_HEAVY = {
    'objective': 'count:poisson', 'device': 'cuda', 'tree_method': 'hist',
    'max_depth': 14, 'max_leaves': 511, 'learning_rate': 0.015,
    'subsample': 0.9, 'colsample_bytree': 0.9, 'colsample_bynode': 0.9,
    'min_child_weight': 40, 'max_bin': 512,
    'reg_alpha': 0.3, 'reg_lambda': 3.0, 'seed': 42, 'verbosity': 1,
}

# MLP: same s01_base config
MLP_CFG = dict(hidden=256, n_blocks=5, drop=0.20, bs=16000, lr=1.0e-3, wd=1e-4, T_0=30)


def log(msg):
    print(f"[GPU_FULL {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


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
    def forward(self, x):
        return self.head(self.blocks(self.proj(x))).squeeze(-1)


def encode_cats_int_inplace(X, X_test, cat_features):
    """Consistent cat encoding using union of train+test categories."""
    for c in cat_features:
        if c in X.columns:
            cats = pd.Categorical(pd.concat([X[c], X_test[c]]))
            X[c] = cats.codes[:len(X)].astype(np.int32)
            X_test[c] = cats.codes[len(X):].astype(np.int32)
    return X, X_test


def train_xgb_full(X_full, y_full, X_test, cat_features):
    log("\n=== XGB Heavy Poisson — full train (GPU) ===")
    X_tr = X_full.copy(); X_te = X_test.copy()
    X_tr, X_te = encode_cats_int_inplace(X_tr, X_te, cat_features)
    log(f"  X_tr={X_tr.shape}, X_te={X_te.shape}")

    t0 = time.time()
    dtrain = xgb.DMatrix(X_tr, label=y_full)
    dtest = xgb.DMatrix(X_te)
    log(f"  DMatrix built [{time.time()-t0:.0f}s]")

    t0 = time.time()
    bst = xgb.train(XGB_HEAVY, dtrain, num_boost_round=XGB_N_ROUND,
                    evals=[(dtrain, 'train')], verbose_eval=500)
    log(f"  trained in {time.time()-t0:.0f}s")

    test_pred = np.clip(bst.predict(dtest), 0, None)
    log(f"  test predict: mean={test_pred.mean():.1f}")

    bst.save_model(str(OUT / 'xgb_heavy_poisson_full.json'))
    np.save(OUT / 'xgb_heavy_poisson_full_test_pred.npy', test_pred)
    log(f"  Saved XGB full model + preds")

    del dtrain, dtest, bst, X_tr; gc.collect()
    return test_pred, X_te


def train_mlp_full(X_full, y_full, X_test_int, cat_features):
    """X_test_int: test with cats already encoded as int (from XGB step).
    We also need to encode X_full the same way.
    """
    log("\n=== MLP Poisson 5-seed — full train (GPU) ===")
    log(f"  MLP cfg: {MLP_CFG}  epochs={MLP_EPOCHS}  seeds={SEEDS}")

    # Encode X_full cats as int (same as train)
    X_tr = X_full.copy()
    for c in cat_features:
        if c in X_tr.columns:
            # Use global categories (match test encoding)
            cats = pd.Categorical(pd.concat([X_tr[c], X_test_int[c].astype(object)]))
            # But X_test_int is already int codes. Re-encode X_full consistently:
            pass
    # Simpler: just encode X_full cats as int codes independently
    X_tr_arr = X_tr.copy()
    for c in cat_features:
        if c in X_tr_arr.columns:
            X_tr_arr[c] = pd.Categorical(X_tr_arr[c]).codes.astype(np.int32)
    X_tr_np = X_tr_arr.astype(np.float32).values
    X_te_np = X_test_int.astype(np.float32).values
    log(f"  X_tr_np={X_tr_np.shape}  X_te_np={X_te_np.shape}")

    mean = np.nanmean(X_tr_np, axis=0).astype(np.float32)
    std = np.nanstd(X_tr_np, axis=0).astype(np.float32); std[std < 1e-8] = 1.0
    X_tr_np = np.nan_to_num((X_tr_np - mean) / std, nan=0).astype(np.float32)
    X_te_np = np.nan_to_num((X_te_np - mean) / std, nan=0).astype(np.float32)
    X_tr_np = np.clip(X_tr_np, -10, 10)
    X_te_np = np.clip(X_te_np, -10, 10)

    np.savez(OUT / 'mlp_full_stats.npz', mean=mean, std=std)

    Xt = torch.from_numpy(X_tr_np).to(DEVICE)
    yt = torch.from_numpy(y_full.astype(np.float32)).to(DEVICE)
    Xv = torch.from_numpy(X_te_np).to(DEVICE)
    del X_tr_np, X_te_np, X_tr, X_tr_arr; gc.collect()
    log(f"  GPU VRAM: {torch.cuda.memory_allocated()/1e9:.2f} GB")

    log_mean = float(np.log(max(y_full.mean(), 1e-6)))
    d_in = Xt.shape[1]
    n = Xt.shape[0]

    seed_preds = []
    for sd in SEEDS:
        log(f"  -- seed {sd} --")
        torch.manual_seed(sd); np.random.seed(sd)
        model = ResMLP(d_in, hidden=MLP_CFG['hidden'], n_blocks=MLP_CFG['n_blocks'],
                       drop=MLP_CFG['drop']).to(DEVICE)
        with torch.no_grad():
            model.head.bias.fill_(log_mean)
            model.head.weight.zero_()

        loss_fn = nn.PoissonNLLLoss(log_input=True, reduction='mean')
        opt = torch.optim.AdamW(model.parameters(), lr=MLP_CFG['lr'],
                                weight_decay=MLP_CFG['wd'])
        sched = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(
            opt, T_0=MLP_CFG['T_0'], T_mult=2, eta_min=MLP_CFG['lr'] * 0.01)

        t0 = time.time()
        bs = MLP_CFG['bs']
        for ep in range(MLP_EPOCHS):
            model.train()
            perm = torch.randperm(n, device=DEVICE)
            tot_loss = 0.0; nb = 0
            for i in range(0, n, bs):
                idx = perm[i:i+bs]
                pred = model(Xt[idx])
                pred = torch.clamp(pred, min=-15.0, max=20.0)
                loss = loss_fn(pred, yt[idx])
                opt.zero_grad(set_to_none=True); loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
                tot_loss += float(loss.item()); nb += 1
            sched.step()
            if (ep + 1) % 5 == 0 or ep == 0:
                log(f"    ep {ep+1:2d}: loss={tot_loss/max(nb,1):.1f}")

        model.eval()
        with torch.no_grad():
            pv = torch.clamp(model(Xv), min=-15.0, max=20.0)
            p_test = np.clip(torch.exp(pv).cpu().numpy(), 0, None)
        log(f"  seed {sd}: test mean={p_test.mean():.1f} t={time.time()-t0:.0f}s")
        seed_preds.append(p_test)

        torch.save({k: v.detach().cpu().clone() for k, v in model.state_dict().items()},
                   OUT / f'mlp_full_seed{sd}.pt')

        del model, opt, sched; gc.collect(); torch.cuda.empty_cache()

    mlp_test = np.mean(seed_preds, axis=0)
    log(f"  MLP 5-seed avg: mean={mlp_test.mean():.1f} std={mlp_test.std():.1f}")
    np.save(OUT / 'mlp_poisson_full_test_pred.npy', mlp_test)
    np.save(OUT / 'mlp_poisson_full_seed_preds.npy', np.stack(seed_preds, axis=0))

    del Xt, yt, Xv; gc.collect(); torch.cuda.empty_cache()
    return mlp_test


def main():
    log("=== GPU full training: XGB Heavy + 5-seed MLP ===")
    X_full = pd.read_parquet(CACHE / 'X_full.parquet')
    y_full = np.load(CACHE / 'y_full.npy').astype(np.float32)
    X_test = pd.read_parquet(CACHE / 'X_test.parquet')
    with open(CACHE / 'cat_features.json') as f:
        cat_features = json.load(f)
    log(f"  X_full={X_full.shape}  X_test={X_test.shape}  y={y_full.shape}")

    # XGB Heavy
    xgb_test, X_test_int = train_xgb_full(X_full, y_full, X_test, cat_features)

    # MLP — reuse X_test_int (already int-encoded)
    mlp_test = train_mlp_full(X_full, y_full, X_test_int, cat_features)

    log("\n=== GPU full training done ===")


if __name__ == '__main__':
    main()
