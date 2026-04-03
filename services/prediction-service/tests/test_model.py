"""Unit tests for ModelManager."""

import numpy as np
import pandas as pd
import pytest

from app.core.model import ModelManager


class TestModelManagerLoad:
    def test_load_missing_file(self, tmp_path):
        """Loading a non-existent file raises FileNotFoundError."""
        manager = ModelManager()
        with pytest.raises(FileNotFoundError, match="Model file not found"):
            manager.load(str(tmp_path / "nonexistent_model.pkl"))

    def test_is_loaded_false_before_load(self):
        manager = ModelManager()
        assert manager.is_loaded is False


class TestModelManagerPredictWithoutLoad:
    def test_predict_without_load(self):
        """Calling predict before load raises RuntimeError."""
        manager = ModelManager()
        dummy_df = pd.DataFrame({"f1": [1.0], "f2": [2.0]})
        with pytest.raises(RuntimeError, match="Model is not loaded"):
            manager.predict(dummy_df)


class TestModelManagerInfoWithoutLoad:
    def test_info_without_load(self):
        """Calling info before load raises RuntimeError."""
        manager = ModelManager()
        with pytest.raises(RuntimeError, match="Model is not loaded"):
            manager.info()


class TestModelManagerWithMockedModel:
    def test_predict_clips_negative_values(self, mock_model):
        """predict() must clip negative values to 0."""
        import numpy as np

        mock_model.predict = lambda df: np.array([-5.0, 3.0, -0.1, 10.0])

        manager = ModelManager()
        manager._model = mock_model

        df = pd.DataFrame({"f": [1, 2, 3, 4]})
        result = manager.predict(df)

        assert all(result >= 0), f"Expected all predictions >= 0, got {result}"
        assert result[1] == pytest.approx(3.0)
        assert result[3] == pytest.approx(10.0)

    def test_predict_returns_ndarray(self, mock_model):
        """predict() returns a numpy ndarray."""
        manager = ModelManager()
        manager._model = mock_model

        df = pd.DataFrame({"f": range(10)})
        result = manager.predict(df)
        assert isinstance(result, np.ndarray)

    def test_is_loaded_true_after_model_set(self, mock_model):
        manager = ModelManager()
        manager._model = mock_model
        assert manager.is_loaded is True

    def test_info_returns_expected_keys(self, mock_model):
        """info() returns a dict with required keys when model is loaded."""
        manager = ModelManager()
        manager._model = mock_model
        manager._model_path = "/some/path/model.pkl"
        manager._metadata = {"cv_score": 0.95, "training_date": "2025-01-01"}

        info = manager.info()

        assert "model_type" in info
        assert "feature_count" in info
        assert "feature_names" in info
        assert "n_estimators_fitted" in info
        assert info["feature_count"] == 5
        assert info["feature_names"] == ["f1", "f2", "f3", "f4", "f5"]
        assert info["cv_score"] == 0.95
