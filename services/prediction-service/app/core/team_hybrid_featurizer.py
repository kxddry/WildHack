"""Team Hybrid featurizer — builds features for hybrid LightGBM at inference time.

Wraps team_pipeline.DatasetBuilder to produce the same feature set the hybrid
model was trained on. Called per-route from the /predict endpoint.
"""

import logging
import pandas as pd
from team_pipeline.data import DatasetBuilder
from team_pipeline.kaggle_features import add_kaggle_features, get_extra_names, BUILD_KWARGS

logger = logging.getLogger(__name__)


class TeamHybridFeaturizer:
    """Builds inference features using the team DatasetBuilder pipeline."""

    def __init__(
        self,
        feat_cols_step: list[str],
        feat_cols_global: list[str],
        cat_cols: list[str],
    ):
        self._feat_cols_step = feat_cols_step
        self._feat_cols_global = feat_cols_global
        self._cat_cols = cat_cols

    def build(
        self,
        history_df: pd.DataFrame,
        anchor_ts,
        route_id: int,
        warehouse_id: int,
        forecast_steps: int = 10,
    ) -> pd.DataFrame:
        """Build a (forecast_steps, n_features) DataFrame for prediction.

        history_df must already contain the current observation as its last row
        (routes.py appends it before calling build). It should have columns:
        timestamp, route_id, office_from_id, status_1..8, target_2h.

        The anchor is determined by DatasetBuilder._get_test_anchor_df which
        takes the last row per route from the training frame — since history_df
        has the current observation as the last row, this naturally becomes the
        prediction anchor.
        """
        # 1. Apply kaggle features to the full history
        history = history_df.copy()
        # Ensure target_2h exists (it may be 0.0 for current observation)
        if 'target_2h' not in history.columns:
            history['target_2h'] = 0.0

        history = add_kaggle_features(history)
        extra_feats = get_extra_names(history)

        # 2. Build empty test metadata frame — DatasetBuilder only uses test
        #    to join 'id' and 'target_2h' columns (data.py lines 494-501).
        #    We don't have either at inference time.
        test_df = pd.DataFrame({
            'route_id': pd.Series(dtype='int64'),
            'timestamp': pd.Series(dtype='datetime64[ns]'),
        })

        # 3. DatasetBuilder(train=history, test=empty) — _get_test_anchor_df
        #    picks the last row per route from self.train (= history with
        #    current observation as last row).
        builder = DatasetBuilder(train=history, test=test_df, config='team')

        # 4. Build features with the same kwargs used during training
        build_kwargs = {k: v for k, v in BUILD_KWARGS.items() if k != 'train_days'}
        result = builder.build_train_test(
            train_days=None,  # history already windowed by the DB query
            extra_numeric_features=extra_feats,
            **build_kwargs,
        )
        # result = (X_train, y_train, X_test) — we only need X_test
        X_test = result[2]

        if len(X_test) == 0:
            logger.warning(
                "TeamHybridFeaturizer produced 0 rows for route_id=%d — "
                "history may be too short (%d rows)",
                route_id, len(history_df),
            )
            # Return an empty frame with the right columns so the caller
            # can handle it gracefully (model will produce zeros).
            return pd.DataFrame(columns=self._feat_cols_global)

        # 5. Cast categoricals
        for c in self._cat_cols:
            if c in X_test.columns:
                X_test[c] = X_test[c].astype('category')

        logger.debug(
            "Built features for route_id=%d: %d rows x %d cols",
            route_id, len(X_test), len(X_test.columns),
        )
        return X_test
