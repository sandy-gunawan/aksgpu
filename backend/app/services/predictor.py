import io
import logging
from datetime import datetime, timedelta

import joblib
import numpy as np
import pandas as pd
import torch

from app.config import (
    ALL_FEATURES,
    DEVICE,
    FEATURE_COLUMNS,
    HIDDEN_SIZE,
    INPUT_WINDOW,
    NUM_FEATURES,
    NUM_LAYERS,
    DROPOUT,
    OUTPUT_WINDOW,
)
from app.models.lstm_model import WeatherLSTM
from app.services.blob_storage import BlobStorageService
from app.services.data_fetcher import WeatherDataFetcher

logger = logging.getLogger(__name__)

VALID_MODEL_TYPES = ("lstm", "xgboost", "arima")

# File extensions per model type
_MODEL_EXT = {"lstm": ".pt", "xgboost": ".pkl", "arima": ".pkl"}


class WeatherPredictor:
    """Loads trained models and generates weather forecasts.  Supports LSTM, XGBoost, ARIMA."""

    def __init__(
        self,
        blob_service: BlobStorageService | None = None,
        data_fetcher: WeatherDataFetcher | None = None,
    ):
        self.blob = blob_service or BlobStorageService()
        self.fetcher = data_fetcher or WeatherDataFetcher()
        # Per-model-type storage
        self._models: dict[str, object] = {}
        self._scalers: dict[str, object] = {}
        self._model_names: dict[str, str] = {}

    # ---- Convenience properties (backward compat) ----

    @property
    def model(self):
        return self._models.get("lstm")

    @property
    def scaler(self):
        return self._scalers.get("lstm") or next(iter(self._scalers.values()), None)

    @property
    def model_name(self):
        return self._model_names.get("lstm")

    @property
    def is_loaded(self) -> bool:
        return bool(self._models)

    def is_model_loaded(self, model_type: str = "lstm") -> bool:
        return model_type in self._models and model_type in self._scalers

    def loaded_model_types(self) -> list[str]:
        return [mt for mt in VALID_MODEL_TYPES if self.is_model_loaded(mt)]

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------

    def load_model(self, model_type: str = "lstm", city: str = "") -> bool:
        """Download the latest model + scaler from Blob Storage and cache."""
        if model_type not in VALID_MODEL_TYPES:
            logger.error("Unknown model type: %s", model_type)
            return False

        model_name = self.blob.get_latest_model_name(model_type=model_type, city=city)
        if model_name is None:
            logger.warning("No trained %s model found in Blob Storage (city=%s)", model_type, city or "any")
            return False

        # Skip reload if already loaded
        if self._model_names.get(model_type) == model_name and self.is_model_loaded(model_type):
            return True

        try:
            if model_type == "lstm":
                return self._load_lstm(model_name)
            else:
                return self._load_alternative(model_name, model_type)
        except Exception:
            logger.exception("Failed to load %s model %s", model_type, model_name)
            return False

    def load_all_models(self, city: str = "") -> dict[str, bool]:
        """Attempt to load all available model types. Returns {type: success}."""
        return {mt: self.load_model(mt, city=city) for mt in VALID_MODEL_TYPES}

    def _load_lstm(self, model_name: str) -> bool:
        pt_bytes = self.blob.download_model(model_name)
        model = WeatherLSTM(
            num_features=NUM_FEATURES,
            hidden_size=HIDDEN_SIZE,
            num_layers=NUM_LAYERS,
            dropout=DROPOUT,
            output_window=OUTPUT_WINDOW,
        )
        buf = io.BytesIO(pt_bytes)
        model.load_state_dict(
            torch.load(buf, map_location=DEVICE, weights_only=True)
        )
        model.to(DEVICE)
        model.eval()

        scaler_name = model_name.replace(".pt", "_scaler.pkl")
        scaler_bytes = self.blob.download_model(scaler_name)
        scaler = joblib.load(io.BytesIO(scaler_bytes))

        self._models["lstm"] = model
        self._scalers["lstm"] = scaler
        self._model_names["lstm"] = model_name
        logger.info("Loaded LSTM model: %s on %s", model_name, DEVICE)
        return True

    def _load_alternative(self, model_name: str, model_type: str) -> bool:
        """Load XGBoost or ARIMA model from Blob Storage."""
        model_bytes = self.blob.download_model(model_name)

        if model_type == "xgboost":
            from app.models.xgboost_model import WeatherXGBoost
            model = WeatherXGBoost()
        elif model_type == "arima":
            from app.models.arima_model import WeatherARIMA
            model = WeatherARIMA()
        else:
            return False

        model.load_bytes(model_bytes)

        # Load shared scaler
        scaler_name = model_name.replace(".pkl", "_scaler.pkl")
        # For alternative models, the scaler name pattern is {model_type}_{city}_{ts}_scaler.pkl
        # The model file is {model_type}_{city}_{ts}.pkl so we need to construct scaler name
        base = model_name.rsplit(".", 1)[0]  # strip extension
        scaler_name = f"{base}_scaler.pkl"
        scaler_bytes = self.blob.download_model(scaler_name)
        scaler = joblib.load(io.BytesIO(scaler_bytes))

        self._models[model_type] = model
        self._scalers[model_type] = scaler
        self._model_names[model_type] = model_name
        logger.info("Loaded %s model: %s", model_type, model_name)
        return True

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    def predict(
        self,
        days: int = 7,
        lat: float | None = None,
        lon: float | None = None,
        model_type: str = "lstm",
    ) -> list[dict]:
        """
        Generate an hourly forecast for the next `days` days.
        Uses the specified model_type for prediction.
        """
        if not self.is_model_loaded(model_type):
            raise RuntimeError(f"No {model_type} model loaded. Train one first.")

        if lat is not None and lon is not None:
            fetcher = WeatherDataFetcher(lat=lat, lon=lon)
        else:
            fetcher = self.fetcher

        # Fetch the last INPUT_WINDOW hours of observed weather
        recent = fetcher.fetch_recent(past_days=INPUT_WINDOW // 24 + 1, forecast_days=0)
        features = recent[ALL_FEATURES].values.astype(np.float32)

        if len(features) < INPUT_WINDOW:
            raise ValueError(
                f"Need {INPUT_WINDOW} hours of recent data, only got {len(features)}"
            )
        window = features[-INPUT_WINDOW:]

        scaler = self._scalers[model_type]

        # ARIMA works on raw data (it has its own differencing); LSTM/XGBoost need scaled data
        if model_type == "arima":
            pred_real_chunk = self._predict_alternative(window, model_type)
        else:
            scaled_window = scaler.transform(window)
            # Run prediction based on model type
            if model_type == "lstm":
                pred_scaled = self._predict_lstm(scaled_window)
            else:
                pred_scaled = self._predict_alternative(scaled_window, model_type)
            # Inverse transform to get real values
            pred_real_chunk = scaler.inverse_transform(pred_scaled)

        # Trim to requested hours
        total_hours = days * 24
        pred_real = pred_real_chunk[:total_hours]

        # Build time index
        last_time = recent["time"].iloc[-1]
        forecast = []
        for i, row in enumerate(pred_real):
            t = last_time + timedelta(hours=i + 1)
            forecast.append(
                {
                    "time": t.isoformat(),
                    "temperature": round(float(row[0]), 1),
                    "humidity": round(float(row[1]), 1),
                    "wind_speed": round(float(row[2]), 1),
                    "precipitation": round(float(max(row[3], 0)), 2),
                    "pressure": round(float(row[4]), 1),
                }
            )
        return forecast

    def _predict_lstm(self, scaled_window: np.ndarray) -> np.ndarray:
        model = self._models["lstm"]
        input_tensor = (
            torch.from_numpy(scaled_window)
            .unsqueeze(0)
            .float()
            .to(DEVICE)
        )
        with torch.no_grad():
            return model(input_tensor).cpu().numpy()[0]

    def _predict_alternative(self, scaled_window: np.ndarray, model_type: str) -> np.ndarray:
        model = self._models[model_type]
        return model.predict(scaled_window)
