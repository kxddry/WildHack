# -*- coding: utf-8 -*-
"""Block 8c: Train MLP pyramid 5-seed on FULL team train, predict test.

Winner config from Block 3: v10_pyramid_h1536 (1536 -> 768 -> 384 -> 192)
  drop=0.2, activation='gelu', loss='l1_raw', lr=2e-3, epochs=40

Target encoding for cats (alpha=20 smoothing, computed on full train).
"""

import time, json, sys, gc, math
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

CACHE = Path('team_final_cache')
OUT = Path('team_final_out'); OUT.mkdir(exist_ok=True)
LOG = OUT / 'mlp_full_train.log'
DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

BS = 65536
SEEDS = [42, 123, 456, 789, 2024]

# Winner config from Block 3
CFG = dict(
    layers=(1536, 768, 384, 192),
    drop=0.2,
    activation='gelu',
    lr=2e-3,
    epochs=20,  # avg best_ep from fold CV ~ 15-25
)


class BigMLP(nn.Module):
    def __init__(self, d_in, layers=(1024, 512, 256), drop=0.2):
        super().__init__()
        blks = []
        prev = d_in
        for h in layers:
            blks += [nn.Linear(prev, h), nn.BatchNorm1d(h), nn.GELU(), nn.Dropout(drop)]
            prev = h
        blks.append(nn.Linear(prev, 1))
        self.net = nn.Sequential(*blks)
    def forward(self, x): return self.net(x).squeeze(-1)


def cosine_sched(opt, warmup, total):
    def fn(s):
        if s < warmup: return s / max(1, warmup)
        p = (s - warmup) / max(1, total - warmup)
        return max(0.05, 0.5 * (1 + math.cos(math.pi * p)))
    return torch.optim.lr_scheduler.LambdaLR(opt, fn)


def log(msg, fh=None):
    line = f"[B8C_MLP {datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if fh: fh.write(line + '\n'); fh.flush()


def te_encode(X_train, y_train, X_test, cat_cols, alpha=20):
    """Target encoding with alpha smoothing (same as exp_023)."""
    gm = y_train.mean()
    X_tr_nn = X_train.copy()
    X_te_nn = X_test.copy()
    mappings = {}
    for col in cat_cols:
        if col in X_tr_nn.columns:
            stats = pd.DataFrame({'cat': X_tr_nn[col], 'y': y_train})
            agg = stats.groupby('cat')['y'].agg(['mean', 'count'])
            agg['sm'] = (agg['count'] * agg['mean'] + alpha * gm) / (agg['count'] + alpha)
            m = agg['sm'].to_dict()
            mappings[col] = m
            X_tr_nn[col] = X_tr_nn[col].map(m).fillna(gm).astype(np.float32)
            X_te_nn[col] = X_te_nn[col].map(m).fillna(gm).astype(np.float32)
    return X_tr_nn, X_te_nn, mappings


def main():
    fh = open(LOG, 'w', encoding='utf-8')
    log("=== Block 8c: MLP pyramid 5-seed full-train ===", fh)
    log(f"CFG: {CFG}", fh)
    log(f"Device: {DEVICE}", fh)

    X_full = pd.read_parquet(CACHE / 'X_full.parquet')
    X_test = pd.read_parquet(CACHE / 'X_test.parquet')
    y_full = np.load(CACHE / 'y_full.npy').astype(np.float32)
    with open(CACHE / 'meta.json') as f:
        meta = json.load(f)
    cat_cols = meta['cat_cols']
    log(f"  X_full={X_full.shape}  X_test={X_test.shape}", fh)

    # Target encode cats
    log("Target encoding cats...", fh)
    X_tr_nn, X_te_nn, te_maps = te_encode(X_full, y_full, X_test, cat_cols, alpha=20)

    # Save TE mappings
    import joblib
    joblib.dump(te_maps, OUT / 'mlp_te_mappings.pkl')

    # Force float32
    for c in X_tr_nn.select_dtypes('float64').columns:
        X_tr_nn[c] = X_tr_nn[c].astype(np.float32)
        X_te_nn[c] = X_te_nn[c].astype(np.float32)

    X_tr_np = X_tr_nn.fillna(0).values.astype(np.float32)
    X_te_np = X_te_nn.fillna(0).values.astype(np.float32)
    del X_tr_nn, X_te_nn, X_full, X_test; gc.collect()

    mean = X_tr_np.mean(axis=0).astype(np.float32)
    std = X_tr_np.std(axis=0).astype(np.float32); std[std < 1e-8] = 1.0
    X_tr_s = np.nan_to_num((X_tr_np - mean) / std, nan=0, posinf=0, neginf=0).astype(np.float32)
    X_te_s = np.nan_to_num((X_te_np - mean) / std, nan=0, posinf=0, neginf=0).astype(np.float32)
    del X_tr_np, X_te_np; gc.collect()
    np.savez(OUT / 'mlp_full_scaler.npz', mean=mean, std=std)

    Xt = torch.from_numpy(X_tr_s).to(DEVICE)
    yt = torch.from_numpy(y_full).to(DEVICE)
    Xv = torch.from_numpy(X_te_s).to(DEVICE)
    del X_tr_s, X_te_s; gc.collect()
    log(f"  VRAM: {torch.cuda.memory_allocated()/1e9:.2f} GB", fh)

    d_in = Xt.shape[1]; n = Xt.shape[0]
    log(f"  d_in={d_in} n_train={n}", fh)

    seed_test_preds = []
    for sd in SEEDS:
        log(f"\n--- seed {sd} ---", fh)
        torch.manual_seed(sd); np.random.seed(sd)
        model = BigMLP(d_in, layers=tuple(CFG['layers']), drop=CFG['drop']).to(DEVICE)
        opt = torch.optim.AdamW(model.parameters(), lr=CFG['lr'], weight_decay=1e-4)
        sched = cosine_sched(opt, warmup=5 * max(1, n // BS), total=CFG['epochs'] * max(1, n // BS))
        crit = nn.L1Loss()

        t0 = time.time()
        for ep in range(CFG['epochs']):
            model.train()
            perm = torch.randperm(n, device=DEVICE)
            ep_loss = 0.0
            for i in range(0, n, BS):
                idx = perm[i:i + BS]
                pred = model(Xt[idx])
                loss = crit(pred, yt[idx])
                opt.zero_grad(set_to_none=True); loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step(); sched.step()
                ep_loss += float(loss.item()) * len(idx)
            if (ep + 1) % 5 == 0 or ep == 0:
                log(f"  ep {ep+1}: loss={ep_loss/n:.3f}", fh)

        model.eval()
        with torch.no_grad():
            test_pred = np.clip(model(Xv).cpu().numpy(), 0, None)
        log(f"  seed {sd} test mean={test_pred.mean():.2f}  t={time.time()-t0:.0f}s", fh)
        seed_test_preds.append(test_pred)

        torch.save({'state': model.state_dict(), 'mean': mean, 'std': std,
                    'layers': CFG['layers'], 'seed': sd},
                   OUT / f'mlp_pyramid_full_seed{sd}.pt')
        del model, opt, sched; gc.collect(); torch.cuda.empty_cache()

    mlp_test = np.mean(seed_test_preds, axis=0)
    log(f"\nMLP 5-seed ensemble test mean: {mlp_test.mean():.2f}", fh)
    np.save(OUT / 'mlp_pyramid_full_test_pred.npy', mlp_test)
    np.save(OUT / 'mlp_pyramid_full_test_preds_per_seed.npy', np.stack(seed_test_preds, axis=0))

    del Xt, yt, Xv; gc.collect(); torch.cuda.empty_cache()
    log("DONE", fh)
    fh.close()


if __name__ == '__main__':
    main()
