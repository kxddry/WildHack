# WB Space — Final Solutions

Финальные решения для **solo** и **team** треков соревнования WB Space (otgruzki-bez-prostoev).

## Структура

```
final_submissions/
├── README.md        — этот файл
├── solo/            — финальное решение Solo Track (target_1h, 8 horizons)
│   ├── README.md
│   ├── 01_build_features.py
│   ├── 02_train_lgb.py
│   ├── 03_train_gpu.py
│   ├── 04_submit.py
│   ├── models/      — натренированные модели для submission
│   └── submission_*.csv
└── team/            — финальное решение Team Track (target_2h, 10 horizons)
    ├── README.md
    ├── precompute_oof_features.py
    ├── 01_build_features.py
    ├── 02_train_lgb.py
    ├── 03_train_mlp.py
    ├── 04_submit.py
    ├── models/      — натренированные модели для submission
    └── submission_*.csv
```

## Общее

Оба решения используют:
- **3-fold OOT cross-validation** + **Ridge stacking** на out-of-fold предсказаниях
- **Kaggle features** (same-slot lags, momentum, nonzero rate, smoothed target encoding)
- **Multi-seed MLP averaging** для уменьшения variance
- **Round to int** в финальных предсказаниях
- **Post-processing**: масштабирование и (для team) zero-routes mask

## Solo Track

| Component | Detail |
|---|---|
| Loss | **Poisson** для всех моделей |
| Models | LightGBM Poisson + XGBoost Heavy Poisson GPU + ResMLP × 5 seeds |
| Stack | Ridge with `coefs=[0.637, 0.509, -0.149]`, intercept `274.34` |
| OOF | **0.335679** (in-sample on full Ridge) |
| Submission | `solo/submission_x1.025.csv` |

**Insight:** для solo (большие counts, mean ~266K) Poisson loss работает значительно лучше MAE.

## Team Track

| Component | Detail |
|---|---|
| Loss | **MAE** для LGB + **L1** для MLP |
| Models | LightGBM Hybrid (per-horizon 1-5 + global 6-10) + ResMLP Pyramid × 5 seeds |
| Stack | Ridge with `coefs=[0.846, 0.165]`, intercept `2.10` |
| OOF | **0.277158** (honest LOFO) |
| Submission | `team/submission_x0.97.csv` |

**Insight:** для team (маленькие counts, mean ~67) MAE остаётся королём — Poisson/Tweedie/Huber хуже. Pyramid arch (1536→768→384→192) бьёт обычный 1024-512-256.

## Зависимости

- Python 3.13+
- LightGBM 4.6
- XGBoost 3.2 (CUDA)
- PyTorch 2.6 + CUDA 12.4
- scikit-learn 1.8
- pandas 3.0, numpy 2.4

## Структура исходного репозитория

Скрипты `0X_*.py` зависят от модулей `core/data.py` и `core/metric.py` из родительского репозитория. Запускать следует из `experiments/` директории.

## Reproducibility

Все скрипты используют фиксированные random_seed (`42` для одиночных моделей, `[42, 123, 456, 789, 2024]` для multi-seed ensemble). Точная воспроизводимость подтверждена для LGB Hybrid baseline (старые сохранённые модели дают идентичный score).

## Git LFS

Большие файлы моделей хранятся через **Git LFS**:
- `*.pkl` (LightGBM models, 66-82 MB)
- `*.pt` (PyTorch ResMLP weights, 3-9 MB)
- `*.npz` / `*.npy` (numpy artefacts)
- `solo/models/*.json` / `team/models/*.json` (XGBoost models, до 250 MB)

### Установка LFS перед клонированием

```bash
# Установка Git LFS (один раз)
git lfs install

# Клонирование репозитория
git clone <repo-url>
cd <repo>

# LFS файлы скачиваются автоматически
# Если нет — pull вручную:
git lfs pull
```

### При первом push
```bash
cd final_submissions
git lfs install
git add .gitattributes
git add solo/ team/ README.md .gitignore
git commit -m "Add final submissions (solo + team)"
git push
```

`.gitattributes` уже настроен — Git автоматически переключит соответствующие файлы на LFS storage.
