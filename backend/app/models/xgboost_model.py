import io
import logging
import pickle

import numpy as np

from app.config import INPUT_WINDOW, OUTPUT_WINDOW, NUM_FEATURES
from app.models.base import BaseWeatherModel

logger = logging.getLogger(__name__)

try:
    import xgboost as xgb
except ImportError:
    xgb = None
    logger.warning("xgboost not installed – XGBoost model unavailable")


class WeatherXGBoost(BaseWeatherModel):
    """
    XGBoost model for weather forecasting.

    Strategy:
    - Flatten the input window into a 1-D feature vector with engineered features
      (lag values, rolling mean/std, time features already included in data).
    - Train one XGBoost regressor per output feature.
    - For multi-step prediction, predict the full output window directly
      (one model per feature, predicting OUTPUT_WINDOW steps at once
      via MultiOutputRegressor-style approach using XGBoost's built-in
      multi-output or chained single-output models).

    For simplicity and speed, we use a *direct* strategy:
    each model predicts all OUTPUT_WINDOW steps for one feature at once.
    """

    model_type: str = "xgboost"

    def __init__(
        self,
        num_features: int = NUM_FEATURES,
        input_window: int = INPUT_WINDOW,
        output_window: int = OUTPUT_WINDOW,
    ):
        if xgb is None:
            raise ImportError("xgboost package is required for WeatherXGBoost")
        self.num_features = num_features
        self.input_window = input_window
        self.output_window = output_window
        self.models: list[xgb.XGBRegressor | None] = [None] * num_features

    def _engineer_features(self, X: np.ndarray) -> np.ndarray:
        """
        Convert 3-D windows (N, input_window, num_features) into 2-D
        feature matrix (N, engineered_features).

        Features per sample:
        - Last 48 hours raw values (48 * num_features)
        - Rolling mean over 24h, 72h, 168h per feature (3 * num_features)
        - Rolling std over 24h per feature (num_features)
        - Min/max over full window per feature (2 * num_features)
        - Trend (last 24h mean - first 24h mean) per feature (num_features)
        """
        N = X.shape[0]
        feats_list = []

        # Last 48 hours of raw values (most recent context)
        tail = min(48, self.input_window)
        feats_list.append(X[:, -tail:, :].reshape(N, -1))

        # Rolling means
        for window in [24, 72, 168]:
            w = min(window, self.input_window)
            feats_list.append(X[:, -w:, :].mean(axis=1))

        # Rolling std (24h)
        w24 = min(24, self.input_window)
        feats_list.append(X[:, -w24:, :].std(axis=1))

        # Min / Max over full window
        feats_list.append(X.min(axis=1))
        feats_list.append(X.max(axis=1))

        # Trend: mean of last 24h - mean of first 24h
        first_24 = X[:, :min(24, self.input_window), :].mean(axis=1)
        last_24 = X[:, -min(24, self.input_window):, :].mean(axis=1)
        feats_list.append(last_24 - first_24)

        return np.hstack(feats_list)

    def fit(
        self,
        X_train: np.ndarray,
        Y_train: np.ndarray,
        X_test: np.ndarray,
        Y_test: np.ndarray,
    ) -> list[dict]:
        X_tr = self._engineer_features(X_train)
        X_te = self._engineer_features(X_test)

        history = []
        for feat_idx in range(self.num_features):
            # Target: flatten output_window for this feature
            y_tr = Y_train[:, :, feat_idx]  # (N, output_window)
            y_te = Y_test[:, :, feat_idx]

            # Use GPU if available (XGBoost 2.0+ native CUDA support)
            import torch
            use_gpu = torch.cuda.is_available()
            model = xgb.XGBRegressor(
                n_estimators=300,
                max_depth=8,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                min_child_weight=3,
                reg_alpha=0.1,
                reg_lambda=1.0,
                tree_method="hist",
                device="cuda" if use_gpu else "cpu",
                n_jobs=-1,
                verbosity=0,
            )
            if use_gpu and feat_idx == 0:
                logger.info("XGBoost using GPU (CUDA)")

            # XGBoost multi-output: fit with (N, output_window) target
            model.fit(
                X_tr, y_tr,
                eval_set=[(X_te, y_te)],
                verbose=False,
            )

            self.models[feat_idx] = model

            # Compute test loss
            pred_te = model.predict(X_te)
            mse = float(np.mean((pred_te - y_te) ** 2))
            history.append({
                "feature_idx": feat_idx,
                "test_mse": round(mse, 6),
            })
            logger.info("XGBoost feature %d: test_mse=%.6f", feat_idx, mse)

        return [{"epoch": 1, "train_loss": 0.0, "test_loss": np.mean([h["test_mse"] for h in history])}]

    def predict(self, input_window: np.ndarray) -> np.ndarray:
        """input_window: (input_window, num_features) -> (output_window, num_features)"""
        X = self._engineer_features(input_window[np.newaxis, :, :])  # (1, feats)
        result = np.zeros((self.output_window, self.num_features), dtype=np.float32)
        for feat_idx, model in enumerate(self.models):
            if model is None:
                continue
            pred = model.predict(X)  # (1, output_window)
            result[:, feat_idx] = pred[0]
        return result

    def save_bytes(self) -> bytes:
        buf = io.BytesIO()
        pickle.dump({
            "models": self.models,
            "num_features": self.num_features,
            "input_window": self.input_window,
            "output_window": self.output_window,
        }, buf)
        return buf.getvalue()

    def load_bytes(self, data: bytes) -> None:
        payload = pickle.loads(data)
        self.models = payload["models"]
        self.num_features = payload["num_features"]
        self.input_window = payload["input_window"]
        self.output_window = payload["output_window"]
