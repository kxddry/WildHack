"""exp_004: Training window sweep — compare 7/11/14/21/28 days."""
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

BASE_BUILD_KWARGS = dict(
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

LGB_CONFIG = dict(
    objective='regression_l1',
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

TRAIN_DAYS_LIST = [7, 11, 14, 21, 28]


def main():
    print("=" * 60)
    print("EXP_004: Training window sweep")
    print("=" * 60)

    t0 = time.time()
    train_df = pd.read_parquet(TRAIN_PATH)
    metric = WapePlusRbias()

    results = []

    for train_days in TRAIN_DAYS_LIST:
        print(f"\n--- train_days={train_days} ---")
        t1 = time.time()

        build_kwargs = {**BASE_BUILD_KWARGS, 'train_days': train_days}

        validator = OOTValidator(df=train_df, builder_cls=DatasetBuilder, config='team')
        X_train, y_train, X_val, y_val = validator.make_oot_split(val_points=10, **build_kwargs)

        cat_features = validator.builder.cat_features.copy()
        for col in cat_features:
            X_train[col] = X_train[col].astype('category')
            X_val[col] = X_val[col].astype('category')

        model = LGBMRegressor(**LGB_CONFIG)
        model.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            eval_metric='l1',
            categorical_feature=cat_features,
            callbacks=[early_stopping(stopping_rounds=100), log_evaluation(500)]
        )

        y_pred_val = np.clip(model.predict(X_val), 0, None)
        y_val_np = y_val.to_numpy() if hasattr(y_val, 'to_numpy') else np.array(y_val)
        score = metric.calculate(y_val_np, y_pred_val)
        ratio = y_pred_val.sum() / y_val_np.sum()

        elapsed_iter = time.time() - t1
        results.append((train_days, score, ratio, elapsed_iter))
        print(f"  train_days={train_days} | CV={score:.6f} | ratio={ratio:.4f} | {elapsed_iter:.0f}s")

    print("\n" + "=" * 60)
    print("SUMMARY:")
    print(f"{'days':>6} {'CV':>10} {'ratio':>8} {'time':>8}")
    for train_days, score, ratio, elapsed_iter in results:
        print(f"{train_days:>6} {score:>10.6f} {ratio:>8.4f} {elapsed_iter:>7.0f}s")

    best = min(results, key=lambda x: x[1])
    print(f"\nBest: train_days={best[0]}, CV={best[1]:.6f}")

    elapsed = time.time() - t0
    print(f"\nTotal time: {elapsed:.0f}s")
    print(f"RESULT: exp_004 | best_days={best[0]} | best_CV={best[1]:.6f}")


if __name__ == '__main__':
    main()
