# Руководство по развёртыванию

## Требования

| Компонент | Минимальная версия | Описание |
|-----------|-------------------|----------|
| Docker | 20.10+ | Контейнеризация |
| Docker Compose | 2.0+ (v2 plugin) | Оркестрация сервисов |
| RAM | 4 GB | Достаточно для всех сервисов |
| Дисковое пространство | 2 GB | Образы Docker + данные PostgreSQL |

Дополнительно для подготовки данных (опционально):
- Python 3.11+
- pip (для установки зависимостей скриптов)

## Быстрый старт

### 1. Клонирование репозитория

```bash
git clone <repo-url>
cd WildHack
```

### 2. Подготовка модели

Убедитесь, что обученная модель находится в директории `models/`:

```bash
ls models/model.pkl
# Если файл отсутствует, обучите модель из experiments/ или получите от участников команды
```

Опционально: файл `models/model_metadata.json` с метаданными модели:

```json
{
    "cv_score": 0.292,
    "objective": "regression_l1",
    "training_date": "2026-04-01"
}
```

### 3. Настройка переменных окружения (опционально)

Скопируйте шаблон и отредактируйте при необходимости:

```bash
cp .env.example .env
```

Для стандартного запуска изменения не требуются — значения по умолчанию уже настроены в `docker-compose.yml`.

### 4. Запуск

```bash
cd infrastructure
docker-compose up --build
```

Первый запуск займёт 2-5 минут (сборка Docker-образов). Последующие запуски — около 30 секунд.

Для запуска в фоне:

```bash
docker-compose up --build -d
```

### 5. Проверка работоспособности

Дождитесь, пока все сервисы стартуют, и проверьте:

```bash
# Health-check prediction-service
curl http://localhost:8000/health

# Health-check dispatcher-service
curl http://localhost:8001/health

# Информация о модели
curl http://localhost:8000/model/info
```

Ожидаемый ответ от `/health`:

```json
{
    "status": "healthy",
    "model_loaded": true,
    "database_connected": true,
    "uptime_seconds": 15.23
}
```

### 6. Заполнение данными

Для демонстрации загрузите тестовые данные:

```bash
# Из корня проекта
python scripts/seed_status_history.py
python scripts/seed_demo_data.py
```

### 7. Доступ к интерфейсам

| Сервис | URL | Описание |
|--------|-----|----------|
| Dashboard | http://localhost:8501 | Основной интерфейс оператора |
| Prediction API (Swagger) | http://localhost:8000/docs | Интерактивная документация prediction-service |
| Dispatcher API (Swagger) | http://localhost:8001/docs | Интерактивная документация dispatcher-service |
| Grafana | http://localhost:3000 | Мониторинг метрик (логин: admin / admin) |
| Prometheus | http://localhost:9090 | Консоль метрик |

---

## Переменные окружения

### PostgreSQL

| Переменная | Значение по умолчанию | Описание |
|------------|----------------------|----------|
| `POSTGRES_DB` | `wildhack` | Имя базы данных |
| `POSTGRES_USER` | `wildhack` | Пользователь БД |
| `POSTGRES_PASSWORD` | `wildhack_dev` | Пароль пользователя БД |

### prediction-service

| Переменная | Значение по умолчанию | Описание |
|------------|----------------------|----------|
| `DATABASE_URL` | `postgresql+asyncpg://wildhack:wildhack_dev@postgres:5432/wildhack` | Строка подключения к PostgreSQL |
| `MODEL_PATH` | `/app/models/model.pkl` | Путь к файлу модели внутри контейнера |
| `MODEL_VERSION` | `v1` | Версия модели для трекинга |
| `HISTORY_WINDOW` | `288` | Окно истории (288 x 30 мин = 6 дней) |
| `FORECAST_STEPS` | `10` | Шагов прогноза (10 x 30 мин = 5 часов) |
| `STEP_INTERVAL_MINUTES` | `30` | Интервал шага (минуты) |

### dispatcher-service

| Переменная | Значение по умолчанию | Описание |
|------------|----------------------|----------|
| `DATABASE_URL` | `postgresql+asyncpg://wildhack:wildhack_dev@postgres:5432/wildhack` | Строка подключения к PostgreSQL |
| `PREDICTION_SERVICE_URL` | `http://prediction-service:8000` | Адрес prediction-service |
| `TRUCK_CAPACITY` | `33` | Ёмкость машины (контейнеров) |
| `BUFFER_PCT` | `0.10` | Буфер неточности (10%) |
| `MIN_TRUCKS` | `1` | Минимум машин |

### dashboard

| Переменная | Значение по умолчанию | Описание |
|------------|----------------------|----------|
| `PREDICTION_SERVICE_URL` | `http://prediction-service:8000` | Адрес prediction-service |
| `DISPATCHER_SERVICE_URL` | `http://dispatcher-service:8001` | Адрес dispatcher-service |
| `DATABASE_URL` | `postgresql://wildhack:wildhack_dev@postgres:5432/wildhack` | Строка подключения (синхронная) |

### Grafana

| Переменная | Значение по умолчанию | Описание |
|------------|----------------------|----------|
| `GF_SECURITY_ADMIN_PASSWORD` | `admin` | Пароль администратора |
| `GF_AUTH_ANONYMOUS_ENABLED` | `true` | Анонимный доступ для просмотра |
| `GF_AUTH_ANONYMOUS_ORG_ROLE` | `Viewer` | Роль анонимного пользователя |

---

## Управление сервисами

### Просмотр логов

```bash
# Все сервисы
docker-compose logs -f

# Конкретный сервис
docker-compose logs -f prediction-service
docker-compose logs -f dispatcher-service
docker-compose logs -f dashboard
```

### Перезапуск отдельного сервиса

```bash
docker-compose restart prediction-service
```

### Остановка

```bash
# Остановка с сохранением данных
docker-compose down

# Остановка с удалением данных PostgreSQL
docker-compose down -v
```

### Пересборка после изменений кода

```bash
docker-compose up --build prediction-service
```

---

## Схема базы данных

Схема создаётся автоматически при первом запуске PostgreSQL из файла `infrastructure/postgres/init.sql`.

**Таблицы:**

| Таблица | Назначение |
|---------|-----------|
| `route_status_history` | История статусов маршрутов (feature engineering) |
| `forecasts` | Сохранённые прогнозы |
| `transport_requests` | Заявки на транспорт |
| `model_metadata` | Метаданные моделей |
| `warehouses` | Справочник складов |

При необходимости сбросить БД:

```bash
docker-compose down -v
docker-compose up --build
```

---

## Устранение неполадок

### Сервис не стартует

**Проблема:** `prediction-service` падает при старте.

**Причина:** Отсутствует файл модели.

**Решение:**
```bash
# Проверьте наличие модели
ls -la models/model.pkl

# Модель должна быть в формате joblib (LightGBM)
```

---

**Проблема:** Ошибка подключения к PostgreSQL.

**Причина:** PostgreSQL ещё не готов.

**Решение:** Docker Compose использует healthcheck (`pg_isready`). Если проблема повторяется:
```bash
# Проверьте статус PostgreSQL
docker-compose ps postgres

# Посмотрите логи
docker-compose logs postgres
```

---

### Dashboard не показывает данные

**Проблема:** Dashboard открывается, но таблицы и графики пустые.

**Причина:** Нет данных в БД.

**Решение:**
```bash
# Загрузите демо-данные
python scripts/seed_status_history.py
python scripts/seed_demo_data.py
```

---

### Порт уже занят

**Проблема:** `Bind for 0.0.0.0:8000 failed: port is already allocated`

**Решение:**
```bash
# Найдите процесс на порту
lsof -i :8000

# Или измените порт в docker-compose.yml
# ports:
#   - "8080:8000"  # внешний:внутренний
```

---

### Ошибки при прогнозе

**Проблема:** `POST /predict` возвращает 500.

**Возможные причины:**
1. Недостаточно исторических данных в `route_status_history`
2. Несовместимая версия модели (другой набор признаков)

**Диагностика:**
```bash
# Логи prediction-service
docker-compose logs -f prediction-service

# Проверьте количество записей истории
docker-compose exec postgres psql -U wildhack -c \
  "SELECT route_id, COUNT(*) FROM route_status_history GROUP BY route_id LIMIT 10;"
```

---

### Prometheus не собирает метрики

**Проблема:** Grafana показывает "No data".

**Решение:**
```bash
# Проверьте, что Prometheus видит targets
# Откройте http://localhost:9090/targets

# Проверьте доступность метрик напрямую
curl http://localhost:8000/metrics
curl http://localhost:8001/metrics
```

---

## Volumes и персистентность

| Volume | Путь в контейнере | Описание |
|--------|-------------------|----------|
| `postgres_data` | `/var/lib/postgresql/data` | Данные PostgreSQL (persisted) |
| `../models` (bind mount, read-only) | `/app/models` | Модель LightGBM |

Данные PostgreSQL сохраняются между перезапусками. Для полного сброса используйте `docker-compose down -v`.
