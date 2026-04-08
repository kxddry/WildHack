# Production Team Model

Drop-in модель для `services/prediction-service` обученная локально на kaggle data.

## Зачем это нужно

`services/retraining-service` обычно сам обучает модель, но требует:
- Поднятый Postgres
- Заполненный `route_status_history` (≥1000 строк за 30 дней)
- CPU мощность для обучения

Если у разработчика сервиса нет всего этого — можно использовать модель отсюда. Она обучена тем же pipeline что и `retraining-service`, поэтому 1-в-1 совместима с `prediction-service` `InferenceFeatureEngine`.

## Что внутри

```
production_team_model/
├── README.md
├── train_production_model.py        — скрипт обучения (standalone, без зависимости от postgres)
├── train.log                         — лог последнего запуска
└── models/
    ├── model.pkl                     — LightGBM Booster (canonical name) [LFS]
    ├── v20260408_051606.pkl          — версионная копия [LFS]
    ├── v20260408_051606_metadata.json — метрики, n_iter, hyperparams
    ├── static_aggs.json              — 5 агрегационных таблиц для InferenceFeatureEngine [LFS]
    └── fill_values.json              — 307 медианных fill values
```

## Метрики (out-of-time validation, последние 10 timestamps)

```
WAPE           = 0.0081
RBias          = 0.0002
combined_score = 0.0083  ← gradient pushing < 0.01 — отличный production score
best_iter      = 5000
train_rows     = 1,431,000
val_rows       = 10,000
features       = 312
```

## Конфигурация обучения

```python
LightGBM(
    objective='regression_l1',  # MAE
    metric='mae',
    learning_rate=0.025,
    num_leaves=63,
    max_depth=9,
    min_child_samples=80,
    subsample=0.8,
    colsample_bytree=0.75,
    reg_alpha=0.5,
    reg_lambda=8.0,
    n_estimators=5000,
    early_stopping_rounds=100,
)
```

Параметры идентичны `services/retraining-service/app/config.py`.

## Pipeline

1. **fetch_training_data**: загрузка `Data/raw/train_team_track.parquet`, slice последних 30 дней (`training_window_days=30`).
2. **build_features**: тот же набор фич что в `services/retraining-service/app/core/trainer.py::ModelTrainer.build_features`:
   - time features (`dow, pod, slot, is_hooliday`)
   - status aggregations (`total/early/mid/late_inventory, *_share, status_entropy`)
   - target lag/diff/rolling (1..10, +15+20+48+96, windows 3..288)
   - inventory lag/diff/rolling
   - status_1..8 detailed lags
3. **train_model**: OOT split (последние 10 timestamps как val), LightGBM с MAE loss, early stopping.
4. **save_model**: pickle to `model.pkl`, metadata json, static_aggs json, fill_values json.

## Как использовать в prediction-service

```bash
# Скопировать в running контейнер
docker compose cp final_submissions/production_team_model/models/model.pkl \
    prediction-service:/app/models/model.pkl
docker compose cp final_submissions/production_team_model/models/static_aggs.json \
    prediction-service:/app/models/static_aggs.json
docker compose cp final_submissions/production_team_model/models/fill_values.json \
    prediction-service:/app/models/fill_values.json

# Перезагрузить модель в сервисе
curl -X POST http://localhost:8001/model/reload
```

Или просто положить файлы в volume / image для prediction-service и перезапустить контейнер.

## Воспроизведение обучения

```bash
# Из корня репозитория, без поднятого postgres
python final_submissions/production_team_model/train_production_model.py
```

Зависимости: `pandas`, `numpy`, `lightgbm`. Время: ~12-15 минут CPU.

## Совместимость

- **Модель**: pickle `lgb.Booster` — загружается через `pickle.load()` (тот же pattern что `prediction-service` `ModelManager.load`).
- **Schema features**: 312 numeric + категориальные columns после `_set_cat_dtypes`.
- **InferenceFeatureEngine**: 1-в-1 (pipeline скопирован с `services/retraining-service/app/core/trainer.py`).
