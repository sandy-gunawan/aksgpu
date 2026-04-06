import io
import logging
import pickle
import warnings

import numpy as np

from app.config import INPUT_WINDOW, OUTPUT_WINDOW, NUM_FEATURES
from app.models.base import BaseCropModel

logger = logging.getLogger(__name__)

try:
    from statsmodels.tsa.statespace.sarimax import SARIMAX
except ImportError:
    SARIMAX = None
    logger.warning("statsmodels not installed — ARIMA unavailable")


class CropARIMA(BaseCropModel):
    """ARIMA model for crop health. One SARIMAX per feature. Order (1,1,1) for daily data."""

    model_type: str = "arima"

    def __init__(self, num_features: int = NUM_FEATURES,
                 input_window: int = INPUT_WINDOW, output_window: int = OUTPUT_WINDOW,
                 order: tuple = (1, 1, 1)):
        if SARIMAX is None:
            raise ImportError("statsmodels required for CropARIMA")
        self.num_features = num_features
        self.input_window = input_window
        self.output_window = output_window
        self.order = order
        self.params: list = [None] * num_features
        self.trained = False

    def fit(self, X_train, Y_train, X_test, Y_test) -> list[dict]:
        last_input = X_train[-1]
        last_output = Y_train[-1]
        series = np.concatenate([last_input, last_output], axis=0)

        logger.info("ARIMA training on CPU (statsmodels)")
        total_test_mse = 0.0
        for fi in range(self.num_features):
            feat_series = series[:, fi]
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    model = SARIMAX(feat_series, order=self.order,
                                    enforce_stationarity=False, enforce_invertibility=False)
                    result = model.fit(disp=False, maxiter=50)
                    self.params[fi] = result.params

                test_input = X_test[-1, :, fi]
                forecast = self._predict_feature(fi, test_input)
                test_actual = Y_test[-1, :, fi]
                n = min(len(forecast), len(test_actual))
                mse = float(np.mean((forecast[:n] - test_actual[:n]) ** 2))
                total_test_mse += mse
                logger.info("ARIMA feature %d: test_mse=%.6f", fi, mse)
            except Exception:
                logger.exception("ARIMA fit failed for feature %d", fi)
                self.params[fi] = None

        self.trained = True
        avg_mse = total_test_mse / max(self.num_features, 1)
        return [{"epoch": 1, "train_loss": 0.0, "test_loss": round(avg_mse, 6)}]

    def _predict_feature(self, feat_idx: int, input_series: np.ndarray) -> np.ndarray:
        if self.params[feat_idx] is None:
            return np.full(self.output_window, input_series[-1])
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model = SARIMAX(input_series, order=self.order,
                                enforce_stationarity=False, enforce_invertibility=False)
                result = model.filter(self.params[feat_idx])
                forecast = result.forecast(steps=self.output_window)
                return forecast
        except Exception:
            return np.full(self.output_window, input_series[-1])

    def predict(self, input_window: np.ndarray) -> np.ndarray:
        result = np.zeros((self.output_window, self.num_features), dtype=np.float32)
        for fi in range(self.num_features):
            result[:, fi] = self._predict_feature(fi, input_window[:, fi])
        return result

    def save_bytes(self) -> bytes:
        buf = io.BytesIO()
        pickle.dump({"params": self.params, "order": self.order,
                      "num_features": self.num_features, "input_window": self.input_window,
                      "output_window": self.output_window}, buf)
        return buf.getvalue()

    def load_bytes(self, data: bytes) -> None:
        p = pickle.loads(data)
        self.params = p["params"]
        self.order = p["order"]
        self.num_features = p["num_features"]
        self.input_window = p["input_window"]
        self.output_window = p["output_window"]
        self.trained = True
