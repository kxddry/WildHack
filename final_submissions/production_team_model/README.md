# Production Team Model — LightGBM Hybrid

Drop-in hybrid model for `services/prediction-service`, wrapped from the team's
submission artifact.

## What's inside

```
production_team_model/
├── README.md
├── train_production_model.py    — standalone trainer (reads parquet, no Postgres)
└── models/
    └── model.pkl                — hybrid envelope (6 LGBMRegressor models)
```

## Envelope format

`model.pkl` is a joblib-serialized dict:

```python
{
    'models': {
        'step_1': LGBMRegressor,   # horizon_step == 1
        'step_2': LGBMRegressor,   # horizon_step == 2
        'step_3': LGBMRegressor,   # horizon_step == 3
        'step_4': LGBMRegressor,   # horizon_step == 4
        'step_5': LGBMRegressor,   # horizon_step == 5
        'global_6_10': LGBMRegressor,  # horizon_step 6-10
    },
    'feat_cols_step': [...],    # feature columns for step_1..5 (no horizon_step)
    'feat_cols_global': [...],  # feature columns for global_6_10 (with horizon_step)
    'cat_cols': [...],          # categorical feature names
    'metadata': {
        'model_version': str,
        'training_date': str,
        'combined_score': float | None,
        'submodels': {...},
    },
}
```

## How to regenerate

```bash
# From repo root — wraps final_submissions/team/models/lgb_hybrid_mae_full.pkl
python scripts/wrap_team_hybrid_artifact.py
```

## How to use in prediction-service

```bash
# Copy to running container
docker compose cp final_submissions/production_team_model/models/model.pkl \
    prediction-service:/app/models/model.pkl

# Reload model
curl -X POST http://localhost:8001/model/reload
```

## Standalone training (no Postgres)

```bash
python final_submissions/production_team_model/train_production_model.py
```

Requires: `pandas`, `numpy`, `lightgbm`, `joblib`.
