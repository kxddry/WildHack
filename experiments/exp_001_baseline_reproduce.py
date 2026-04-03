"""exp_001: Reproduce notebook 08 baseline (expected CV ~0.292)."""
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


def main():
    print("=" * 60)
    print("EXP_001: Baseline reproduce (notebook 08)")
    print("=" * 60)

    t0 = time.time()

    print("\n[1/5] Loading data...")
    train_df = pd.read_parquet(TRAIN_PATH)
    test_df = pd.read_parquet(TEST_PATH)
    print(f"  train: {train_df.shape}, test: {test_df.shape}")

    print("\n[2/5] Building OOT validation split...")
    validator = OOTValidator(df=train_df, builder_cls=DatasetBuilder, config='team')
    X_train, y_train, X_val, y_val = validator.make_oot_split(val_points=10, **BUILD_KWARGS)
    print(f"  X_train: {X_train.shape}, X_val: {X_val.shape}")

    cat_features = validator.builder.cat_features.copy()
    for col in cat_features:
        X_train[col] = X_train[col].astype('category')
        X_val[col] = X_val[col].astype('category')

    print("\n[3/5] Training LightGBM...")
    model = LGBMRegressor(**LGB_CONFIG)
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        eval_metric='l1',
        categorical_feature=cat_features,
        callbacks=[early_stopping(stopping_rounds=100), log_evaluation(200)]
    )

    y_pred_val = np.clip(model.predict(X_val), 0, None)

    print("\n[4/5] Evaluating...")
    metric = WapePlusRbias()
    y_val_np = y_val.to_numpy() if hasattr(y_val, 'to_numpy') else np.array(y_val)
    score = metric.calculate(y_val_np, y_pred_val)

    print(f"\n  >>> WAPE+RBias = {score:.6f} <<<")
    print(f"  y_val sum: {y_val_np.sum():.0f}, y_pred sum: {y_pred_val.sum():.0f}")
    print(f"  ratio pred/true: {y_pred_val.sum() / y_val_np.sum():.4f}")

    print("\n[5/5] Generating submission...")
    builder = DatasetBuilder(train=train_df, test=test_df, config='team')
    X_train_full, y_train_full, X_test_full, meta_test = builder.build_train_test(
        return_meta_test=True, **BUILD_KWARGS
    )

    cat_features_full = builder.cat_features.copy()
    for col in cat_features_full:
        X_train_full[col] = X_train_full[col].astype('category')
        X_test_full[col] = X_test_full[col].astype('category')

    model_full = LGBMRegressor(**LGB_CONFIG)
    model_full.fit(
        X_train_full, y_train_full,
        eval_metric='l1',
        categorical_feature=cat_features_full,
        callbacks=[log_evaluation(200)]
    )

    y_pred_test = np.clip(model_full.predict(X_test_full), 0, None)
    submission = builder.make_submission_from_long_preds(y_pred_test, meta_test)
    submission.to_csv('results/exp_001_submission.csv', index=False)

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.0f}s. Submission saved to results/exp_001_submission.csv")
    print(f"RESULT: exp_001 | CV={score:.6f} | model=LGB_L1 | train_days=7 | all_features=True")


if __name__ == '__main__':
    main()
