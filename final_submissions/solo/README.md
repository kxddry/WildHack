# WB Space — Solo Track Final Solution

## Стек моделей

3-моделный Ridge stack из:

1. **LightGBM (Poisson loss)** — обучен на полном train с `n_estimators=3700` (среднее по 3-fold OOF best_iter × 1.05).
2. **XGBoost Heavy (Poisson, GPU)** — `max_depth=14, max_leaves=511, lr=0.015, max_bin=512`, n_round=4000.
3. **ResMLP × 5 seeds** (`hidden=256, n_blocks=5, drop=0.2`, Poisson NLL loss с `head.bias = log(mean(y))` инициализацией). Усреднение 5 сидов: `[42, 123, 456, 789, 2024]`.

**Stack meta:** `Ridge(alpha=1.0)` обучен на out-of-fold предсказаниях из 3-fold CV.
- coefs: `LGB=0.6372, MLP=0.5095, XGB=-0.1489`
- intercept: `274.34`
- OOF score (in-sample): **0.335679**

## Структура

```
final_submissions/solo/
├── README.md
├── 01_build_features.py        — построение train+test features (Kaggle features + DatasetBuilder)
├── 02_train_lgb.py             — обучение LightGBM Poisson на FULL train (CPU)
├── 03_train_gpu.py             — обучение XGB Heavy + 5-seed ResMLP на FULL train (GPU)
├── 04_submit.py                — Ridge stacking + round + сохранение CSV
├── models/
│   ├── lgb_poisson_full.pkl                   — LGB модель (66 MB)
│   ├── xgb_heavy_poisson_full.json            — XGB модель (249 MB)
│   ├── mlp_full_seed{42,123,456,789,2024}.pt  — 5 ResMLP моделей (3 MB каждая)
│   ├── mlp_full_stats.npz                     — mean/std для MLP
│   ├── final_test_preds.npz                   — индивидуальные test predictions всех моделей
│   └── final_submission_meta.json             — Ridge коэффициенты + OOF score
├── submission_x1.01.csv        — финальный, scale=1.01
├── submission_x1.02.csv        — финальный, scale=1.02
└── submission_x1.025.csv       — финальный, scale=1.025
```

## Pipeline

```
[01] build_features.py
        ├─ Загружает Data/raw/{train,test}_solo_track.parquet
        ├─ Добавляет Kaggle features (same-slot lags, momentum, nonzero rate, smoothed TE)
        ├─ Через core.data.DatasetBuilder строит X_full + X_test
        └─ Сохраняет в full_cache/

[02] train_lgb.py            (CPU, ~25 мин)
        ├─ Загружает full_cache/X_full.parquet + y_full.npy
        ├─ Обучает LightGBM с objective='poisson', n_estimators=3700
        └─ Сохраняет models/lgb_poisson_full.pkl + test predictions

[03] train_gpu.py            (GPU, ~16 мин)
        ├─ XGB Heavy Poisson GPU (n_round=4000)
        ├─ 5 ResMLP с разными сидами × 15 epochs
        └─ Сохраняет XGB.json + 5 mlp.pt + scaler

[04] submit.py
        ├─ Загружает test predictions всех 3 модальностей
        ├─ Обучает Ridge meta на solo OOF (LGB Poisson + MLP MSeed + XGB Heavy)
        ├─ Применяет meta к test → стэк
        ├─ np.round() → int64
        └─ Сохраняет submission CSV
```

## Гиперпараметры

### LightGBM Poisson
```python
dict(
    objective='poisson', boosting_type='gbdt',
    n_estimators=3700,
    learning_rate=0.025, num_leaves=127, max_depth=9,
    min_child_samples=60, subsample=0.85, subsample_freq=1,
    colsample_bytree=0.85,
    reg_alpha=0.2, reg_lambda=5.0,
    random_state=42, n_jobs=-1, verbosity=-1,
)
```

### XGBoost Heavy Poisson GPU
```python
{
    'objective': 'count:poisson',
    'device': 'cuda', 'tree_method': 'hist',
    'max_depth': 14, 'max_leaves': 511,
    'learning_rate': 0.015,
    'subsample': 0.9, 'colsample_bytree': 0.9, 'colsample_bynode': 0.9,
    'min_child_weight': 40, 'max_bin': 512,
    'reg_alpha': 0.3, 'reg_lambda': 3.0,
    'seed': 42,
}
# num_boost_round=4000, no early stop on full train
```

### ResMLP (5 seeds)
- Архитектура: `Linear(d_in, 256) → BN → GELU → 5 × ResBlock(256, drop=0.2) → Linear(256, 1)`
- ResBlock: `Linear(256,256) → BN → GELU → Dropout → Linear(256,256) → BN`, residual + GELU
- Loss: `PoissonNLLLoss(log_input=True)`
- Init: `head.bias = log(mean(y_train))`, `head.weight = 0`
- Optimizer: `AdamW(lr=1e-3, wd=1e-4)`
- Scheduler: `CosineAnnealingWarmRestarts(T_0=30, T_mult=2, eta_min=1e-5)`
- Batch size: `16000`
- Epochs: `15`
- Categorical: int code encoding (NOT category dtype)
- Standardization on train

### Ridge stacking
- `Ridge(alpha=1.0)` обучен на полном OOF (3 fold × 8000 = 24000 точек)
- Features: `[lgb_poisson_oof, mlp_mseed_oof, xgb_heavy_poisson_oof]`
- Coefs: `[0.6372, 0.5095, -0.1489]`, intercept `274.34`

## Ключевые insights

1. **Poisson loss > MAE** для solo (target_1h, mean ~266715 — большие counts)
2. **Multi-seed averaging для MLP** — снизил variance ~-0.005
3. **XGB heavy получает отрицательный coef в Ridge** — модель сильно скоррелирована с LGB и используется как корректор
4. **Round to int** не влияет на метрику для больших target значений (разница в 8-м знаке)

## Запуск

```bash
# Запускать из experiments/ репо (зависит от core/data.py, core/metric.py)
python 01_build_features.py
python 02_train_lgb.py        # ~25 min CPU
python 03_train_gpu.py        # ~16 min GPU
python 04_submit.py           # ~30 sec
```

## Submission результаты

- `submission_x1.01.csv`:  scale=1.010, mean=275036, sum=2200M
- `submission_x1.02.csv`:  scale=1.020, mean=277759, sum=2222M
- `submission_x1.025.csv`: scale=1.025, mean=279120, sum=2233M

Базовый OOF stack (scale=1.0): **0.335679**
