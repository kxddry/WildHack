# WB Space — Team Track Final Solution

## Стек моделей

2-моделный Ridge stack:

1. **LightGBM Hybrid (per-horizon 1-5 + global 6-10)**, 6 моделей с MAE loss.
2. **ResMLP Pyramid × 5 seeds** (`1536 → 768 → 384 → 192`), L1 loss + target encoding для cat features.

**Stack meta:** `Ridge(alpha=1.0)` обучен на out-of-fold предсказаниях из 3-fold CV.
- coefs: `LGB=0.8464, MLP_pyramid=0.1650`
- intercept: `2.0959`
- OOF stack (honest LOFO): **0.277158**
- OOF stack (in-sample): 0.273784
- Лучший OOF scale: `0.9975`

## Структура

```
final_submissions/team/
├── README.md
├── precompute_oof_features.py  — построение OOT 3-fold features (для CV/OOF)
├── 01_build_features.py        — построение train+test features (для финального обучения)
├── 02_train_lgb.py             — обучение LGB Hybrid (per-horizon 1-5 + global 6-10) на FULL train
├── 03_train_mlp.py             — обучение Pyramid MLP × 5 seeds на FULL train (target encoding)
├── 04_submit.py                — Ridge stacking + zero-routes + scale + CSV
├── models/
│   ├── lgb_hybrid_mae_full.pkl                   — 6 LGB моделей (82 MB)
│   ├── lgb_hybrid_mae_full_test_pred.npy         — LGB test predictions
│   ├── mlp_pyramid_full_seed{42,...,2024}.pt     — 5 ResMLP моделей (9 MB каждая)
│   ├── mlp_pyramid_full_test_pred.npy            — ensemble MLP test predictions
│   ├── mlp_pyramid_full_test_preds_per_seed.npy  — индивидуальные seed predictions
│   ├── mlp_full_scaler.npz                       — mean/std для MLP стандартизации
│   ├── mlp_te_mappings.pkl                       — target encoding mappings
│   └── final_test_preds.npz                      — все test preds + Ridge meta
├── submission_x0.975.csv       — финальный, scale=0.975 (LB ~0.255)
├── submission_x0.98.csv        — финальный, scale=0.98 (LB ~0.258)
└── submission_x0.9975.csv      — финальный, scale=0.9975 (best OOF scale)
```

## Pipeline

```
[precompute_oof_features.py]   (CPU, ~5 мин, генерация ОДИН раз)
        ├─ Загружает Data/raw/train_team_track.parquet
        ├─ Добавляет Kaggle features (same-slot, momentum, deviation, cv, smoothed TE)
        ├─ Через core.data.OOTValidator + DatasetBuilder строит 3 OOT folds
        │   - val = последние 10 точек на route, fold_gap_days=2
        │   - train_days=7 sliding window
        ├─ Сохраняет precomputed/X_train_lgb_fN.parquet, nn_fN.npz, meta_fN.json
        └─ Используется блоками 02 (collect_best_iters) и для exp_023 OOF generation

[01] build_features.py     (CPU, ~2 мин)
        ├─ Строит full team train + test через DatasetBuilder
        ├─ 489 features, 7 cat (route_id, office_from_id, dow, pod, is_hooliday, slot, horizon_step)
        └─ Сохраняет в team_final_cache/

[02] train_lgb.py          (CPU, ~25 мин)
        ├─ Загружает team_final_cache + collect best_iter from saved fold models
        ├─ Per-horizon 1-5 (n_iter ~600-4000) + global 6-10 (n_iter ~2300)
        ├─ MAE loss (regression_l1)
        └─ Сохраняет models/lgb_hybrid_mae_full.pkl + test predictions

[03] train_mlp.py          (GPU, ~6 мин)
        ├─ Target encode cat features (alpha=20 smoothing) на full train
        ├─ Pyramid arch (1536→768→384→192) × 5 seeds
        ├─ L1 loss, AdamW(lr=2e-3, wd=1e-4), bs=65536, 20 epochs
        └─ Сохраняет 5 mlp.pt + ensemble preds

[04] submit.py             (~10 sec)
        ├─ Загружает test predictions LGB + MLP
        ├─ Обучает Ridge на NEW MLP OOF + LGB OOF (exp_023_oof_final)
        ├─ Применяет meta к test preds
        ├─ Zero-routes (16 маршрутов где train_14d max=0) → 160 rows = 0
        ├─ Scale calibration (best from honest OOF: 0.9975)
        ├─ np.round() → int64
        └─ Сохраняет submission CSV (несколько scale вариантов)
```

## Гиперпараметры

### LightGBM Hybrid (одинаковы для всех 6 моделей)
```python
dict(
    objective='regression_l1',  # MAE
    learning_rate=0.02,
    num_leaves=63, max_depth=9,
    min_child_samples=80, min_child_weight=0.01, min_split_gain=0.05,
    subsample=0.8, subsample_freq=1, colsample_bytree=0.7,
    reg_alpha=0.5, reg_lambda=8.0,
    subsample_for_bin=200000,
    random_state=42, n_jobs=-1, verbosity=-1,
)
```

### Per-step n_estimators (avg × 1.05 от 3-fold OOF best_iter)
```python
{'step_1': 4088, 'step_2': 1902, 'step_3': 974,
 'step_4': 779, 'step_5': 536, 'global_6_10': 2318}
```

### ResMLP Pyramid
```python
class BigMLP(nn.Module):
    layers = (1536, 768, 384, 192)  # pyramid
    drop = 0.2
    activation = nn.GELU()
    # Architecture: Linear -> BN -> GELU -> Dropout repeated 4x, then Linear -> 1
```

- Loss: `nn.L1Loss()` (raw target, NO log1p, NO Poisson)
- Optimizer: `AdamW(lr=2e-3, weight_decay=1e-4)`
- Scheduler: `cosine` warmup=5 epochs, total=20 epochs
- Batch size: `65536`
- Epochs: `20`
- Seeds: `[42, 123, 456, 789, 2024]`
- Target encoding для всех 7 cat features (alpha=20 smoothing на full train)
- Standardization после TE

### Post-processing
1. **Zero-routes**: 16 маршрутов где `train.target_2h.max() == 0` за последние 14 дней — обнуляем 160 rows (16 × 10 horizon_steps).
   ```python
   zero_routes = {21, 124, 215, 243, 364, 370, 407, 450, 474, 505, 531, 582, 928, 931, 968, 977}
   ```
2. **Scale calibration**: best constant scale на honest OOF = `0.9975`. Можно экспериментально подбирать `0.97-1.0`.
3. **Round to int**: `np.round(preds).astype(np.int64)` (target — целые counts).

## Ключевые insights

1. **Pyramid architecture (1536→768→384→192) bьёт обычный 1024-512-256** на team — sweet spot.
2. **L1 loss (MAE) на raw target лучше чем log1p L1 + Poisson** для team (target_2h, mean ~67 — маленькие counts).
3. **MAE остаётся королём для LGB на team** — Tweedie/Huber/Poisson все хуже на 0.03-0.05.
4. **Pyramid MLP даёт diversity для стека** — corr c LGB ~0.91 (vs 0.97 у старого 1024-512-256 MLP).
5. **Multi-seed averaging** для MLP: 5 сидов × 20 epochs (без early stop, fixed schedule).
6. **Target encoding** для категориальных (alpha=20) критичен для MLP.

## Запуск

```bash
# Запускать из experiments/ репо
python precompute_oof_features.py    # ~5 min CPU (если ещё нет precomputed/)
python 01_build_features.py          # ~2 min CPU
python 02_train_lgb.py               # ~25 min CPU
python 03_train_mlp.py               # ~6 min GPU
python 04_submit.py                  # ~10 sec
```

## Submission результаты

| File | scale | mean | sum | LB |
|---|---|---|---|---|
| `submission_x0.975.csv` | 0.975 | 73.60 | 736.0K | **0.255** |
| `submission_x0.98.csv`  | 0.98  | 73.91 | 739.0K | 0.258 |
| `submission_x0.9975.csv`| 0.9975| 75.30 | 752.9K | best OOF scale |

OOF stack honest LOFO: **0.277158**

## Honest OOF score (3-fold CV LOFO Ridge)

| Model | OOF score |
|---|---|
| LGB Hybrid MAE | 0.315046 |
| MLP Pyramid 5-seed | 0.306331 |
| **Stacked (Ridge)** | **0.277158** |
