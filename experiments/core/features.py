import pandas as pd


class TimeSeriesFeatureBuilder:
    def __init__(self, df: pd.DataFrame, route_col: str = 'route_id', time_col: str = 'timestamp'):
        self.df = df.sort_values([route_col, time_col]).copy()
        self.route_col = route_col
        self.time_col = time_col
        self.created_features = []

    def _register_features(self, new_features: list[str]) -> None:
        self.created_features.extend([f for f in new_features if f not in self.created_features])

    def add_lag_features(self, features: list[str], lags: list[int]) -> pd.DataFrame:
        df = self.df.copy()
        route_group = df.groupby(self.route_col, sort=False)
        new_features = []

        for feature in features:
            if feature not in df.columns:
                continue
            for lag in lags:
                lag_col = f'{feature}_lag_{lag}'
                df[lag_col] = route_group[feature].shift(lag)
                new_features.append(lag_col)

        self.df = df
        self._register_features(new_features)
        return self.df

    def add_diff_features(self, features: list[str], periods: list[int]) -> pd.DataFrame:
        df = self.df.copy()
        route_group = df.groupby(self.route_col, sort=False)
        new_features = []

        for feature in features:
            if feature not in df.columns:
                continue
            for period in periods:
                col = f'{feature}_diff_{period}'
                df[col] = df[feature] - route_group[feature].shift(period)
                new_features.append(col)

        self.df = df
        self._register_features(new_features)
        return self.df

    def add_rolling_features(
        self,
        features: list[str],
        windows: list[int],
        statistics: tuple[str, ...] = ('mean', 'std')
    ) -> pd.DataFrame:
        df = self.df.copy()
        new_features = []

        for feature in features:
            if feature not in df.columns:
                continue

            shifted = df.groupby(self.route_col, sort=False)[feature].shift(1)

            for window in windows:
                grouped_shifted = shifted.groupby(df[self.route_col], sort=False)

                if 'mean' in statistics:
                    col = f'{feature}_roll_{window}_mean'
                    df[col] = grouped_shifted.transform(lambda s: s.rolling(window=window, min_periods=1).mean())
                    new_features.append(col)

                if 'std' in statistics:
                    col = f'{feature}_roll_{window}_std'
                    df[col] = grouped_shifted.transform(lambda s: s.rolling(window=window, min_periods=2).std())
                    df[col] = df[col].fillna(0)
                    new_features.append(col)

                if 'max' in statistics:
                    col = f'{feature}_roll_{window}_max'
                    df[col] = grouped_shifted.transform(lambda s: s.rolling(window=window, min_periods=1).max())
                    new_features.append(col)

                if 'min' in statistics:
                    col = f'{feature}_roll_{window}_min'
                    df[col] = grouped_shifted.transform(lambda s: s.rolling(window=window, min_periods=1).min())
                    new_features.append(col)

        self.df = df
        self._register_features(new_features)
        return self.df

    def get_result(self) -> tuple[pd.DataFrame, list[str]]:
        return self.df.copy(), self.created_features.copy()
