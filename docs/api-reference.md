# Справочник API

Все четыре FastAPI-сервиса предоставляют автоматическую OpenAPI-документацию (Swagger UI):

| Сервис | Swagger | Порт |
|--------|---------|------|
| prediction-service | http://localhost:8000/docs | 8000 |
| dispatcher-service | http://localhost:8001/docs | 8001 |
| scheduler-service | http://localhost:8002/docs | 8002 |
| retraining-service | http://localhost:8003/docs | 8003 |

> **Versioned API.** prediction-service и dispatcher-service монтируют тот же роутер дополнительно под префиксом `/api/v1/...` (PRD §6) — для обратной совместимости легаси-пути остаются работать.

---

## Prediction Service (порт 8000)

### POST /predict

Прогноз отгрузок для одного маршрута на 10 шагов вперёд (5 часов).

**Запрос:**

```json
{
    "route_id": 101,
    "timestamp": "2026-04-02T12:00:00",
    "status_1": 15.0,
    "status_2": 23.0,
    "status_3": 8.0,
    "status_4": 42.0,
    "status_5": 31.0,
    "status_6": 19.0,
    "status_7": 5.0,
    "status_8": 2.0
}
```

| Поле | Тип | Обязательное | Описание |
|------|-----|:---:|----------|
| route_id | int | да | Идентификатор маршрута |
| timestamp | datetime | да | Момент наблюдения (ISO 8601) |
| status_1..8 | float | да | Кол-во товаров на каждом из 8 этапов обработки за последние 30 минут |

**Ответ (200):**

```json
{
    "route_id": 101,
    "warehouse_id": 42,
    "anchor_timestamp": "2026-04-02T12:00:00",
    "forecasts": [
        { "horizon_step": 1, "timestamp": "2026-04-02T12:30:00", "predicted_value": 12.3456 },
        { "horizon_step": 2, "timestamp": "2026-04-02T13:00:00", "predicted_value": 14.7891 }
    ],
    "model_version": "v1",
    "shadow_forecasts": [
        { "horizon_step": 1, "timestamp": "2026-04-02T12:30:00", "predicted_value": 12.0987 }
    ]
}
```

| Поле | Тип | Описание |
|------|-----|----------|
| route_id | int | Идентификатор маршрута |
| warehouse_id | int | Резолвится из истории маршрута или таблицы `routes` |
| anchor_timestamp | datetime | Опорный момент прогноза (`request.timestamp`) |
| forecasts | list[ForecastStep] | 10 шагов primary-модели |
| forecasts[].horizon_step | int | Номер шага (1–10) |
| forecasts[].timestamp | datetime | Время точки прогноза |
| forecasts[].predicted_value | float | Прогнозируемый `target_2h` (ёмкости), округлено до 4 знаков |
| model_version | string | Версия primary-модели |
| shadow_forecasts | list[ForecastStep] \| null | Прогноз shadow-модели для A/B (только если shadow загружена) |

---

### POST /predict/batch

Параллельный пакетный прогноз для нескольких маршрутов. Concurrency ограничена `asyncio.Semaphore(10)`. Если прогноз для одного маршрута падает с ошибкой, остальные обрабатываются (fail-safe).

**Запрос:**

```json
{
    "predictions": [
        {
            "route_id": 101,
            "timestamp": "2026-04-02T12:00:00",
            "status_1": 15.0, "status_2": 23.0, "status_3": 8.0, "status_4": 42.0,
            "status_5": 31.0, "status_6": 19.0, "status_7": 5.0, "status_8": 2.0
        },
        {
            "route_id": 102,
            "timestamp": "2026-04-02T12:00:00",
            "status_1": 10.0, "status_2": 18.0, "status_3": 5.0, "status_4": 30.0,
            "status_5": 22.0, "status_6": 14.0, "status_7": 3.0, "status_8": 1.0
        }
    ]
}
```

**Ответ (200):**

```json
{
    "results": [
        { "route_id": 101, "warehouse_id": 42, "anchor_timestamp": "2026-04-02T12:00:00", "forecasts": [], "model_version": "v1" },
        { "route_id": 102, "warehouse_id": 42, "anchor_timestamp": "2026-04-02T12:00:00", "forecasts": [], "model_version": "v1" }
    ],
    "total": 2,
    "processing_time_ms": 145.23
}
```

`total` — количество **успешных** прогнозов; падения отдельных маршрутов логируются и пропускаются.

---

### GET /model/info

Метаданные загруженной primary-модели.

**Ответ (200):**

```json
{
    "model_version": "v1",
    "model_type": "LGBMRegressor",
    "objective": "regression_l1",
    "cv_score": 0.292,
    "feature_count": 156,
    "training_date": "2026-04-01",
    "forecast_horizon": 10,
    "step_interval_minutes": 30
}
```

---

### POST /model/reload

Hot-reload primary-модели с диска без рестарта сервиса. Используется retraining-service после промоута.

**Ответ (200):**

```json
{
    "status": "reloaded",
    "details": { "version": "v20260408_120000", "feature_count": 156 }
}
```

---

### POST /model/reload-features

Перечитать `static_aggs.json` и `fill_values.json`. Дёргается retraining-service после нового тренинга, чтобы inference-фичи синхронизировались с обновлёнными статистиками.

**Ответ (200):**

```json
{
    "status": "reloaded",
    "reloaded": ["static_aggs", "fill_values"],
    "errors": []
}
```

Если ровно один файл не удалось перечитать, в `errors` будет описание; статус `reloaded` всё равно вернётся. Если оба упали — 500.

---

### POST /model/shadow/load

Загрузить shadow-модель для A/B сравнения.

**Query:**

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|--------------|----------|
| `path` | string | `models/shadow_model.pkl` | Путь к файлу. Должен находиться внутри каталога `MODEL_PATH` (валидируется) |

**Ответ (200):**

```json
{ "status": "shadow_loaded", "path": "models/v20260408_120000.pkl" }
```

---

### POST /model/shadow/promote

Промоутить shadow → primary. Бывшая primary становится недоступной.

**Ответ (200):**

```json
{ "status": "promoted", "new_primary_path": "models/v20260408_120000.pkl" }
```

**404:** если shadow не загружена.

---

### DELETE /model/shadow

Снять shadow-модель. Primary не затрагивается.

**Ответ (200):**

```json
{ "status": "shadow_removed" }
```

---

### GET /health

Проверка здоровья prediction-service.

**Ответ (200):**

```json
{
    "status": "healthy",
    "model_loaded": true,
    "database_connected": true,
    "uptime_seconds": 3421.56
}
```

| Поле | Значения |
|------|----------|
| status | `"healthy"` (real model + DB) / `"mock"` (синтетический fallback при `MOCK_MODE=1`) / `"degraded"` (модель или БД недоступны) |
| model_loaded | bool |
| database_connected | bool |
| uptime_seconds | float |

---

## Dispatcher Service (порт 8001)

### POST /dispatch

Расчёт необходимого транспорта для склада. Два режима:

1. **Прямая передача прогнозов** — массив `forecasts` в теле
2. **Выборка из БД** — `time_range_start` + `time_range_end`

**Запрос (с прогнозами):**

```json
{
    "warehouse_id": 42,
    "forecasts": [
        { "timestamp": "2026-04-02T14:00:00", "total_containers": 95.7 },
        { "timestamp": "2026-04-02T16:00:00", "total_containers": 72.3 }
    ]
}
```

**Запрос (из БД):**

```json
{
    "warehouse_id": 42,
    "time_range_start": "2026-04-02T12:00:00",
    "time_range_end": "2026-04-02T17:00:00"
}
```

**Ответ (200):**

```json
{
    "warehouse_id": 42,
    "dispatch_requests": [
        {
            "time_slot_start": "2026-04-02T14:00:00",
            "time_slot_end": "2026-04-02T14:00:00",
            "total_containers": 95.7,
            "truck_capacity": 33,
            "buffer_pct": 0.10,
            "trucks_needed": 4,
            "calculation": "ceil(95.7 * (1 + 0.1) / 33) = ceil(105.2700 / 33) = 4"
        }
    ],
    "config": { "truck_capacity": 33, "buffer_pct": 0.10, "min_trucks": 1 }
}
```

`dispatch_requests` сохраняются в `transport_requests` через UPSERT по `(warehouse_id, time_slot_start, time_slot_end)`.

**Ошибки:** 404 — нет прогнозов в указанном диапазоне; 422 — не указаны ни `forecasts`, ни оба `time_range_*`.

---

### GET /dispatch/schedule

Сохранённое расписание заявок для склада.

| Параметр | Тип | Обязательное | Описание |
|----------|-----|:---:|----------|
| warehouse_id | int (query) | да | Идентификатор склада |

**Ответ (200):**

```json
{
    "warehouse_id": 42,
    "schedule": [
        {
            "time_slot_start": "2026-04-02T14:00:00",
            "time_slot_end": "2026-04-02T16:00:00",
            "trucks_needed": 4,
            "total_containers": 95.7,
            "status": "planned"
        }
    ]
}
```

---

### GET /warehouses

Список всех складов с агрегатами.

**Ответ (200):**

```json
{
    "warehouses": [
        {
            "warehouse_id": 42,
            "route_count": 15,
            "latest_forecast_at": "2026-04-02T12:30:00",
            "upcoming_trucks": 7
        }
    ],
    "total": 1
}
```

---

### GET /api/v1/transport-requests

PRD §6.2 — заявки склада в окне `[from, to]`.

| Параметр | Тип | Обязательное | Описание |
|----------|-----|:---:|----------|
| office_id | int (query) | да | ID склада (≥0) |
| from | datetime (query) | да | Начало окна (ISO 8601) |
| to | datetime (query) | да | Конец окна (строго `> from`) |

**Пример:** `GET /api/v1/transport-requests?office_id=42&from=2026-04-02T12:00:00&to=2026-04-02T18:00:00`

**Ответ (200):**

```json
{
    "items": [
        {
            "id": 101,
            "office_from_id": 42,
            "time_window_start": "2026-04-02T14:00:00",
            "time_window_end": "2026-04-02T16:00:00",
            "routes": [101, 102, 103],
            "total_predicted_units": 95.7,
            "vehicles_required": 4,
            "status": "planned",
            "created_at": "2026-04-02T12:35:12"
        }
    ],
    "total": 1,
    "office_id": 42,
    "range_from": "2026-04-02T12:00:00",
    "range_to": "2026-04-02T18:00:00"
}
```

**Ошибки:** 422 — `from >= to`.

---

### GET /api/v1/metrics/business

PRD §9.2 — два бизнес-KPI, считаются только по слотам с заполненными `actual_vehicles` / `actual_units`.

| Параметр | Тип | Обязательное | Описание |
|----------|-----|:---:|----------|
| from | datetime (query) | нет | Начало окна |
| to | datetime (query) | нет | Конец окна |

Если оба отсутствуют — окно не ограничено. Если указан только один — 422.

**Ответ (200):**

```json
{
    "order_accuracy": 0.84,
    "avg_truck_utilization": 0.71,
    "n_slots_evaluated": 142,
    "n_slots_total": 168,
    "truck_capacity": 33,
    "range_from": "2026-04-01T00:00:00",
    "range_to": "2026-04-08T00:00:00",
    "note": null
}
```

| Поле | Описание |
|------|----------|
| order_accuracy | Доля слотов, где `|predicted_vehicles - actual_vehicles| ≤ 1` (включая угловой случай `actual=0, predicted=1` — пустой рейс считается «достаточно точным», его стоимость учитывается отдельно через утилизацию) |
| avg_truck_utilization | Среднее `actual_units / (vehicles * capacity)` по реально отгруженным слотам |
| n_slots_evaluated | Сколько слотов имеют фактические значения |
| n_slots_total | Сколько всего слотов в окне |
| truck_capacity | Текущая `TRUCK_CAPACITY` |
| note | Если `n_slots_evaluated == 0`: `"No slots have actual fulfilment data yet — KPIs will populate once transport_requests.actual_vehicles is backfilled."` |

---

### GET /health

```json
{ "status": "healthy", "database_connected": true, "uptime_seconds": 3421.56 }
```

---

## Scheduler Service (порт 8002)

Внутри Scheduler работают три APScheduler-задачи (`AsyncIOScheduler`):

| Job ID | Интервал | Что делает |
|--------|----------|-----------|
| `prediction_cycle` | `PREDICTION_INTERVAL_MINUTES` (30) | predict + dispatch цикл |
| `quality_check` | `QUALITY_CHECK_INTERVAL_MINUTES` (60) | WAPE/RBias + автопромоут shadow по streak |
| `backfill_target_2h` | 30 | Дописывает фактические значения в `route_status_history` и `transport_requests.actual_*` |

### GET /pipeline/status

```json
{
    "pipeline": {
        "last_run_at": "2026-04-02T12:30:00",
        "last_run_status": "success",
        "routes_processed": 73,
        "transport_requests_created": 12
    },
    "quality": {
        "last_check_at": "2026-04-02T12:00:00",
        "wape": 0.21,
        "rbias": 0.04,
        "combined_score": 0.25,
        "shadow_streak": 1
    }
}
```

Точная форма зависит от `PipelineOrchestrator.status` / `QualityChecker.status` — это снапшот in-memory состояния.

---

### POST /pipeline/trigger

Внеочередной запуск `prediction_cycle`.

**Ответ (200):** результат прогона (зависит от орчестратора), пример:

```json
{
    "status": "success",
    "started_at": "2026-04-02T12:35:00",
    "finished_at": "2026-04-02T12:35:08",
    "routes_processed": 73,
    "transport_requests_created": 12
}
```

---

### GET /pipeline/history

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|--------------|----------|
| limit | int (query) | 20 | Количество записей |

**Ответ (200):**

```json
{
    "runs": [
        {
            "id": 1024,
            "run_type": "prediction_cycle",
            "status": "success",
            "started_at": "2026-04-02T12:30:00",
            "completed_at": "2026-04-02T12:30:09",
            "details": { "routes_processed": 73 }
        }
    ],
    "total": 1
}
```

Источник — таблица `pipeline_runs`.

---

### POST /quality/trigger

Внеочередной расчёт качества (WAPE + RBias).

**Ответ (200):**

```json
{
    "metrics": {
        "checked_at": "2026-04-02T13:00:00",
        "wape": 0.21,
        "rbias": 0.04,
        "combined_score": 0.25,
        "n_pairs": 1234,
        "alert_triggered": false
    }
}
```

Метрики персистятся в `prediction_quality`.

---

### GET /quality/alerts

Активные алерты по дрейфу качества + последние посчитанные метрики.

```json
{
    "alerts": [],
    "last_metrics": {
        "checked_at": "2026-04-02T13:00:00",
        "wape": 0.21,
        "rbias": 0.04,
        "combined_score": 0.25
    }
}
```

---

### GET /health

```json
{ "status": "healthy", "database_connected": true, "uptime_seconds": 3421.56 }
```

---

## Retraining Service (порт 8003)

### POST /retrain

Полный цикл переобучения. Защищён `asyncio.Lock` — параллельный запуск возвращает 409.

**Конвейер:**

1. `fetch_training_data(TRAINING_WINDOW_DAYS)` — сырые данные из `route_status_history`
2. `build_features` (тред-пул)
3. `train_model` — LightGBM (тред-пул)
4. `save_model` → `models/<version>.pkl`
5. `save_static_aggs` → пересчёт `static_aggs.json` / `fill_values.json`
6. Сравнение с champion (`compare_champion_challenger`)
7. `register_model` → запись в `model_metadata`
8. Если challenger лучше — `POST prediction-service/model/shadow/load`
9. Запись в `retrain_history`

**Ответ (200):**

```json
{
    "version": "v20260408_120000",
    "model_path": "/app/models/v20260408_120000.pkl",
    "metrics": {
        "wape": 0.20,
        "rbias": 0.03,
        "combined_score": 0.23,
        "feature_count": 156,
        "best_iteration": 1247,
        "train_rows": 50432
    },
    "is_better_than_champion": true,
    "promotion_status": "shadow_loaded",
    "started_at": "2026-04-08T12:00:00",
    "finished_at": "2026-04-08T12:05:34",
    "status": "success"
}
```

**Значения `promotion_status`:** `shadow_loaded` / `skipped` (challenger хуже champion) / `promotion_failed` (HTTP к prediction упал).

**Ошибки:** 409 — retrain уже идёт; 422 — проблема с данными (`ValueError`); 500 — другая ошибка пайплайна.

---

### GET /retrain/status

Результат **последнего** запуска (in-memory, обнуляется при рестарте):

```json
{
    "version": "v20260408_120000",
    "status": "success",
    "promotion_status": "shadow_loaded",
    "started_at": "2026-04-08T12:00:00",
    "finished_at": "2026-04-08T12:05:34"
}
```

---

### GET /models

Все версии из `model_metadata`, отсортированные по времени создания.

```json
[
    {
        "model_version": "v20260408_120000",
        "model_path": "/app/models/v20260408_120000.pkl",
        "cv_score": 0.23,
        "feature_count": 156,
        "training_date": "2026-04-08T12:05:34",
        "config_json": { "training_window_days": 7 }
    }
]
```

---

### GET /models/champion

Текущий champion (наименьший `cv_score`).

**Ответ (200):**

```json
{
    "model_version": "v20260401_080000",
    "model_path": "/app/models/v20260401_080000.pkl",
    "cv_score": 0.292,
    "feature_count": 156
}
```

**404** — champion ещё не зарегистрирован.

---

### POST /models/{version}/shadow

Загрузить указанную версию как shadow в prediction-service.

**Ответ (200):**

```json
{
    "version": "v20260408_120000",
    "model_path": "/app/models/v20260408_120000.pkl",
    "result": { "status": "shadow_loaded" }
}
```

**404** — версия не найдена.

---

### POST /models/{version}/promote

Промоутить версию в primary (через shadow → promote).

**Ответ (200):**

```json
{
    "version": "v20260408_120000",
    "model_path": "/app/models/v20260408_120000.pkl",
    "result": { "status": "promoted" }
}
```

---

### GET /health

```json
{ "status": "healthy", "database_connected": true, "uptime_seconds": 3421.56 }
```

---

## Коды ошибок (общие)

| Код | Ситуация | Пример |
|-----|----------|--------|
| 200 | Успех | Прогноз выполнен |
| 400 | Bad request | shadow path вне `MODEL_PATH` |
| 404 | Не найдено | Нет прогнозов в окне; champion не зарегистрирован |
| 409 | Conflict | Retrain уже идёт |
| 422 | Validation | Неверный формат запроса; `from >= to`; нет ни `forecasts`, ни `time_range_*` |
| 500 | Внутренняя ошибка | Сбой prediction pipeline; retrain failed |
| 503 | Сервис не готов | Модель не загружена |

**Формат ошибки FastAPI:**

```json
{ "detail": "No forecasts found for warehouse 42 in the given time range" }
```

---

## Prometheus-метрики

Все четыре FastAPI-сервиса экспортируют метрики на `GET /metrics` (формат Prometheus). Метрики генерируются библиотекой `prometheus-fastapi-instrumentator`:

| Метрика | Тип | Описание |
|---------|-----|----------|
| `http_request_duration_seconds` | histogram | Время обработки запроса |
| `http_requests_total` | counter | Количество запросов |
| `http_request_size_bytes` | summary | Размер запросов |
| `http_response_size_bytes` | summary | Размер ответов |

Все четыре сервиса прописаны как targets в `infrastructure/prometheus/prometheus.yml` (scrape interval 15s).

---

## Конфигурация через переменные окружения

Полный перечень переменных и дефолтов: см. [docs/deployment.md](deployment.md#переменные-окружения). Ниже — самые востребованные.

### Prediction Service

| Переменная | По умолчанию | Описание |
|------------|-------------|----------|
| `MODEL_PATH` | `/app/models/model.pkl` | Путь к primary-модели в контейнере |
| `STATIC_AGGS_PATH` | `/app/models/static_aggs.json` | Путь к статическим агрегациям |
| `FILL_VALUES_PATH` | `/app/models/fill_values.json` | Путь к fill-значениям |
| `MODEL_VERSION` | `v1` | Идентификатор версии |
| `HISTORY_WINDOW` | `288` | Окно истории для feature engineering |
| `FORECAST_STEPS` | `10` | Шагов прогноза |
| `STEP_INTERVAL_MINUTES` | `30` | Интервал шага |
| `MOCK_MODE` | `0` | Если `1` — синтетический fallback |

### Dispatcher Service

| Переменная | По умолчанию | Описание |
|------------|-------------|----------|
| `PREDICTION_SERVICE_URL` | `http://prediction-service:8000` | URL prediction-service |
| `TRUCK_CAPACITY` | `33` | Вместимость машины (ёмкости) |
| `BUFFER_PCT` | `0.10` | Базовый буфер |
| `MIN_TRUCKS` | `1` | Минимум машин |
| `ADAPTIVE_BUFFER` | `false` | Плавающий буфер по неопределённости |
| `MIN_BUFFER_PCT` | `0.05` | Нижняя граница adaptive buffer |
| `MAX_BUFFER_PCT` | `0.25` | Верхняя граница adaptive buffer |

### Scheduler Service

| Переменная | По умолчанию | Описание |
|------------|-------------|----------|
| `PREDICTION_INTERVAL_MINUTES` | `30` | Период `prediction_cycle` |
| `QUALITY_CHECK_INTERVAL_MINUTES` | `60` | Период `quality_check` |
| `BATCH_SIZE` | `50` | Маршрутов на batch |
| `FORECAST_HOURS_AHEAD` | `6` | Окно для запросов dispatch |
| `SHADOW_PROMOTE_STREAK_THRESHOLD` | `3` | Подряд побед shadow до автопромоута |

### Retraining Service

| Переменная | По умолчанию | Описание |
|------------|-------------|----------|
| `MODEL_OUTPUT_DIR` | `/app/models` | Куда писать новые версии |
| `TRAINING_WINDOW_DAYS` | `7` | Окно сырых данных |
| `MIN_TRAINING_ROWS` | `1000` | Защита от тренинга на пустоте |
| `N_ESTIMATORS` | `5000` | LightGBM hyperparameter |
| `LEARNING_RATE` | `0.025` | LightGBM hyperparameter |
| `NUM_LEAVES` | `63` | LightGBM hyperparameter |
| `MAX_DEPTH` | `9` | LightGBM hyperparameter |

### Dashboard

| Переменная | По умолчанию | Описание |
|------------|-------------|----------|
| `PREDICTION_SERVICE_URL` | `http://prediction-service:8000` | Прокси-цель для server-side API routes |
| `DISPATCHER_SERVICE_URL` | `http://dispatcher-service:8001` | Прокси-цель |
| `DATABASE_URL` | `postgresql://wildhack:wildhack_dev@postgres:5432/wildhack` | Sync DSN для `pg` (`node-postgres`) |
