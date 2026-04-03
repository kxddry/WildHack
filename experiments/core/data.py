import numpy as np
import pandas as pd

from core.features import TimeSeriesFeatureBuilder


class DatasetBuilder:
    def __init__(self, train: pd.DataFrame, test: pd.DataFrame, config: str = 'team'):
        train = train.sort_values(['route_id', 'timestamp']).reset_index(drop=True)
        test = test.sort_values(['route_id', 'timestamp']).reset_index(drop=True)

        self.forecast_points = 10 if config == 'team' else 8
        self.future_target_cols = [f'target_2h_step_{step}' for step in range(1, self.forecast_points + 1)]

        self.status_features = [f'status_{i}' for i in range(1, 9)]
        self.target = 'target_2h'

        self.holiday_dates = pd.to_datetime([
            '2025-05-01', '2025-05-02', '2025-05-08', '2025-05-09'
        ])

        self.base_numeric_features = [
            'total_inventory', 'status_early', 'status_mid', 'status_late',
            'early_inventory', 'mid_inventory', 'late_inventory',
            'early_share', 'mid_share', 'late_share',
            'status_entropy', 'horizon_minutes'
        ] + [f'status_{i}_share' for i in range(1, 9)]

        self.cat_features = [
            'office_from_id', 'route_id', 'dow', 'pod',
            'is_hooliday', 'slot', 'horizon_step'
        ]
        self.numeric_features = []

        self.train = self._preprocess_df(train)
        prep_test = self._preprocess_df(test)
        self.test = self._restore_test_df(prep_test)

    def _register_numeric_features(self, new_numeric_features: list[str]) -> None:
        self.numeric_features = list(dict.fromkeys(self.numeric_features + new_numeric_features))

    def _get_part_of_day(self, hour: int) -> str:
        if 0 <= hour < 6:
            return 'night'
        if 6 <= hour < 12:
            return 'morning'
        if 12 <= hour < 18:
            return 'day'
        return 'evening'

    def _add_time_features(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df['dow'] = df['timestamp'].dt.day_name()
        df['pod'] = df['timestamp'].dt.hour.map(lambda x: self._get_part_of_day(x))
        df['slot'] = df['timestamp'].dt.hour * 2 + df['timestamp'].dt.minute // 30
        df['is_hooliday'] = df['timestamp'].dt.normalize().isin(self.holiday_dates).astype(int)
        return df

    def _add_total_status_features(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        available_status_features = [f for f in self.status_features if f in df.columns]
        if len(available_status_features) == 0:
            return df

        df['total_inventory'] = df[available_status_features].sum(axis=1)

        for status in available_status_features:
            df[f'{status}_share'] = df[status] / (df['total_inventory'] + 1e-8)

        early_cols = [f for f in ['status_1', 'status_2', 'status_3'] if f in df.columns]
        mid_cols = [f for f in ['status_4', 'status_5', 'status_6'] if f in df.columns]
        late_cols = [f for f in ['status_7', 'status_8'] if f in df.columns]

        df['early_inventory'] = df[early_cols].sum(axis=1) if len(early_cols) > 0 else 0.0
        df['mid_inventory'] = df[mid_cols].sum(axis=1) if len(mid_cols) > 0 else 0.0
        df['late_inventory'] = df[late_cols].sum(axis=1) if len(late_cols) > 0 else 0.0

        df['status_early'] = df['early_inventory']
        df['status_mid'] = df['mid_inventory']
        df['status_late'] = df['late_inventory']

        df['early_share'] = df['early_inventory'] / (df['total_inventory'] + 1e-8)
        df['mid_share'] = df['mid_inventory'] / (df['total_inventory'] + 1e-8)
        df['late_share'] = df['late_inventory'] / (df['total_inventory'] + 1e-8)

        share_cols = [f'{status}_share' for status in available_status_features]
        shares = df[share_cols].to_numpy()
        df['status_entropy'] = -np.sum(shares * np.log(shares + 1e-8), axis=1) / np.log(len(available_status_features))

        new_numeric_features = (
            ['total_inventory']
            + [f'{status}_share' for status in available_status_features]
            + ['early_inventory', 'mid_inventory', 'late_inventory']
            + ['status_early', 'status_mid', 'status_late']
            + ['early_share', 'mid_share', 'late_share']
            + ['status_entropy']
        )
        self._register_numeric_features(new_numeric_features)
        return df

    def _preprocess_df(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = self._add_time_features(df)
        df = self._add_total_status_features(df)
        return df

    def _restore_test_df(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if 'office_from_id' in df.columns:
            return df
        route_to_office = self.train.groupby('route_id')['office_from_id'].first().to_dict()
        df['office_from_id'] = df['route_id'].map(route_to_office)
        return df

    def _make_future_target(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        route_group = df.groupby('route_id', sort=False)
        for step in range(1, self.forecast_points + 1):
            df[f'target_2h_step_{step}'] = route_group[self.target].shift(-step)
        return df

    def _agg_stats_by_group_keys(
        self,
        stats_source: pd.DataFrame,
        group_keys: list[str],
        agg_features: list[str],
        statistics: tuple[str, ...]
    ) -> tuple[pd.DataFrame, list[str]]:
        train = stats_source.copy()
        agg_features = [f for f in agg_features if f in train.columns]
        base_df = train[group_keys].drop_duplicates().copy()

        if len(agg_features) == 0:
            return base_df, group_keys

        blocks = []
        group_name = '_and_'.join(group_keys)

        for stat_name in ['mean', 'median', 'std']:
            if stat_name in statistics:
                block = train.groupby(group_keys)[agg_features].agg(stat_name).reset_index()
                block = block.rename(columns={f: f'{f}_agg_by_{group_name}_{stat_name}' for f in agg_features})
                blocks.append(block)

        for block in blocks:
            base_df = base_df.merge(block, how='left', on=group_keys)

        new_numeric_features = [col for col in base_df.columns if col not in group_keys]
        self._register_numeric_features(new_numeric_features)
        return base_df, group_keys

    def _add_default_ts_features(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        ts_builder = TimeSeriesFeatureBuilder(df=df, route_col='route_id', time_col='timestamp')

        target_features = [self.target]
        inventory_features = [f for f in ['total_inventory', 'status_early', 'status_mid', 'status_late'] if f in df.columns]
        detailed_status_features = [f for f in self.status_features if f in df.columns]

        ts_builder.add_lag_features(features=target_features, lags=list(range(1, 11)))
        ts_builder.add_diff_features(features=target_features, periods=list(range(1, 11)) + [15, 20, 48, 96])
        ts_builder.add_rolling_features(
            features=target_features,
            windows=[3, 6, 12, 24, 48, 96, 144, 288],
            statistics=('mean', 'std', 'max', 'min')
        )

        if len(inventory_features) > 0:
            ts_builder.add_lag_features(features=inventory_features, lags=list(range(1, 11)))
            ts_builder.add_diff_features(features=inventory_features, periods=list(range(1, 11)) + [15, 20, 48, 96])
            ts_builder.add_rolling_features(
                features=inventory_features,
                windows=[3, 6, 12, 24, 48, 96, 144, 288],
                statistics=('mean', 'std')
            )

        if len(detailed_status_features) > 0:
            ts_builder.add_lag_features(features=detailed_status_features, lags=[1, 2, 3, 6, 12, 18, 36, 96])

        df_with_ts, ts_features = ts_builder.get_result()
        self._register_numeric_features(ts_features)
        return df_with_ts

    def make_static_features(
        self,
        train_df: pd.DataFrame,
        test_df: pd.DataFrame,
        stats_source: pd.DataFrame = None,
        statistics: tuple[str, ...] = ('mean', 'std'),
        agg_features: list[str] = None,
        group_keys_list: list[list[str]] = None
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        train_df = train_df.copy()
        test_df = test_df.copy()

        if stats_source is None:
            stats_source = train_df.copy()
        else:
            stats_source = stats_source.copy()

        if agg_features is None:
            agg_features = self.status_features.copy()

        if group_keys_list is None:
            group_keys_list = [
                ['route_id'], ['office_from_id'], ['dow'], ['pod'],
                ['route_id', 'dow'], ['route_id', 'pod']
            ]

        for group_keys in group_keys_list:
            stats_df, merge_keys = self._agg_stats_by_group_keys(
                stats_source=stats_source, group_keys=group_keys,
                agg_features=agg_features, statistics=statistics
            )
            train_df = train_df.merge(stats_df, how='left', on=merge_keys)
            test_df = test_df.merge(stats_df, how='left', on=merge_keys)

        return train_df, test_df

    def make_total_inventory_aggs(
        self,
        train_df: pd.DataFrame,
        test_df: pd.DataFrame,
        stats_source: pd.DataFrame = None,
        statistics: tuple[str, ...] = ('mean', 'std'),
        agg_features: list[str] = None,
        group_keys_list: list[list[str]] = None
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        train_df = train_df.copy()
        test_df = test_df.copy()

        if stats_source is None:
            stats_source = train_df.copy()
        else:
            stats_source = stats_source.copy()

        train_df = self._add_total_status_features(train_df)
        test_df = self._add_total_status_features(test_df)
        stats_source = self._add_total_status_features(stats_source)

        if agg_features is None:
            agg_features = ['total_inventory']

        if group_keys_list is None:
            group_keys_list = [
                ['route_id'], ['office_from_id'],
                ['route_id', 'dow'], ['route_id', 'pod'], ['route_id', 'slot']
            ]

        for group_keys in group_keys_list:
            stats_df, merge_keys = self._agg_stats_by_group_keys(
                stats_source=stats_source, group_keys=group_keys,
                agg_features=agg_features, statistics=statistics
            )
            train_df = train_df.merge(stats_df, how='left', on=merge_keys)
            test_df = test_df.merge(stats_df, how='left', on=merge_keys)

        return train_df, test_df

    def _add_target_mean_hist(
        self, df: pd.DataFrame, static_group_keys_list: list[list[str]] = None
    ) -> pd.DataFrame:
        if static_group_keys_list is None:
            static_group_keys_list = [['route_id'], ['route_id', 'pod'], ['route_id', 'dow']]

        df = df.sort_values(['timestamp']).copy()
        global_mean = df[self.target].mean()

        for group_keys in static_group_keys_list:
            feature_name = '_'.join(group_keys) + '_target_mean_hist'
            self._register_numeric_features([feature_name])
            group_cum_sum = df.groupby(group_keys)[self.target].cumsum() - df[self.target]
            group_cum_count = df.groupby(group_keys).cumcount()
            df[feature_name] = group_cum_sum / group_cum_count
            df[feature_name] = df[feature_name].fillna(global_mean)

        return df

    def _add_target_std_hist(
        self, df: pd.DataFrame, static_group_keys_list: list[list[str]] = None
    ) -> pd.DataFrame:
        if static_group_keys_list is None:
            static_group_keys_list = [['route_id'], ['route_id', 'pod'], ['route_id', 'dow']]

        df = df.sort_values(['timestamp']).copy()

        for group_keys in static_group_keys_list:
            feature_name = '_'.join(group_keys) + '_target_std_hist'
            self._register_numeric_features([feature_name])
            group_cum_sum = df.groupby(group_keys)[self.target].cumsum() - df[self.target]
            group_cum_sum_sq = df.groupby(group_keys)[self.target].transform(lambda s: (s ** 2).cumsum()) - df[self.target] ** 2
            group_cum_count = df.groupby(group_keys).cumcount()
            group_mean_hist = group_cum_sum / group_cum_count
            group_mean_sq_hist = group_cum_sum_sq / group_cum_count
            group_var_hist = (group_mean_sq_hist - group_mean_hist ** 2).clip(lower=0)
            df[feature_name] = np.sqrt(group_var_hist)
            df[feature_name] = df[feature_name].fillna(0)

        return df

    def _add_target_zero_rate_hist(
        self, df: pd.DataFrame, static_group_keys_list: list[list[str]] = None
    ) -> pd.DataFrame:
        if static_group_keys_list is None:
            static_group_keys_list = [['route_id'], ['route_id', 'pod'], ['route_id', 'dow']]

        df = df.sort_values(['timestamp']).copy()
        is_zero = (df[self.target] == 0).astype(int)

        for group_keys in static_group_keys_list:
            feature_name = '_'.join(group_keys) + '_target_zero_rate_hist'
            self._register_numeric_features([feature_name])
            group_zero_cum_sum = is_zero.groupby([df[key] for key in group_keys]).cumsum() - is_zero
            group_cum_count = df.groupby(group_keys).cumcount()
            df[feature_name] = group_zero_cum_sum / group_cum_count
            df[feature_name] = df[feature_name].fillna(0)

        return df

    def _add_target_count_hist(
        self, df: pd.DataFrame, static_group_keys_list: list[list[str]] = None
    ) -> pd.DataFrame:
        if static_group_keys_list is None:
            static_group_keys_list = [['route_id'], ['route_id', 'pod'], ['route_id', 'dow']]

        df = df.sort_values(['timestamp']).copy()

        for group_keys in static_group_keys_list:
            feature_name = '_'.join(group_keys) + '_target_count_hist'
            self._register_numeric_features([feature_name])
            df[feature_name] = df.groupby(group_keys).cumcount()

        return df

    def _expand_anchors_to_long(self, df: pd.DataFrame, include_target: bool = True) -> pd.DataFrame:
        df = df.copy()
        blocks = []

        for step in range(1, self.forecast_points + 1):
            block = df.copy()
            block['anchor_timestamp'] = block['timestamp']
            block['horizon_step'] = step
            block['horizon_minutes'] = step * 30
            block['timestamp'] = block['anchor_timestamp'] + pd.Timedelta(minutes=30 * step)
            block = self._add_time_features(block)

            if include_target:
                block[self.target] = block[f'target_2h_step_{step}']
            else:
                drop_cols = [col for col in [self.target] + self.future_target_cols if col in block.columns]
                if len(drop_cols) > 0:
                    block = block.drop(columns=drop_cols)

            blocks.append(block)

        return pd.concat(blocks, axis=0, ignore_index=True)

    def _get_test_anchor_df(self, source_df: pd.DataFrame = None) -> pd.DataFrame:
        if source_df is None:
            source_df = self.train.copy()
        return source_df.sort_values(['route_id', 'timestamp']).groupby('route_id').tail(1).copy()

    def _get_feature_cols(self, df: pd.DataFrame) -> list[str]:
        excluded_cols = ['id', 'timestamp', 'anchor_timestamp', self.target] + self.future_target_cols
        feature_cols = self.cat_features + self.status_features + self.numeric_features
        return [f for f in dict.fromkeys(feature_cols) if f in df.columns and f not in excluded_cols]

    def _fill_numeric_na(
        self, X_train: pd.DataFrame, X_test: pd.DataFrame, feature_cols: list[str]
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        numeric_cols = [f for f in feature_cols if f not in self.cat_features]
        if len(numeric_cols) == 0:
            return X_train, X_test
        fill_values = X_train[numeric_cols].median()
        X_train[numeric_cols] = X_train[numeric_cols].fillna(fill_values)
        X_test[numeric_cols] = X_test[numeric_cols].fillna(fill_values)
        return X_train, X_test

    def _encode_cat_features(
        self, train_df: pd.DataFrame, test_df: pd.DataFrame
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        cat_features = [col for col in self.cat_features if col in train_df.columns and col in test_df.columns]
        train_df = train_df.copy()
        test_df = test_df.copy()

        full = pd.concat([train_df[cat_features], test_df[cat_features]], axis=0)
        full_ohe = pd.get_dummies(full, columns=cat_features, dummy_na=False)

        train_ohe = full_ohe.iloc[:len(train_df)].reset_index(drop=True).astype(int)
        test_ohe = full_ohe.iloc[len(train_df):].reset_index(drop=True).astype(int)

        train_df = train_df.drop(cat_features, axis=1).reset_index(drop=True)
        test_df = test_df.drop(cat_features, axis=1).reset_index(drop=True)

        train_df = pd.concat([train_df, train_ohe], axis=1)
        test_df = pd.concat([test_df, test_ohe], axis=1)
        return train_df, test_df

    def _cut_history_with_buffer(
        self, df: pd.DataFrame, train_days: int, max_lag: int = 96, max_window: int = 288
    ) -> pd.DataFrame:
        df = df.copy()
        buffer_points = max(max_lag, max_window, self.forecast_points)
        buffer_minutes = buffer_points * 30
        total_days_back = pd.Timedelta(days=train_days) + pd.Timedelta(minutes=buffer_minutes)
        min_ts = df['timestamp'].max() - total_days_back
        return df[df['timestamp'] >= min_ts]

    def build_train_test(
        self,
        train_days: int = 14,
        return_y_test: bool = False,
        return_meta_test: bool = False,
        use_static_aggs: bool = False,
        use_total_status_features: bool = False,
        use_total_inventory_aggs: bool = False,
        use_target_mean_hist: bool = False,
        use_target_std_hist: bool = False,
        use_target_zero_rate_hist: bool = False,
        use_target_count_hist: bool = False,
        use_default_ts_features: bool = True,
        static_agg_features: list[str] = None,
        total_inventory_agg_features: list[str] = None,
        static_group_keys_list: list[list[str]] = None,
        total_inventory_group_keys_list: list[list[str]] = None,
        target_hist_group_keys_list: list[list[str]] = None,
        encode_cat_features: bool = False,
        statistics: tuple[str, ...] = ('mean', 'std')
    ):
        feature_history = self.train.copy()

        if train_days is not None:
            feature_history = self._cut_history_with_buffer(
                feature_history, train_days=train_days, max_lag=96, max_window=288
            )

        if use_default_ts_features:
            feature_history = self._add_default_ts_features(feature_history)

        if use_target_mean_hist:
            feature_history = self._add_target_mean_hist(
                feature_history, static_group_keys_list=target_hist_group_keys_list
            )

        if use_target_std_hist:
            feature_history = self._add_target_std_hist(
                feature_history, static_group_keys_list=target_hist_group_keys_list
            )

        if use_target_zero_rate_hist:
            feature_history = self._add_target_zero_rate_hist(
                feature_history, static_group_keys_list=target_hist_group_keys_list
            )

        if use_target_count_hist:
            feature_history = self._add_target_count_hist(
                feature_history, static_group_keys_list=target_hist_group_keys_list
            )

        train_anchor_df = self._make_future_target(feature_history)
        mask = train_anchor_df[self.future_target_cols].notna().all(axis=1)
        train_anchor_df = train_anchor_df.loc[mask]

        if train_days is not None:
            train_window_start = train_anchor_df['timestamp'].max() - pd.Timedelta(days=train_days)
            train_anchor_df = train_anchor_df[train_anchor_df['timestamp'] >= train_window_start].copy()

        test_anchor_df = self._get_test_anchor_df(source_df=feature_history)

        if use_total_status_features:
            train_anchor_df = self._add_total_status_features(train_anchor_df)
            test_anchor_df = self._add_total_status_features(test_anchor_df)

        if use_static_aggs:
            train_anchor_df, test_anchor_df = self.make_static_features(
                train_df=train_anchor_df, test_df=test_anchor_df,
                stats_source=feature_history, statistics=statistics,
                agg_features=static_agg_features, group_keys_list=static_group_keys_list
            )

        if use_total_inventory_aggs:
            train_anchor_df, test_anchor_df = self.make_total_inventory_aggs(
                train_df=train_anchor_df, test_df=test_anchor_df,
                stats_source=feature_history, statistics=statistics,
                agg_features=total_inventory_agg_features,
                group_keys_list=total_inventory_group_keys_list
            )

        train_long = self._expand_anchors_to_long(train_anchor_df, include_target=True)
        test_long = self._expand_anchors_to_long(test_anchor_df, include_target=False)

        test_merge_cols = ['route_id', 'timestamp']
        available_test_cols = test_merge_cols.copy()
        if 'id' in self.test.columns:
            available_test_cols.append('id')
        if self.target in self.test.columns:
            available_test_cols.append(self.target)

        test_long = test_long.merge(self.test[available_test_cols], how='left', on=test_merge_cols)

        feature_cols = self._get_feature_cols(train_long)
        X_train = train_long[feature_cols].copy()
        y_train = train_long[self.target].copy()
        X_test = test_long[feature_cols].copy()

        X_train, X_test = self._fill_numeric_na(X_train, X_test, feature_cols)

        if encode_cat_features:
            X_train, X_test = self._encode_cat_features(X_train, X_test)

        if return_y_test and self.target in test_long.columns:
            y_test = test_long[self.target].copy()
            if return_meta_test:
                meta_test = test_long[['route_id', 'timestamp', 'horizon_step']].copy()
                if 'id' in test_long.columns:
                    meta_test['id'] = test_long['id'].values
                return X_train, y_train, X_test, y_test, meta_test
            return X_train, y_train, X_test, y_test

        if return_meta_test:
            meta_test = test_long[['route_id', 'timestamp', 'horizon_step']].copy()
            if 'id' in test_long.columns:
                meta_test['id'] = test_long['id'].values
            return X_train, y_train, X_test, meta_test

        return X_train, y_train, X_test

    def make_submission_from_long_preds(
        self, y_pred_test: np.ndarray, meta_test: pd.DataFrame
    ) -> pd.DataFrame:
        sub = meta_test.copy()
        if 'id' not in sub.columns:
            raise ValueError('meta_test has no id column')
        sub['y_pred'] = np.clip(y_pred_test, 0, None)
        return sub[['id', 'y_pred']].sort_values('id').reset_index(drop=True)


class OOTValidator:
    def __init__(self, df: pd.DataFrame, builder_cls=DatasetBuilder, config: str = 'team'):
        self.df = df.sort_values('timestamp').reset_index(drop=True)
        self.builder_cls = builder_cls
        self.config = config

    def make_oot_split(self, val_points: int = 10, **build_kwargs):
        df = self.df.copy()
        last_date = df['timestamp'].max()
        cutoff_date = last_date - pd.Timedelta(minutes=val_points * 30)

        train_part = df[df['timestamp'] < cutoff_date].copy()
        val_part = df[df['timestamp'] >= cutoff_date].copy()

        print(f'train: {train_part["timestamp"].min()} -> {train_part["timestamp"].max()}')
        print(f'val:   {val_part["timestamp"].min()} -> {val_part["timestamp"].max()}')

        builder = self.builder_cls(train=train_part, test=val_part, config=self.config)
        self.builder = builder

        X_train, y_train, X_val, y_val = builder.build_train_test(return_y_test=True, **build_kwargs)
        return X_train, y_train, X_val, y_val
