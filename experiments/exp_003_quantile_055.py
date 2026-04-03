"""exp_003: LightGBM quantile regression alpha=0.55 (stronger upward shift)."""
import sys
import time
from functools import partial

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor, early_stopping, log_evaluation

from core.data import DatasetBuilder, OOTValidator
from core.metric import WapePlusRbias

print = partial(print, flush=True)

TRAIN_PATH = '../Data/raw/train_team_track.parquet'
TEST_PATH = '../Data/raw/test_team_track.parquet'

BUILD_KWARGS = dict(
    train_days=7,
    use_static_aggs=True,
    use_total_status_features=True,
    use_total_inventory_aggs=True,
    use_target_mean_hist=True,
    use_target_std_hist=True,
    use_target_zero_rate_hist=True,
    use_target_count_hist=True,
    use_default_ts_features=True,
    static_agg_features=[f'status_{i}' for i in range(1, 9)],
    total_inventory_agg_features=[
        'total_inventory', 'early_inventory', 'mid_inventory', 'late_inventory',
        'early_share', 'mid_share', 'late_share', 'status_entropy'
    ],
    static_group_keys_list=[
        ['route_id'], ['office_from_id'], ['route_id', 'dow'], ['route_id', 'pod']
    ],
    total_inventory_group_keys_list=[
        ['route_id'], ['office_from_id'],
        ['route_id', 'dow'], ['route_id', 'pod'], ['route_id', 'slot']
    ],
    target_hist_group_keys_list=[
        ['route_id'], ['route_id', 'pod'], ['route_id', 'dow']
    ],
    statistics=('mean', 'std')
)

ALPHA = 0.55

LGB_CONFIG = dict(
    objective='quantile',
    alpha=ALPHA,
    boosting_type='gbdt',
    n_estimators=5000,
    learning_rate=0.025,
    num_leaves=63,
    max_depth=9,
    min_child_samples=80,
    min_child_weight=0.01,
    min_split_gain=0.05,
    subsample=0.8,
    subsample_freq=1,
    colsample_bytree=0.75,
    reg_alpha=0.5,
    reg_lambda=8.0,
    subsample_for_bin=200000,
    random_state=42,
    n_jobs=-1,
    importance_type='gain',
    verbosity=-1,
)


def main():
    print("=" * 60)
    print(f"EXP_003: Quantile regression alpha={ALPHA}")
    print("=" * 60)

    t0 = time.time()

    print("\n[1/4] Loading data...")
    train_df = pd.read_parquet(TRAIN_PATH)
    test_df = pd.read_parquet(TEST_PATH)

    print("\n[2/4] Building OOT validation split...")
    validator = OOTValidator(df=train_df, builder_cls=DatasetBuilder, config='team')
    X_train, y_train, X_val, y_val = validator.make_oot_split(val_points=10, **BUILD_KWARGS)

    cat_features = validator.builder.cat_features.copy()
    for col in cat_features:
        X_train[col] = X_train[col].astype('category')
        X_val[col] = X_val[col].astype('category')

    print(f"\n[3/4] Training LightGBM quantile(alpha={ALPHA})...")
    model = LGBMRegressor(**LGB_CONFIG)
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        categorical_feature=cat_features,
        callbacks=[early_stopping(stopping_rounds=100), log_evaluation(200)]
    )

    y_pred_val = np.clip(model.predict(X_val), 0, None)

    print("\n[4/4] Evaluating...")
    metric = WapePlusRbias()
    y_val_np = y_val.to_numpy() if hasattr(y_val, 'to_numpy') else np.array(y_val)
    score = metric.calculate(y_val_np, y_pred_val)

    print(f"\n  >>> WAPE+RBias = {score:.6f} <<<")
    print(f"  y_val sum: {y_val_np.sum():.0f}, y_pred sum: {y_pred_val.sum():.0f}")
    print(f"  ratio pred/true: {y_pred_val.sum() / y_val_np.sum():.4f}")

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.0f}s")
    print(f"RESULT: exp_003 | CV={score:.6f} | model=LGB_quantile_{ALPHA} | train_days=7")


if __name__ == '__main__':
    main()
