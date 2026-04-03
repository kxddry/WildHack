# Справочник API

Оба сервиса предоставляют автоматическую OpenAPI-документацию (Swagger UI):
- Prediction Service: http://localhost:8000/docs
- Dispatcher Service: http://localhost:8001/docs

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
| timestamp | datetime | да | Момент времени наблюдения (ISO 8601) |
| status_1..8 | float | да | Количество товаров, прошедших соответствующий этап обработки за последние 30 минут |

**Ответ (200):**

```json
{
    "route_id": 101,
    "warehouse_id": 42,
    "anchor_timestamp": "2026-04-02T12:00:00",
    "forecasts": [
        {
            "horizon_step": 1,
            "timestamp": "2026-04-02T12:30:00",
            "predicted_value": 12.3456
        },
        {
            "horizon_step": 2,
            "timestamp": "2026-04-02T13:00:00",
            "predicted_value": 14.7891
        },
        {
            "horizon_step": 3,
            "timestamp": "2026-04-02T13:30:00",
            "predicted_value": 11.2034
        }
    ],
    "model_version": "v1"
}
```

| Поле | Тип | Описание |
|------|-----|----------|
| route_id | int | Идентификатор маршрута |
| warehouse_id | int | Идентификатор склада, которому принадлежит маршрут |
| anchor_timestamp | datetime | Опорный момент времени прогноза |
| forecasts | list[ForecastStep] | Массив из 10 шагов прогноза |
| forecasts[].horizon_step | int | Номер шага (1-10) |
| forecasts[].timestamp | datetime | Временная точка прогноза |
| forecasts[].predicted_value | float | Прогнозируемое значение target_2h (ёмкости) |
| model_version | string | Версия использованной модели |

---

### POST /predict/batch

Пакетный прогноз для нескольких маршрутов. Если прогноз для одного маршрута падает с ошибкой, остальные обрабатываются (fail-safe).

**Запрос:**

```json
{
    "predictions": [
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
        },
        {
            "route_id": 102,
            "timestamp": "2026-04-02T12:00:00",
            "status_1": 10.0,
            "status_2": 18.0,
            "status_3": 5.0,
            "status_4": 30.0,
            "status_5": 22.0,
            "status_6": 14.0,
            "status_7": 3.0,
            "status_8": 1.0
        }
    ]
}
```

**Ответ (200):**

```json
{
    "results": [
        {
            "route_id": 101,
            "warehouse_id": 42,
            "anchor_timestamp": "2026-04-02T12:00:00",
            "forecasts": [...],
            "model_version": "v1"
        },
        {
            "route_id": 102,
            "warehouse_id": 42,
            "anchor_timestamp": "2026-04-02T12:00:00",
            "forecasts": [...],
            "model_version": "v1"
        }
    ],
    "total": 2,
    "processing_time_ms": 145.23
}
```

| Поле | Тип | Описание |
|------|-----|----------|
| results | list[PredictResponse] | Массив результатов прогноза |
| total | int | Количество успешных прогнозов |
| processing_time_ms | float | Общее время обработки в миллисекундах |

---

### GET /model/info

Метаданные загруженной модели.

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

| Поле | Тип | Описание |
|------|-----|----------|
| model_version | string | Версия модели |
| model_type | string | Тип модели (LGBMRegressor) |
| objective | string | Функция потерь при обучении |
| cv_score | float \| null | Кросс-валидационный скор (WAPE + \|RBias\|) |
| feature_count | int | Количество признаков |
| training_date | string \| null | Дата обучения модели |
| forecast_horizon | int | Количество шагов прогноза |
| step_interval_minutes | int | Интервал между шагами в минутах |

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

| Поле | Тип | Описание |
|------|-----|----------|
| status | string | `"healthy"` или `"degraded"` |
| model_loaded | bool | Загружена ли модель |
| database_connected | bool | Доступна ли БД |
| uptime_seconds | float | Время работы сервиса в секундах |

---

## Dispatcher Service (порт 8001)

### POST /dispatch

Расчёт необходимого транспорта для склада. Поддерживает два режима:
1. **Прямая передача прогнозов** — передать массив `forecasts`
2. **Выборка из БД** — указать `time_range_start` и `time_range_end`

**Запрос (с прогнозами):**

```json
{
    "warehouse_id": 42,
    "forecasts": [
        {
            "timestamp": "2026-04-02T14:00:00",
            "total_containers": 95.7
        },
        {
            "timestamp": "2026-04-02T16:00:00",
            "total_containers": 72.3
        }
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

| Поле | Тип | Обязательное | Описание |
|------|-----|:---:|----------|
| warehouse_id | int | да | Идентификатор склада |
| forecasts | list[ForecastInput] \| null | нет* | Массив прогнозов |
| forecasts[].timestamp | datetime | да | Временная точка |
| forecasts[].total_containers | float | да | Прогнозируемое количество ёмкостей |
| time_range_start | datetime \| null | нет* | Начало временного диапазона |
| time_range_end | datetime \| null | нет* | Конец временного диапазона |

*Необходимо указать либо `forecasts`, либо оба поля `time_range_start` и `time_range_end`.

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
        },
        {
            "time_slot_start": "2026-04-02T16:00:00",
            "time_slot_end": "2026-04-02T16:00:00",
            "total_containers": 72.3,
            "truck_capacity": 33,
            "buffer_pct": 0.10,
            "trucks_needed": 3,
            "calculation": "ceil(72.3 * (1 + 0.1) / 33) = ceil(79.5300 / 33) = 3"
        }
    ],
    "config": {
        "truck_capacity": 33,
        "buffer_pct": 0.10,
        "min_trucks": 1
    }
}
```

| Поле | Тип | Описание |
|------|-----|----------|
| warehouse_id | int | Идентификатор склада |
| dispatch_requests | list | Массив заявок на транспорт |
| dispatch_requests[].time_slot_start | datetime | Начало временного слота |
| dispatch_requests[].time_slot_end | datetime | Конец временного слота |
| dispatch_requests[].total_containers | float | Суммарный объём ёмкостей |
| dispatch_requests[].truck_capacity | int | Вместимость машины |
| dispatch_requests[].buffer_pct | float | Применённый буфер |
| dispatch_requests[].trucks_needed | int | Рассчитанное количество машин |
| dispatch_requests[].calculation | string | Формула расчёта (для прозрачности) |
| config | dict | Использованные параметры конфигурации |

---

### GET /dispatch/schedule

Расписание диспатчинга для склада (из сохранённых заявок).

**Параметры запроса:**

| Параметр | Тип | Обязательное | Описание |
|----------|-----|:---:|----------|
| warehouse_id | int | да | Идентификатор склада |

**Пример:** `GET /dispatch/schedule?warehouse_id=42`

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

Список всех складов с текущей информацией.

**Ответ (200):**

```json
{
    "warehouses": [
        {
            "warehouse_id": 42,
            "route_count": 15,
            "latest_forecast_at": "2026-04-02T12:30:00",
            "upcoming_trucks": 7
        },
        {
            "warehouse_id": 55,
            "route_count": 8,
            "latest_forecast_at": "2026-04-02T12:00:00",
            "upcoming_trucks": 3
        }
    ],
    "total": 2
}
```

| Поле | Тип | Описание |
|------|-----|----------|
| warehouses[].warehouse_id | int | Идентификатор склада |
| warehouses[].route_count | int | Количество маршрутов склада |
| warehouses[].latest_forecast_at | datetime \| null | Время последнего прогноза |
| warehouses[].upcoming_trucks | int | Суммарное количество запланированных машин |
| total | int | Общее количество складов |

---

### GET /health

Проверка здоровья dispatcher-service.

**Ответ (200):**

```json
{
    "status": "healthy",
    "database_connected": true,
    "uptime_seconds": 3421.56
}
```

---

## Коды ошибок

| Код | Ситуация | Пример |
|-----|----------|--------|
| 200 | Успешный запрос | Прогноз выполнен |
| 404 | Данные не найдены | Нет прогнозов для склада в указанном диапазоне |
| 422 | Ошибка валидации | Неверный формат запроса, отсутствуют обязательные поля, не указаны ни `forecasts`, ни `time_range_*` |
| 500 | Внутренняя ошибка | Сбой prediction pipeline |
| 503 | Сервис не готов | Модель не загружена |

**Формат ошибки:**

```json
{
    "detail": "No forecasts found for warehouse 42 in the given time range"
}
```

---

## Prometheus-метрики

Оба сервиса экспортируют метрики на эндпоинте `GET /metrics` (формат Prometheus).

Метрики генерируются библиотекой `prometheus-fastapi-instrumentator`:

| Метрика | Тип | Описание |
|---------|-----|----------|
| `http_request_duration_seconds` | histogram | Время обработки запроса |
| `http_requests_total` | counter | Общее количество запросов |
| `http_request_size_bytes` | summary | Размер запросов |
| `http_response_size_bytes` | summary | Размер ответов |

---

## Конфигурация через переменные окружения

### Prediction Service

| Переменная | По умолчанию | Описание |
|------------|-------------|----------|
| `DATABASE_URL` | `postgresql+asyncpg://wildhack:wildhack_dev@localhost:5432/wildhack` | URL подключения к PostgreSQL |
| `MODEL_PATH` | `models/model.pkl` | Путь к файлу модели |
| `MODEL_VERSION` | `v1` | Идентификатор версии модели |
| `HISTORY_WINDOW` | `288` | Количество исторических наблюдений для feature engineering |
| `FORECAST_STEPS` | `10` | Количество шагов прогноза |
| `STEP_INTERVAL_MINUTES` | `30` | Интервал между шагами прогноза (минуты) |

### Dispatcher Service

| Переменная | По умолчанию | Описание |
|------------|-------------|----------|
| `DATABASE_URL` | `postgresql+asyncpg://wildhack:wildhack_dev@localhost:5432/wildhack` | URL подключения к PostgreSQL |
| `PREDICTION_SERVICE_URL` | `http://prediction-service:8000` | URL prediction-service |
| `TRUCK_CAPACITY` | `33` | Вместимость машины (ёмкостей) |
| `BUFFER_PCT` | `0.10` | Буфер для ошибки прогноза (0.10 = 10%) |
| `MIN_TRUCKS` | `1` | Минимум машин при ненулевом прогнозе |

### Dashboard

| Переменная | По умолчанию | Описание |
|------------|-------------|----------|
| `PREDICTION_SERVICE_URL` | `http://prediction-service:8000` | URL prediction-service |
| `DISPATCHER_SERVICE_URL` | `http://dispatcher-service:8001` | URL dispatcher-service |
| `DATABASE_URL` | `postgresql://wildhack:wildhack_dev@postgres:5432/wildhack` | URL PostgreSQL (синхронный) |
