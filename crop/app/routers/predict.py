import io
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.config import LOCATION_LAT, LOCATION_LON, LOCATION_NAME, ALL_FEATURES, SATELLITE_FEATURES
from app.services.data_fetcher import CropDataFetcher
from app.services.satellite_fetcher import SatelliteFetcher
from app.services.blob_storage import BlobStorageService
from app.services.predictor import VALID_MODEL_TYPES, CropPredictor

import pandas as pd

router = APIRouter(prefix="/api/crop", tags=["crop-predict"])

predictor = CropPredictor()
_blob = BlobStorageService()


def _load_cached_data(location: str, lat: float, lon: float) -> pd.DataFrame | None:
    """Try to load cached crop data from Blob Storage."""
    key = f"crop_{location}_{lat}_{lon}.parquet"
    try:
        raw = _blob.download_data(key)
        df = pd.read_parquet(io.BytesIO(raw))
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        return df
    except Exception:
        return None


@router.get("/predict")
def get_prediction(
    days: int = Query(default=32, ge=1, le=60),
    lat: float = Query(default=LOCATION_LAT, ge=-90, le=90),
    lon: float = Query(default=LOCATION_LON, ge=-180, le=180),
    location: str = Query(default=LOCATION_NAME),
    model_type: str = Query(default="lstm"),
):
    """Predict vegetation health (NDVI/EVI) for the next N days."""
    if model_type not in VALID_MODEL_TYPES:
        raise HTTPException(400, f"Invalid model_type. Choose from: {VALID_MODEL_TYPES}")

    predictor.load_model(model_type, location=location)
    if model_type not in predictor.loaded_model_types():
        raise HTTPException(404, f"No trained {model_type} model. Train one first.")

    # Try cached data first (fast, from Blob)
    df = _load_cached_data(location, lat, lon)
    if df is not None:
        for col in ALL_FEATURES:
            if col not in df.columns:
                df[col] = 0.0
    else:
        raise HTTPException(404, "No cached data. Go to Training tab and Download Data first.")

    result = predictor.predict(df, model_type=model_type, days=days)
    result["location"] = location
    result["lat"] = lat
    result["lon"] = lon
    return result


@router.get("/compare")
def compare_models(
    days: int = Query(default=32, ge=1, le=60),
    lat: float = Query(default=LOCATION_LAT),
    lon: float = Query(default=LOCATION_LON),
    location: str = Query(default=LOCATION_NAME),
):
    """Compare predictions from all trained models."""
    results = {}
    for mt in VALID_MODEL_TYPES:
        try:
            pred = get_prediction(days=days, lat=lat, lon=lon, location=location, model_type=mt)
            results[mt] = pred
        except HTTPException:
            results[mt] = {"status": "not_available", "model_type": mt}
    return results
