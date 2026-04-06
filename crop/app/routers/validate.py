import io
import logging
from typing import Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from app.config import LOCATION_LAT, LOCATION_LON, LOCATION_NAME, ALL_FEATURES
from app.services.blob_storage import BlobStorageService
from app.services.predictor import VALID_MODEL_TYPES, CropPredictor

router = APIRouter(prefix="/api/crop", tags=["crop-validate"])
logger = logging.getLogger(__name__)

predictor = CropPredictor()
_blob = BlobStorageService()


def _load_cached(location, lat, lon):
    key = f"crop_{location}_{lat}_{lon}.parquet"
    try:
        raw = _blob.download_data(key)
        df = pd.read_parquet(io.BytesIO(raw))
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        return df
    except Exception:
        return None


@router.get("/validate")
def validate_model(
    lat: float = Query(default=LOCATION_LAT),
    lon: float = Query(default=LOCATION_LON),
    location: str = Query(default=LOCATION_NAME),
    lookback_days: int = Query(default=60, ge=30, le=180),
    model_type: str = Query(default="lstm"),
):
    if model_type not in VALID_MODEL_TYPES:
        raise HTTPException(400, f"Invalid model_type. Choose from: {VALID_MODEL_TYPES}")

    predictor.load_model(model_type, location=location)
    if model_type not in predictor.loaded_model_types():
        raise HTTPException(404, f"No trained {model_type} model.")

    from app.config import INPUT_WINDOW, OUTPUT_WINDOW

    df = _load_cached(location, lat, lon)
    if df is None:
        raise HTTPException(404, "No cached data. Download data first.")

    for col in ALL_FEATURES:
        if col not in df.columns:
            df[col] = 0.0

    if len(df) < INPUT_WINDOW + OUTPUT_WINDOW:
        raise HTTPException(400, f"Not enough data. Need {INPUT_WINDOW + OUTPUT_WINDOW} days, got {len(df)}")

    input_df = df.iloc[:INPUT_WINDOW + OUTPUT_WINDOW].copy()
    result = predictor.predict(input_df.iloc[:INPUT_WINDOW + 1], model_type=model_type, days=OUTPUT_WINDOW)

    # Get actual values for comparison
    actual_df = df.iloc[INPUT_WINDOW:INPUT_WINDOW + OUTPUT_WINDOW]
    ndvi_idx = ALL_FEATURES.index("ndvi") if "ndvi" in ALL_FEATURES else 0

    predicted_ndvi = [row[ndvi_idx] for row in result["predictions"]]
    actual_ndvi = actual_df["ndvi"].tolist()

    n = min(len(predicted_ndvi), len(actual_ndvi))
    pred_arr = np.array(predicted_ndvi[:n])
    actual_arr = np.array(actual_ndvi[:n])

    mae = float(np.mean(np.abs(pred_arr - actual_arr)))
    rmse = float(np.sqrt(np.mean((pred_arr - actual_arr) ** 2)))
    ss_res = np.sum((actual_arr - pred_arr) ** 2)
    ss_tot = np.sum((actual_arr - np.mean(actual_arr)) ** 2)
    r2 = float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0
    bias = float(np.mean(pred_arr - actual_arr))

    dates = actual_df["date"].dt.strftime("%Y-%m-%d").tolist()[:n]

    return {
        "model_type": model_type,
        "location": location,
        "dates": dates,
        "predicted_ndvi": [round(v, 4) for v in predicted_ndvi[:n]],
        "actual_ndvi": [round(v, 4) for v in actual_ndvi[:n]],
        "metrics": {
            "mae": round(mae, 4),
            "rmse": round(rmse, 4),
            "r2": round(r2, 4),
            "bias": round(bias, 4),
        },
    }


@router.get("/validate/compare")
def compare_validation(
    lat: float = Query(default=LOCATION_LAT),
    lon: float = Query(default=LOCATION_LON),
    location: str = Query(default=LOCATION_NAME),
    lookback_days: int = Query(default=60),
):
    """Compare validation metrics across all models."""
    results = {}
    for mt in VALID_MODEL_TYPES:
        try:
            results[mt] = validate_model(lat=lat, lon=lon, location=location,
                                         lookback_days=lookback_days, model_type=mt)
        except HTTPException:
            results[mt] = {"status": "not_available", "model_type": mt}
    return results
