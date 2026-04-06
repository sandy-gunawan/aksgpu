import io
import json
import logging
from datetime import date, timedelta

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.config import (
    LOCATION_LAT, LOCATION_LON, LOCATION_NAME, ALL_FEATURES,
    WEATHER_FEATURES, SOIL_FEATURES, AIR_FEATURES, SATELLITE_FEATURES, TEMPORAL_FEATURES,
)
from app.services.blob_storage import BlobStorageService
from app.services.data_fetcher import CropDataFetcher
from app.services.satellite_fetcher import SatelliteFetcher

router = APIRouter(prefix="/api/crop/data", tags=["crop-data"])
logger = logging.getLogger(__name__)

_blob = BlobStorageService()


def _data_key(location: str, lat: float, lon: float) -> str:
    return f"crop_{location}_{lat}_{lon}.parquet"


def _meta_key(location: str, lat: float, lon: float) -> str:
    return f"crop_{location}_{lat}_{lon}_meta.json"


@router.get("/status")
def data_status(
    location: str = Query(default=LOCATION_NAME),
    lat: float = Query(default=LOCATION_LAT),
    lon: float = Query(default=LOCATION_LON),
):
    meta_name = _meta_key(location, lat, lon)
    try:
        meta_bytes = _blob.download_data(meta_name)
        meta = json.loads(meta_bytes)
        return {"available": True, "location": location, "lat": lat, "lon": lon, **meta}
    except Exception:
        return {"available": False, "location": location, "lat": lat, "lon": lon,
                "message": "No cached data. Download data to fetch from Open-Meteo + NASA MODIS."}


@router.post("/download")
def download_data(
    location: str = Query(default=LOCATION_NAME),
    lat: float = Query(default=LOCATION_LAT),
    lon: float = Query(default=LOCATION_LON),
    years: int = Query(default=2, ge=1, le=5),
    months: int = Query(default=0, ge=0, le=11),
):
    """Download weather+soil+air+satellite data and cache to Blob Storage."""
    total_days = years * 365 + months * 30
    if total_days < 60:
        total_days = 60

    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=total_days)

    logger.info("Downloading crop data: %s to %s for %s (%.2f, %.2f)",
                start, end, location, lat, lon)

    # Fetch weather + soil + air quality
    fetcher = CropDataFetcher(lat=lat, lon=lon)
    df = fetcher.fetch_all(start.isoformat(), end.isoformat())

    # Fetch satellite NDVI/EVI
    sat = SatelliteFetcher(lat=lat, lon=lon)
    sat_df = sat.fetch_ndvi_evi(start.isoformat(), end.isoformat())
    df = df.merge(sat_df[["date", "ndvi", "evi"]], on="date", how="left")
    df["ndvi"] = df["ndvi"].interpolate().bfill().ffill()
    df["evi"] = df["evi"].interpolate().bfill().ffill()

    # Ensure all features exist
    for col in ALL_FEATURES:
        if col not in df.columns:
            df[col] = 0.0

    # Save to Blob
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    parquet_bytes = buf.getvalue()
    _blob.upload_data(parquet_bytes, _data_key(location, lat, lon))

    meta = {
        "location": location, "lat": lat, "lon": lon,
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "records": len(df), "features": len(df.columns),
        "size_bytes": len(parquet_bytes),
        "downloaded_at": date.today().isoformat(),
        "data_sources": ["open-meteo-weather", "open-meteo-soil", "open-meteo-air-quality", "nasa-modis-ndvi"],
    }
    _blob.upload_data(json.dumps(meta).encode(), _meta_key(location, lat, lon))

    return {
        "status": "ok", "location": location,
        "records": len(df), "features": len(df.columns),
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "size_bytes": len(parquet_bytes),
        "data_sources_fetched": meta["data_sources"],
    }


@router.get("/presets")
def get_presets():
    from app.config import CROP_PRESETS
    return CROP_PRESETS


@router.get("/preview")
def preview_data(
    location: str = Query(default=LOCATION_NAME),
    lat: float = Query(default=LOCATION_LAT),
    lon: float = Query(default=LOCATION_LON),
    rows: int = Query(default=20, ge=5, le=100),
    offset: int = Query(default=0, ge=0),
):
    """Return a sample of the downloaded data with source grouping."""
    key = _data_key(location, lat, lon)
    try:
        raw = _blob.download_data(key)
        df = pd.read_parquet(io.BytesIO(raw))
    except Exception:
        raise HTTPException(404, "No data found. Download data first.")

    total = len(df)
    subset = df.iloc[offset:offset + rows].copy()

    if "date" in subset.columns:
        subset["date"] = subset["date"].astype(str)

    # Group columns by source
    available_weather = [c for c in WEATHER_FEATURES if c in subset.columns]
    available_soil = [c for c in SOIL_FEATURES if c in subset.columns]
    available_air = [c for c in AIR_FEATURES if c in subset.columns]
    available_sat = [c for c in SATELLITE_FEATURES if c in subset.columns]
    available_temporal = [c for c in TEMPORAL_FEATURES if c in subset.columns]

    return {
        "location": location,
        "total_records": total,
        "showing": {"offset": offset, "count": len(subset)},
        "columns": list(subset.columns),
        "column_groups": {
            "weather": available_weather,
            "soil": available_soil,
            "air_quality": available_air,
            "satellite": available_sat,
            "temporal": available_temporal,
        },
        "data": subset.round(4).to_dict(orient="records"),
        "stats": {
            col: {
                "min": round(float(df[col].min()), 4),
                "max": round(float(df[col].max()), 4),
                "mean": round(float(df[col].mean()), 4),
            }
            for col in ALL_FEATURES if col in df.columns
        },
    }


@router.get("/download-xlsx")
def download_xlsx(
    location: str = Query(default=LOCATION_NAME),
    lat: float = Query(default=LOCATION_LAT),
    lon: float = Query(default=LOCATION_LON),
):
    """Download the cached data as an Excel file."""
    key = _data_key(location, lat, lon)
    try:
        raw = _blob.download_data(key)
        df = pd.read_parquet(io.BytesIO(raw))
    except Exception:
        raise HTTPException(404, "No data found. Download data first.")

    if "date" in df.columns:
        df["date"] = df["date"].astype(str)

    buf = io.BytesIO()
    df.round(4).to_excel(buf, index=False, sheet_name="Crop Data")
    buf.seek(0)

    filename = f"crop_data_{location}_{lat}_{lon}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
