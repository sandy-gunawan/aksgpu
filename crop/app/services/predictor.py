import io
import logging
from typing import Optional

import joblib
import numpy as np
import torch
from sklearn.preprocessing import MinMaxScaler

from app.config import (
    ALL_FEATURES, DEVICE, HIDDEN_SIZE, INPUT_WINDOW, NUM_FEATURES,
    NUM_LAYERS, OUTPUT_WINDOW, SATELLITE_FEATURES,
)
from app.models.lstm_model import CropLSTM
from app.services.blob_storage import BlobStorageService

logger = logging.getLogger(__name__)

VALID_MODEL_TYPES = ("lstm", "xgboost", "arima")

# Stress thresholds based on NDVI
STRESS_LEVELS = [
    (0.6, "healthy", "Healthy vegetation"),
    (0.4, "moderate", "Moderate stress"),
    (0.2, "stressed", "Vegetation stressed"),
    (0.0, "critical", "Critical — severe stress or bare soil"),
]


def classify_stress(ndvi: float) -> dict:
    for threshold, level, desc in STRESS_LEVELS:
        if ndvi >= threshold:
            return {"level": level, "description": desc, "ndvi": round(ndvi, 4)}
    return {"level": "critical", "description": STRESS_LEVELS[-1][2], "ndvi": round(ndvi, 4)}


class CropPredictor:
    def __init__(self, blob_service: BlobStorageService | None = None):
        self.blob = blob_service or BlobStorageService()
        self._models: dict[str, object] = {}
        self._scalers: dict[str, MinMaxScaler] = {}
        self._model_names: dict[str, str] = {}

    def load_model(self, model_type: str = "lstm", location: str = "") -> bool:
        model_name = self.blob.get_latest_model_name(model_type=model_type, location=location)
        if model_name is None:
            logger.warning("No trained %s model found", model_type)
            return False
        if model_type == "lstm":
            return self._load_lstm(model_name)
        return self._load_alternative(model_name, model_type)

    def load_all_models(self, location: str = "") -> dict[str, bool]:
        return {mt: self.load_model(mt, location=location) for mt in VALID_MODEL_TYPES}

    def loaded_model_types(self) -> list[str]:
        return list(self._models.keys())

    def _load_lstm(self, model_name: str) -> bool:
        try:
            pt_bytes = self.blob.download_model(model_name)
            model = CropLSTM(num_features=NUM_FEATURES, hidden_size=HIDDEN_SIZE,
                             num_layers=NUM_LAYERS, output_window=OUTPUT_WINDOW)
            buf = io.BytesIO(pt_bytes)
            model.load_state_dict(torch.load(buf, map_location=DEVICE, weights_only=True))
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
        except Exception as e:
            logger.error("Failed to load LSTM: %s", e)
            return False

    def _load_alternative(self, model_name: str, model_type: str) -> bool:
        try:
            model_bytes = self.blob.download_model(model_name)
            if model_type == "xgboost":
                from app.models.xgboost_model import CropXGBoost
                model = CropXGBoost()
            elif model_type == "arima":
                from app.models.arima_model import CropARIMA
                model = CropARIMA()
            else:
                return False
            model.load_bytes(model_bytes)
            scaler_name = model_name.replace(".pkl", "_scaler.pkl")
            scaler_bytes = self.blob.download_model(scaler_name)
            scaler = joblib.load(io.BytesIO(scaler_bytes))
            self._models[model_type] = model
            self._scalers[model_type] = scaler
            self._model_names[model_type] = model_name
            logger.info("Loaded %s model: %s", model_type, model_name)
            return True
        except Exception as e:
            logger.error("Failed to load %s: %s", model_type, e)
            return False

    def predict(self, input_df, model_type: str = "lstm", days: int = 32) -> dict:
        """
        Run prediction. input_df must be a DataFrame with ALL_FEATURES columns,
        at least INPUT_WINDOW rows.
        Returns dict with predictions, stress levels, and feature names.
        """
        if model_type not in self._models:
            raise ValueError(f"Model '{model_type}' not loaded")
        model = self._models[model_type]
        scaler = self._scalers[model_type]
        features = input_df[ALL_FEATURES].values.astype(np.float32)
        if len(features) < INPUT_WINDOW:
            raise ValueError(f"Need {INPUT_WINDOW} days, got {len(features)}")
        window = features[-INPUT_WINDOW:]
        scaled = scaler.transform(window)

        if model_type == "lstm":
            tensor = torch.from_numpy(scaled).unsqueeze(0).to(DEVICE)
            with torch.no_grad():
                pred_scaled = model(tensor).cpu().numpy()[0]
        else:
            pred_scaled = model.predict(scaled)

        pred = scaler.inverse_transform(pred_scaled)
        pred = pred[:days]

        # Extract NDVI predictions for stress classification
        ndvi_idx = ALL_FEATURES.index("ndvi") if "ndvi" in ALL_FEATURES else None
        evi_idx = ALL_FEATURES.index("evi") if "evi" in ALL_FEATURES else None
        stress_timeline = []
        if ndvi_idx is not None:
            for day_i in range(len(pred)):
                stress_timeline.append({
                    "day": day_i + 1,
                    "ndvi": round(float(pred[day_i, ndvi_idx]), 4),
                    "evi": round(float(pred[day_i, evi_idx]), 4) if evi_idx is not None else None,
                    **classify_stress(float(pred[day_i, ndvi_idx])),
                })

        return {
            "predictions": pred.tolist(),
            "features": ALL_FEATURES,
            "days": len(pred),
            "model_type": model_type,
            "stress_timeline": stress_timeline,
            "current_stress": classify_stress(float(features[-1, ndvi_idx])) if ndvi_idx is not None else None,
        }
