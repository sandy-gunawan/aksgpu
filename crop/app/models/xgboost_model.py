import io
import logging
import pickle

import numpy as np

from app.config import INPUT_WINDOW, OUTPUT_WINDOW, NUM_FEATURES
from app.models.base import BaseCropModel

logger = logging.getLogger(__name__)

try:
    import xgboost as xgb
except ImportError:
    xgb = None


class CropXGBoost(BaseCropModel):
    """XGBoost model for crop health prediction. One regressor per output feature."""

    model_type: str = "xgboost"

    def __init__(self, num_features: int = NUM_FEATURES,
                 input_window: int = INPUT_WINDOW, output_window: int = OUTPUT_WINDOW):
        if xgb is None:
            raise ImportError("xgboost required")
        self.num_features = num_features
        self.input_window = input_window
        self.output_window = output_window
        self.models: list = [None] * num_features

    def _engineer_features(self, X: np.ndarray) -> np.ndarray:
        N = X.shape[0]
        feats = []
        # Last 14 days raw (daily data, not hourly — so smaller tail)
        tail = min(14, self.input_window)
        feats.append(X[:, -tail:, :].reshape(N, -1))
        # Rolling means over 7, 14, 30 days
        for w in [7, 14, 30]:
            w = min(w, self.input_window)
            feats.append(X[:, -w:, :].mean(axis=1))
        # Rolling std 7 days
        w7 = min(7, self.input_window)
        feats.append(X[:, -w7:, :].std(axis=1))
        # Min / Max
        feats.append(X.min(axis=1))
        feats.append(X.max(axis=1))
        # Trend: last 7d mean - first 7d mean
        first = X[:, :min(7, self.input_window), :].mean(axis=1)
        last = X[:, -min(7, self.input_window):, :].mean(axis=1)
        feats.append(last - first)
        return np.hstack(feats)

    def fit(self, X_train, Y_train, X_test, Y_test) -> list[dict]:
        X_tr = self._engineer_features(X_train)
        X_te = self._engineer_features(X_test)
        history = []
        for fi in range(self.num_features):
            y_tr = Y_train[:, :, fi]
            y_te = Y_test[:, :, fi]
            import torch
            use_gpu = torch.cuda.is_available()
            model = xgb.XGBRegressor(
                n_estimators=300, max_depth=8, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8, min_child_weight=3,
                reg_alpha=0.1, reg_lambda=1.0, tree_method="hist",
                device="cuda" if use_gpu else "cpu", n_jobs=-1, verbosity=0,
            )
            if use_gpu and fi == 0:
                logger.info("XGBoost using GPU (CUDA)")
            model.fit(X_tr, y_tr, eval_set=[(X_te, y_te)], verbose=False)
            self.models[fi] = model
            pred_te = model.predict(X_te)
            mse = float(np.mean((pred_te - y_te) ** 2))
            history.append({"feature_idx": fi, "test_mse": round(mse, 6)})
            logger.info("XGBoost feature %d: test_mse=%.6f (gpu=%s)", fi, mse, use_gpu)
        return [{"epoch": 1, "train_loss": 0.0,
                 "test_loss": np.mean([h["test_mse"] for h in history])}]

    def predict(self, input_window: np.ndarray) -> np.ndarray:
        X = self._engineer_features(input_window[np.newaxis, :, :])
        result = np.zeros((self.output_window, self.num_features), dtype=np.float32)
        for fi, model in enumerate(self.models):
            if model is not None:
                result[:, fi] = model.predict(X)[0]
        return result

    def save_bytes(self) -> bytes:
        buf = io.BytesIO()
        pickle.dump({"models": self.models, "num_features": self.num_features,
                      "input_window": self.input_window, "output_window": self.output_window}, buf)
        return buf.getvalue()

    def load_bytes(self, data: bytes) -> None:
        p = pickle.loads(data)
        self.models = p["models"]
        self.num_features = p["num_features"]
        self.input_window = p["input_window"]
        self.output_window = p["output_window"]
