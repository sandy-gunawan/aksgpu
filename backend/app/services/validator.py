import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
import torch

from app.config import ALL_FEATURES, DEVICE, FEATURE_COLUMNS, INPUT_WINDOW, OUTPUT_WINDOW
from app.services.data_fetcher import WeatherDataFetcher
from app.services.predictor import WeatherPredictor

logger = logging.getLogger(__name__)


class ModelValidator:
    """
    Backtesting: compare model predictions against actual observed data
    for a past time window to prove the model works.
    """

    def __init__(
        self,
        predictor: WeatherPredictor | None = None,
        fetcher: WeatherDataFetcher | None = None,
    ):
        self.predictor = predictor or WeatherPredictor()
        self.fetcher = fetcher or WeatherDataFetcher()

    def validate(self, lookback_days: int = 14, model_type: str = "lstm") -> dict:
        """
        1. Fetch actual weather for the last `lookback_days`
        2. Use the specified model to predict that same window
        3. Compute MAE, RMSE, R², Bias
        4. Return metrics + both time series
        """
        if not self.predictor.is_model_loaded(model_type):
            raise RuntimeError(f"No {model_type} model loaded — cannot validate")

        # ---- Actual observed data ----
        actual_df = self.fetcher.fetch_recent(
            past_days=lookback_days, forecast_days=0
        )
        if len(actual_df) < 24:
            raise ValueError("Not enough recent observed data for validation")

        # We need INPUT_WINDOW hours *before* the lookback window for model input
        input_df = self.fetcher.fetch_recent(
            past_days=lookback_days + (INPUT_WINDOW // 24) + 1,
            forecast_days=0,
        )

        # Split: input context | validation window
        lookback_hours = lookback_days * 24
        actual_vals = actual_df[FEATURE_COLUMNS].values[-lookback_hours:]
        actual_times = actual_df["time"].values[-lookback_hours:]

        # ---- Generate predictions (rolling, OUTPUT_WINDOW hours at a time) ----
        input_features = input_df[ALL_FEATURES].values.astype(np.float32)
        start_idx = len(input_features) - lookback_hours - INPUT_WINDOW
        if start_idx < 0:
            start_idx = 0

        scaler = self.predictor._scalers.get(model_type)
        if scaler is None:
            raise RuntimeError(f"No scaler loaded for {model_type}")

        # Rolling prediction: predict OUTPUT_WINDOW hours, slide forward, repeat
        all_pred_scaled = []
        current_window = input_features[start_idx : start_idx + INPUT_WINDOW].copy()
        hours_predicted = 0

        # ARIMA works on raw (unscaled) data — it has its own differencing
        skip_scaling = (model_type == "arima")

        while hours_predicted < lookback_hours:
            if skip_scaling:
                pred_input = current_window
            else:
                pred_input = scaler.transform(current_window)

            if model_type == "lstm":
                inp = (
                    torch.from_numpy(pred_input)
                    .unsqueeze(0)
                    .float()
                    .to(DEVICE)
                )
                with torch.no_grad():
                    chunk = self.predictor._models["lstm"](inp).cpu().numpy()[0]
            else:
                model = self.predictor._models[model_type]
                chunk = model.predict(pred_input)

            # Clamp predictions to prevent NaN/Inf from snowballing
            chunk = np.nan_to_num(chunk, nan=0.0, posinf=1.0, neginf=0.0)
            chunk = np.clip(chunk, -1e6, 1e6)

            if skip_scaling:
                # ARIMA output is already in real units; store raw for final result
                all_pred_scaled.append(chunk)
            else:
                all_pred_scaled.append(chunk)
            hours_predicted += len(chunk)

            # Slide window forward: drop oldest, append predictions
            if skip_scaling:
                # For ARIMA, feed raw predictions back as raw input
                current_window = np.concatenate([
                    current_window[len(chunk):],
                    chunk
                ], axis=0)[-INPUT_WINDOW:]
            else:
                current_window = np.concatenate([
                    current_window[len(chunk):],
                    chunk
                ], axis=0)[-INPUT_WINDOW:]
            # Sanitize window to prevent NaN/Inf from propagating to next iteration
            current_window = np.nan_to_num(current_window, nan=0.0, posinf=1.0, neginf=0.0)
            current_window = np.clip(current_window.astype(np.float32), -1e6, 1e6)

        # Concatenate all chunks and inverse transform
        pred_scaled_full = np.concatenate(all_pred_scaled, axis=0)
        if skip_scaling:
            predicted_all = pred_scaled_full  # already in real units
        else:
            predicted_all = scaler.inverse_transform(pred_scaled_full)

        # Extract only weather features (first len(FEATURE_COLUMNS) columns)
        predicted_vals = predicted_all[:lookback_hours, :len(FEATURE_COLUMNS)]

        # ---- Ensure same length ----
        n = min(len(actual_vals), len(predicted_vals))
        actual_vals = actual_vals[:n]
        predicted_vals = predicted_vals[:n]
        actual_times = actual_times[:n]

        # ---- Compute metrics (temperature column = index 0) ----
        temp_actual = actual_vals[:, 0]
        temp_pred = predicted_vals[:, 0]

        mae = float(np.mean(np.abs(temp_actual - temp_pred)))
        rmse = float(np.sqrt(np.mean((temp_actual - temp_pred) ** 2)))
        ss_res = np.sum((temp_actual - temp_pred) ** 2)
        ss_tot = np.sum((temp_actual - np.mean(temp_actual)) ** 2)
        r2 = float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0
        bias = float(np.mean(temp_pred - temp_actual))

        # ---- Build response ----
        def _to_records(times, vals):
            records = []
            for i in range(len(times)):
                t = pd.Timestamp(times[i]).isoformat()
                records.append(
                    {
                        "time": t,
                        "temperature": round(float(vals[i, 0]), 1),
                        "humidity": round(float(vals[i, 1]), 1),
                        "wind_speed": round(float(vals[i, 2]), 1),
                        "precipitation": round(float(max(vals[i, 3], 0)), 2),
                        "pressure": round(float(vals[i, 4]), 1),
                    }
                )
            return records

        return {
            "model_type": model_type,
            "metrics": {
                "mae": round(mae, 2),
                "rmse": round(rmse, 2),
                "r2": round(r2, 3),
                "bias": round(bias, 2),
            },
            "predicted": _to_records(actual_times, predicted_vals),
            "actual": _to_records(actual_times, actual_vals),
        }

    def validate_all(self, lookback_days: int = 14) -> dict:
        """Run validation for all loaded model types and return combined results."""
        results = {}
        for mt in self.predictor.loaded_model_types():
            try:
                results[mt] = self.validate(lookback_days=lookback_days, model_type=mt)
            except Exception as e:
                logger.warning("Validation failed for %s: %s", mt, e)
                results[mt] = {"model_type": mt, "error": str(e)}
        return results
