import io
import logging
import pickle
import warnings

import numpy as np

from app.config import INPUT_WINDOW, OUTPUT_WINDOW, NUM_FEATURES
from app.models.base import BaseWeatherModel

logger = logging.getLogger(__name__)

# Try GPU-accelerated ARIMA (cuML from RAPIDS) first, fall back to statsmodels
_USE_CUML = False
try:
    from cuml.tsa.arima import ARIMA as cuARIMA
    import cupy as cp
    _USE_CUML = True
    logger.info("cuML ARIMA available -- using GPU-accelerated ARIMA")
except ImportError:
    cuARIMA = None
    cp = None

try:
    from statsmodels.tsa.statespace.sarimax import SARIMAX
except ImportError:
    SARIMAX = None
    if not _USE_CUML:
        logger.warning("Neither cuML nor statsmodels installed -- ARIMA model unavailable")


class WeatherARIMA(BaseWeatherModel):
    """
    ARIMA model for weather forecasting.

    Strategy:
    - Fit one SARIMAX model per weather feature on the training series.
    - Store the fitted model parameters so we can re-fit quickly at
      prediction time on the most recent input window.
    - At prediction time, fit ARIMA on the input window and forecast
      output_window steps.

    ARIMA is inherently univariate, so we run independent models per feature.
    We use order (2,1,2) as a reasonable default for hourly weather data.
    """

    model_type: str = "arima"

    def __init__(
        self,
        num_features: int = NUM_FEATURES,
        input_window: int = INPUT_WINDOW,
        output_window: int = OUTPUT_WINDOW,
        order: tuple = (2, 1, 2),
    ):
        if not _USE_CUML and SARIMAX is None:
            raise ImportError("Either cuML or statsmodels is required for WeatherARIMA")
        self.use_gpu = _USE_CUML
        self.num_features = num_features
        self.input_window = input_window
        self.output_window = output_window
        self.order = order
        # Store per-feature ARIMA parameters (not the full fitted model,
        # since ARIMA must be re-fit on the actual input window at inference)
        self.params: list[np.ndarray | None] = [None] * num_features
        self.trained = False

    def fit(
        self,
        X_train: np.ndarray,
        Y_train: np.ndarray,
        X_test: np.ndarray,
        Y_test: np.ndarray,
    ) -> list[dict]:
        """
        Fit ARIMA on the last training series for each feature.
        Uses GPU-accelerated cuML ARIMA when available, falls back to statsmodels.
        """
        last_input = X_train[-1]   # (input_window, num_features)
        last_output = Y_train[-1]  # (output_window, num_features)
        series = np.concatenate([last_input, last_output], axis=0)

        if self.use_gpu:
            logger.info("ARIMA training on GPU (cuML)")
        else:
            logger.info("ARIMA training on CPU (statsmodels)")

        total_test_mse = 0.0
        for feat_idx in range(self.num_features):
            feat_series = series[:, feat_idx]
            try:
                if self.use_gpu:
                    self._fit_cuml(feat_idx, feat_series, X_test, Y_test)
                else:
                    self._fit_statsmodels(feat_idx, feat_series, X_test, Y_test)

                # Evaluate on test set
                test_input = X_test[-1, :, feat_idx]
                forecast = self._predict_feature(feat_idx, test_input)
                test_actual = Y_test[-1, :, feat_idx]
                n = min(len(forecast), len(test_actual))
                mse = float(np.mean((forecast[:n] - test_actual[:n]) ** 2))
                total_test_mse += mse
                logger.info("ARIMA feature %d: test_mse=%.6f (gpu=%s)", feat_idx, mse, self.use_gpu)
            except Exception:
                logger.exception("ARIMA fit failed for feature %d", feat_idx)
                self.params[feat_idx] = None

        self.trained = True
        avg_mse = total_test_mse / self.num_features
        return [{"epoch": 1, "train_loss": 0.0, "test_loss": round(avg_mse, 6)}]

    def _fit_cuml(self, feat_idx: int, series: np.ndarray,
                  X_test: np.ndarray, Y_test: np.ndarray) -> None:
        """Fit using cuML GPU-accelerated ARIMA."""
        # cuML ARIMA expects (n_obs, 1) for single series
        gpu_series = cp.asarray(series.reshape(-1, 1), dtype=cp.float64)
        model = cuARIMA(gpu_series, order=self.order)
        model.fit()
        # Store params as numpy for serialization
        self.params[feat_idx] = cp.asnumpy(model.params) if hasattr(model, 'params') else series[-5:]

    def _fit_statsmodels(self, feat_idx: int, series: np.ndarray,
                         X_test: np.ndarray, Y_test: np.ndarray) -> None:
        """Fit using statsmodels SARIMAX (CPU fallback)."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = SARIMAX(
                series,
                order=self.order,
                enforce_stationarity=False,
                enforce_invertibility=False,
            )
            fitted = model.fit(disp=False, maxiter=100)
            self.params[feat_idx] = fitted.params

    def _predict_feature(self, feat_idx: int, series: np.ndarray) -> np.ndarray:
        """Predict a single feature using GPU or CPU."""
        if self.use_gpu:
            try:
                gpu_series = cp.asarray(series.reshape(-1, 1), dtype=cp.float64)
                model = cuARIMA(gpu_series, order=self.order)
                model.fit()
                forecast = model.forecast(self.output_window)
                return cp.asnumpy(forecast).flatten().astype(np.float32)
            except Exception:
                logger.warning("cuML predict failed for feature %d, falling back to statsmodels", feat_idx)

        # CPU fallback
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = SARIMAX(
                series,
                order=self.order,
                enforce_stationarity=False,
                enforce_invertibility=False,
            )
            if self.params[feat_idx] is not None:
                fitted = model.fit(disp=False, maxiter=50, start_params=self.params[feat_idx])
            else:
                fitted = model.fit(disp=False, maxiter=100)
            return fitted.forecast(steps=self.output_window).astype(np.float32)

    def predict(self, input_window: np.ndarray) -> np.ndarray:
        """input_window: (input_window, num_features) -> (output_window, num_features)"""
        result = np.zeros((self.output_window, self.num_features), dtype=np.float32)
        for feat_idx in range(self.num_features):
            feat_series = input_window[:, feat_idx]
            try:
                result[:, feat_idx] = self._predict_feature(feat_idx, feat_series)
            except Exception:
                logger.exception("ARIMA predict failed for feature %d", feat_idx)
                result[:, feat_idx] = feat_series[-1]
        return result

    def save_bytes(self) -> bytes:
        buf = io.BytesIO()
        pickle.dump({
            "params": self.params,
            "order": self.order,
            "num_features": self.num_features,
            "input_window": self.input_window,
            "output_window": self.output_window,
            "trained": self.trained,
        }, buf)
        return buf.getvalue()

    def load_bytes(self, data: bytes) -> None:
        payload = pickle.loads(data)
        self.params = payload["params"]
        self.order = payload["order"]
        self.num_features = payload["num_features"]
        self.input_window = payload["input_window"]
        self.output_window = payload["output_window"]
        self.trained = payload.get("trained", True)
