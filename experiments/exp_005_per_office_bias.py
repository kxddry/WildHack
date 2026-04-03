"""exp_005: Per-office bias correction on top of baseline LGB."""
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
    return_meta_test=True,
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


def compute_per_office_correction(y_true, y_pred, route_ids, train_df):
    """Compute multiplicative correction factor per office_from_id."""
    route_to_office = train_df.groupby('route_id')['office_from_id'].first().to_dict()

    df = pd.DataFrame({
        'y_true': y_true,
        'y_pred': y_pred,
        'route_id': route_ids
    })
    df['office_from_id'] = df['route_id'].map(route_to_office)

    corrections = {}
    for office_id, group in df.groupby('office_from_id'):
        true_sum = group['y_true'].sum()
        pred_sum = group['y_pred'].sum()
        if pred_sum > 0:
            corrections[office_id] = true_sum / pred_sum
        else:
            corrections[office_id] = 1.0

    return corrections


def apply_per_office_correction(y_pred, route_ids, corrections, train_df):
    """Apply per-office multiplicative correction."""
    route_to_office = train_df.groupby('route_id')['office_from_id'].first().to_dict()
    offices = pd.Series(route_ids).map(route_to_office)
    factors = offices.map(corrections).fillna(1.0).values
    return np.clip(y_pred * factors, 0, None)


def main():
    print("=" * 60)
    print("EXP_005: Per-office bias correction")
    print("=" * 60)

    t0 = time.time()

    print("\n[1/5] Loading data...")
    train_df = pd.read_parquet(TRAIN_PATH)
    test_df = pd.read_parquet(TEST_PATH)

    print("\n[2/5] Building OOT split...")
    validator = OOTValidator(df=train_df, builder_cls=DatasetBuilder, config='team')
    X_train, y_train, X_val, y_val, meta_val = validator.make_oot_split(
        val_points=10, return_y_test=True, **{k: v for k, v in BUILD_KWARGS.items() if k != 'return_meta_test'},
        return_meta_test=True
    )

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

    y_pred_val_raw = np.clip(model.predict(X_val), 0, None)
    y_val_np = y_val.to_numpy() if hasattr(y_val, 'to_numpy') else np.array(y_val)

    metric = WapePlusRbias()
    score_raw = metric.calculate(y_val_np, y_pred_val_raw)
    print(f"\n  Raw CV: {score_raw:.6f}")

    print("\n[4/5] Computing per-office correction factors...")
    corrections = compute_per_office_correction(
        y_val_np, y_pred_val_raw, meta_val['route_id'].values, train_df
    )

    print(f"  Correction factors ({len(corrections)} offices):")
    for office_id in sorted(corrections.keys())[:10]:
        print(f"    office {office_id}: {corrections[office_id]:.4f}")
    if len(corrections) > 10:
        print(f"    ... and {len(corrections) - 10} more")

    y_pred_val_corrected = apply_per_office_correction(
        y_pred_val_raw, meta_val['route_id'].values, corrections, train_df
    )
    score_corrected = metric.calculate(y_val_np, y_pred_val_corrected)

    print(f"\n  Corrected CV: {score_corrected:.6f}")
    print(f"  Improvement: {score_raw - score_corrected:+.6f}")

    print("\n[5/5] Also trying global scale factors for comparison...")
    for scale in [0.97, 0.98, 0.99, 1.00, 1.01, 1.02, 1.03, 1.04, 1.05]:
        y_scaled = np.clip(y_pred_val_raw * scale, 0, None)
        s = metric.calculate(y_val_np, y_scaled)
        marker = " <-- best" if scale == 1.0 else ""
        print(f"  scale={scale:.2f} -> CV={s:.6f}{marker}")

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.0f}s")
    print(f"RESULT: exp_005 | raw_CV={score_raw:.6f} | corrected_CV={score_corrected:.6f}")


if __name__ == '__main__':
    main()
