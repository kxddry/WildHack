# Руководство по развёртыванию

## Требования

| Компонент | Минимальная версия | Описание |
|-----------|-------------------|----------|
| Docker | 20.10+ | Контейнеризация |
| Docker Compose | v2 (plugin) | Оркестрация сервисов |
| RAM | 4 GB | Достаточно для всех 9 контейнеров |
| Дисковое пространство | ~3 GB | Образы Docker + данные PostgreSQL + артефакты моделей |

Опционально для скриптов подготовки данных и e2e-тестов:
- Python 3.11+
- pip / pytest для запуска `tests/e2e/`

---

## Быстрый старт

### 1. Клонирование репозитория

```bash
git clone https://github.com/kxddry/WildHack && cd WildHack
```

### 2. Подготовка модели

`prediction-service` ожидает в каталоге `models/` три артефакта:

```bash
ls models/
# model.pkl           ← обученный LightGBM
# static_aggs.json    ← статические агрегации, посчитанные при тренинге
# fill_values.json    ← медианные fill-значения
```

Опционально — `models/model_metadata.json` с метаданными модели:

```json
{
    "cv_score": 0.292,
    "objective": "regression_l1",
    "training_date": "2026-04-01"
}
```

#### Поведение при отсутствии артефактов

- **По умолчанию (`MOCK_MODE=0`):** prediction-service **падает на старте** с явным сообщением «Missing required model artifacts: ...». Это сделано намеренно — никакое окружение не должно молча отдавать синтетические прогнозы за реальные.
- **`MOCK_MODE=1`:** включается детерминированный mock-предиктор. Сервис стартует, `/health` возвращает `status: "mock"`, в логах появляется предупреждение `MOCK_MODE=1 — enabling synthetic prediction mode`. Используйте только для локальной разработки.

Артефакты также можно сгенерировать через `retraining-service` (`POST /retrain`) — он writeable для `models/`, prediction monтирует тот же каталог read-only.

### 3. Настройка переменных окружения

```bash
cp .env.example .env
```

Файл `.env.example` содержит все переменные с дефолтами; для стандартного запуска изменения не требуются.

### 4. Запуск стека

```bash
docker compose -f infrastructure/docker-compose.yml up --build
```

Первый запуск занимает 2–5 минут (сборка образов). Последующие запуски — около 30 секунд.

В фоне:

```bash
docker compose -f infrastructure/docker-compose.yml up --build -d
```

Порядок старта (через `depends_on` + healthcheck):

1. `postgres`
2. `db-migrate` (one-shot, применяет идемпотентные миграции из `infrastructure/postgres/migrations/`)
3. `prediction-service` (ждёт postgres + db-migrate)
4. `dispatcher-service` (ждёт prediction-service `service_healthy`)
5. `scheduler-service` (ждёт dispatcher-service)
6. `retraining-service` (ждёт prediction-service)
7. `dashboard` (ждёт prediction-service + dispatcher-service)
8. `prometheus`, `grafana`

### 5. Проверка работоспособности

```bash
# Health-checks всех FastAPI-сервисов
curl http://localhost:8000/health   # prediction
curl http://localhost:8001/health   # dispatcher
curl http://localhost:8002/health   # scheduler
curl http://localhost:8003/health   # retraining

# Метаданные модели
curl http://localhost:8000/model/info
```

Ожидаемый ответ от prediction `/health`:

```json
{
    "status": "healthy",
    "model_loaded": true,
    "database_connected": true,
    "uptime_seconds": 15.23
}
```

В `MOCK_MODE=1` поле `status` будет `"mock"`.

### 6. Доступ к интерфейсам

| Сервис | URL | Описание |
|--------|-----|----------|
| Dashboard | http://localhost:4000 | Operator UI (Next.js) |
| Prediction Swagger | http://localhost:8000/docs | API prediction-service |
| Dispatcher Swagger | http://localhost:8001/docs | API dispatcher-service |
| Scheduler Swagger | http://localhost:8002/docs | API scheduler-service |
| Retraining Swagger | http://localhost:8003/docs | API retraining-service |
| Prometheus | http://localhost:9090 | Метрики |
| Grafana | http://localhost:3001 | Дашборды (логин `admin` / `admin`) |

> **Grafana** биндится на `3001`, чтобы не конфликтовать с локальным `next dev` на `3000`.
> **PostgreSQL** биндится только на `127.0.0.1:5432` — dev-БД не торчит в LAN. Поменять можно через `POSTGRES_PORT` в `.env`.

---

## Переменные окружения

Все переменные имеют дефолты в `docker-compose.yml`, поэтому `.env` опционален. Шаблон лежит в `.env.example`.

### Compose / Image

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `COMPOSE_PROJECT_NAME` | `wildhack` | Префикс имён image / network / volume — позволяет двум клонам репозитория делить кэш |
| `IMAGE_PLATFORM` | `linux/amd64` | Дефолтная платформа сборки. Для нативной сборки на Apple Silicon переопределите на `linux/arm64` |

### PostgreSQL

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `POSTGRES_DB` | `wildhack` | Имя БД |
| `POSTGRES_USER` | `wildhack` | Пользователь |
| `POSTGRES_PASSWORD` | `wildhack_dev` | Пароль |
| `POSTGRES_PORT` | `5432` | Внешний порт (биндится на `127.0.0.1` only) |
| `DATABASE_URL` | `postgresql+asyncpg://wildhack:wildhack_dev@127.0.0.1:5432/wildhack` | DSN для host-side инструментов (psql, pgcli) |

### Порты сервисов

| Переменная | По умолчанию | Сервис |
|------------|--------------|--------|
| `PREDICTION_PORT` | `8000` | prediction-service |
| `DISPATCHER_PORT` | `8001` | dispatcher-service |
| `SCHEDULER_PORT` | `8002` | scheduler-service |
| `RETRAINING_PORT` | `8003` | retraining-service |
| `DASHBOARD_PORT` | `4000` | dashboard (внутри `:3000`) |
| `PROMETHEUS_PORT` | `9090` | prometheus |
| `GRAFANA_PORT` | `3001` | grafana |

### prediction-service

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `DATABASE_URL` | (compose-built) | DSN PostgreSQL |
| `MODEL_PATH` | `/app/models/model.pkl` | Путь к primary-модели в контейнере (фиксированный mount, не переопределяется через `.env`) |
| `STATIC_AGGS_PATH` | `/app/models/static_aggs.json` | Путь к статическим агрегациям (фиксированный) |
| `FILL_VALUES_PATH` | `/app/models/fill_values.json` | Путь к fill-значениям (фиксированный) |
| `MODEL_VERSION` | `v1` | Идентификатор версии для трекинга |
| `HISTORY_WINDOW` | `288` | Окно истории для feature engineering (288 × 30 мин ≈ 6 дней) |
| `FORECAST_STEPS` | `10` | Шагов прогноза (10 × 30 мин = 5 часов) |
| `STEP_INTERVAL_MINUTES` | `30` | Интервал шага в минутах |
| `MOCK_MODE` | `0` | Если `1` — синтетический fallback при отсутствии артефактов |

### dispatcher-service

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `DATABASE_URL` | (compose-built) | DSN PostgreSQL |
| `PREDICTION_SERVICE_URL` | `http://prediction-service:8000` | Адрес prediction-service |
| `TRUCK_CAPACITY` | `33` | Вместимость машины (ёмкости) |
| `BUFFER_PCT` | `0.10` | Базовый буфер |
| `MIN_TRUCKS` | `1` | Минимум машин при ненулевом прогнозе |
| `ADAPTIVE_BUFFER` | `false` | Включает плавающий буфер по неопределённости |
| `MIN_BUFFER_PCT` | `0.05` | Нижняя граница adaptive buffer |
| `MAX_BUFFER_PCT` | `0.25` | Верхняя граница adaptive buffer |

### scheduler-service

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `DATABASE_URL` | (compose-built) | DSN PostgreSQL |
| `PREDICTION_SERVICE_URL` | `http://prediction-service:8000` | Адрес prediction-service |
| `DISPATCHER_SERVICE_URL` | `http://dispatcher-service:8001` | Адрес dispatcher-service |
| `RETRAINING_SERVICE_URL` | `http://retraining-service:8003` | Адрес retraining-service |
| `PREDICTION_INTERVAL_MINUTES` | `30` | Период `prediction_cycle` |
| `QUALITY_CHECK_INTERVAL_MINUTES` | `60` | Период `quality_check` |
| `BATCH_SIZE` | `50` | Маршрутов на batch-вызов prediction |
| `FORECAST_HOURS_AHEAD` | `6` | Окно для запросов dispatch |
| `SHADOW_PROMOTE_STREAK_THRESHOLD` | `3` | Подряд побед shadow до автопромоута |

### retraining-service

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `DATABASE_URL` | (compose-built) | DSN async |
| `SYNC_DATABASE_URL` | (compose-built) | DSN sync (для тренинга) |
| `PREDICTION_SERVICE_URL` | `http://prediction-service:8000` | Куда грузить shadow |
| `MODEL_OUTPUT_DIR` | `/app/models` | Куда писать новые версии |
| `TRAINING_WINDOW_DAYS` | `7` | Окно сырых данных для тренинга |
| `MIN_TRAINING_ROWS` | `1000` | Защита от тренинга на пустоте |
| `N_ESTIMATORS`, `LEARNING_RATE`, `NUM_LEAVES`, `MAX_DEPTH`, ... | см. `app/config.py` | Гиперпараметры LightGBM |

### dashboard

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `PREDICTION_SERVICE_URL` | `http://prediction-service:8000` | Адрес prediction-service (для server-side прокси) |
| `DISPATCHER_SERVICE_URL` | `http://dispatcher-service:8001` | Адрес dispatcher-service |
| `DATABASE_URL` | `postgresql://wildhack:wildhack_dev@postgres:5432/wildhack` | DSN PostgreSQL (синхронный, для `pg` / `node-postgres`) |

### Grafana

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `GF_SECURITY_ADMIN_PASSWORD` | `admin` | Пароль администратора |
| `GF_AUTH_ANONYMOUS_ENABLED` | `false` | Анонимный доступ выключен по умолчанию (включать только в изолированной демо-сети) |
| `GF_AUTH_ANONYMOUS_ORG_ROLE` | `Viewer` | Роль анонимного пользователя при включении |

---

## Управление сервисами

### Просмотр логов

```bash
# Все сервисы
docker compose -f infrastructure/docker-compose.yml logs -f

# Конкретный сервис
docker compose -f infrastructure/docker-compose.yml logs -f prediction-service
docker compose -f infrastructure/docker-compose.yml logs -f scheduler-service
```

### Перезапуск

```bash
docker compose -f infrastructure/docker-compose.yml restart prediction-service
```

### Остановка

```bash
# С сохранением данных PostgreSQL
docker compose -f infrastructure/docker-compose.yml down

# С удалением данных
docker compose -f infrastructure/docker-compose.yml down -v
```

### Пересборка после изменений кода

```bash
# Конкретный сервис
docker compose -f infrastructure/docker-compose.yml up --build prediction-service

# Принудительная пересборка без кэша
docker compose -f infrastructure/docker-compose.yml build --no-cache
```

---

## Healthchecks и graceful shutdown

Каждый python-сервис имеет HEALTHCHECK на уровне Dockerfile (через stdlib `urllib.request`) и compose-уровневый healthcheck с тем же тестом, но переопределяемой cadence (`interval`, `start_period`). Двойной слой нужен, чтобы менять параметры probe без пересборки образа.

`stop_grace_period: 30s` + `uvicorn --timeout-graceful-shutdown 25` дают in-flight запросам 25 секунд на завершение до SIGKILL.

Dashboard использует `node -e "fetch('http://127.0.0.1:3000/').then(...)"` — Node 22 ships global fetch, дополнительные пакеты в образе не нужны.

---

## Схема базы данных и миграции

Базовая схема создаётся `infrastructure/postgres/init.sql` при первом запуске PostgreSQL (выполняется только на пустом volume). Дополнительные изменения — через идемпотентные миграции в `infrastructure/postgres/migrations/`, которые проигрывает sidecar `db-migrate` на каждом `compose up`.

**Таблицы:**

| Таблица | Назначение |
|---------|-----------|
| `route_status_history` | История наблюдений (источник для feature engineering) |
| `forecasts` | Сохранённые прогнозы (primary + shadow) |
| `transport_requests` | Заявки на транспорт + `actual_vehicles` / `actual_units` |
| `routes`, `warehouses` | Справочники |
| `model_metadata` | Реестр моделей |
| `pipeline_runs` | Аудит-лог запусков scheduler |
| `prediction_quality` | История WAPE / RBias / combined_score |
| `retrain_history` | Аудит-лог переобучений |

**Сброс БД:**

```bash
docker compose -f infrastructure/docker-compose.yml down -v
docker compose -f infrastructure/docker-compose.yml up --build
```

При пересоздании volume `init.sql` отработает заново, после чего `db-migrate` догонит миграции.

---

## Volumes и персистентность

| Volume | Путь в контейнере | Режим | Описание |
|--------|-------------------|-------|----------|
| `postgres_data` | `/var/lib/postgresql/data` | rw | Данные PostgreSQL (persisted) |
| `../models` (bind mount) | `/app/models` (prediction) | **ro** | prediction-service consumer |
| `../models` (bind mount) | `/app/models` (retraining) | **rw** | retraining-service producer |

Producer/consumer-контракт через `:ro` / `:rw` исключает случайные записи модели из неположенного сервиса на уровне Docker.

Данные PostgreSQL сохраняются между перезапусками. Полный сброс — `down -v`.

---

## Устранение неполадок

### prediction-service падает с `Missing required model artifacts`

**Причина:** в каталоге `models/` нет одного из трёх обязательных файлов: `model.pkl`, `static_aggs.json`, `fill_values.json`.

**Решение (production):** положите артефакты в `models/` и перезапустите сервис.

**Решение (локальная разработка):**

```bash
# Включите синтетический fallback
echo "MOCK_MODE=1" >> .env
docker compose -f infrastructure/docker-compose.yml up --build prediction-service
```

В `/health` будет `status: "mock"`.

---

### `db-migrate` падает / висит

**Причина:** обычно — синтаксическая ошибка в новом `.sql` файле или попытка применить не-идемпотентную миграцию.

**Решение:**

```bash
docker compose -f infrastructure/docker-compose.yml logs db-migrate
```

Все миграции **обязаны** содержать `IF NOT EXISTS` / guarded `DO`-блоки — `db-migrate` запускается на каждом `up`, не только первом.

---

### Сервис не стартует — `service_healthy` зависает

**Причина:** healthcheck не проходит в течение `start_period`.

**Диагностика:**

```bash
# Текущий статус всех сервисов
docker compose -f infrastructure/docker-compose.yml ps

# Логи конкретного сервиса
docker compose -f infrastructure/docker-compose.yml logs -f <service>
```

Для prediction-service `start_period: 40s` — должен хватать на загрузку модели; если LightGBM грузится дольше (большая модель), увеличьте в `docker-compose.yml`.

---

### PostgreSQL — connection refused

**Причина:** PostgreSQL ещё не готов или порт уже занят на хосте.

**Решение:**

```bash
# Проверка статуса
docker compose -f infrastructure/docker-compose.yml ps postgres
docker compose -f infrastructure/docker-compose.yml logs postgres

# Проверка занятости порта на хосте
lsof -i :5432
```

Поменять порт можно через `POSTGRES_PORT` в `.env`. Внутри сети контейнеров адрес всегда `postgres:5432`.

---

### Dashboard пустой

**Причина:** в БД нет данных.

**Решение:**

```bash
# Внеочередной запуск pipeline через scheduler
curl -X POST http://localhost:8002/pipeline/trigger

# Или ручной seed (если есть скрипты)
python scripts/seed_status_history.py
python scripts/seed_demo_data.py
```

---

### Порт занят (`Bind for 0.0.0.0:8000 failed`)

**Решение:** найдите процесс и переназначьте порт через `.env`.

```bash
lsof -i :8000

# Или поменяйте в .env, например:
# PREDICTION_PORT=18000
```

---

### `/predict` возвращает 500

**Возможные причины:**

1. Недостаточно исторических данных в `route_status_history` — feature engine не может построить признаки даже после cold-start fallback
2. Несовместимая версия модели (другой набор признаков, чем `static_aggs.json` / `fill_values.json`)

**Диагностика:**

```bash
docker compose -f infrastructure/docker-compose.yml logs -f prediction-service

# Сколько наблюдений в истории
docker compose -f infrastructure/docker-compose.yml exec postgres \
  psql -U wildhack -d wildhack -c \
  "SELECT route_id, COUNT(*) FROM route_status_history GROUP BY route_id LIMIT 10;"
```

Перечитать признаки после пересохранения JSON-ов:

```bash
curl -X POST http://localhost:8000/model/reload-features
```

---

### Prometheus показывает `No data`

**Решение:**

```bash
# Targets prometheus
open http://localhost:9090/targets

# Метрики напрямую
curl http://localhost:8000/metrics | head
curl http://localhost:8001/metrics | head
curl http://localhost:8002/metrics | head
curl http://localhost:8003/metrics | head
```

Все четыре FastAPI-сервиса экспонируют `/metrics` через `prometheus-fastapi-instrumentator`. Если targets up, но Grafana пустая — проверьте datasource в `infrastructure/grafana/provisioning/datasources/`.

---

### Apple Silicon — медленная сборка / падение QEMU

**Причина:** ранее в `docker-compose.yml` была попытка зафиксировать `platform: linux/amd64`, которая включала Rosetta/QEMU и делала сборку >20 минут.

**Текущее поведение:** `platform:` намеренно НЕ задан в compose — Docker берёт нативную архитектуру хоста. Если нужна cross-arch сборка для CI, переопределите `IMAGE_PLATFORM` в `.env` или передайте `--platform` явно в `docker compose build`.
