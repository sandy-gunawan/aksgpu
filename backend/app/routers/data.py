import io
import json
import logging
import time
from datetime import date, timedelta

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from app.config import CITY_LAT, CITY_LON, CITY_NAME
from app.services.blob_storage import BlobStorageService
from app.services.data_fetcher import WeatherDataFetcher

router = APIRouter(prefix="/api/data", tags=["data"])
logger = logging.getLogger(__name__)

_blob = BlobStorageService()


def _data_key(city: str, lat: float, lon: float) -> str:
    """Generate a blob filename for cached weather data."""
    return f"weather_{city}_{lat}_{lon}.parquet"


def _meta_key(city: str, lat: float, lon: float) -> str:
    return f"weather_{city}_{lat}_{lon}_meta.json"


@router.get("/status")
def data_status(
    city: str = Query(default=CITY_NAME),
    lat: float = Query(default=CITY_LAT),
    lon: float = Query(default=CITY_LON),
):
    """Check if pre-downloaded weather data exists for this city."""
    meta_name = _meta_key(city, lat, lon)
    try:
        meta_bytes = _blob.download_data(meta_name)
        meta = json.loads(meta_bytes)
        return {
            "available": True,
            "city": city,
            "lat": lat,
            "lon": lon,
            "start_date": meta.get("start_date"),
            "end_date": meta.get("end_date"),
            "records": meta.get("records"),
            "downloaded_at": meta.get("downloaded_at"),
            "size_bytes": meta.get("size_bytes"),
        }
    except Exception:
        return {
            "available": False,
            "city": city,
            "lat": lat,
            "lon": lon,
            "message": "No pre-downloaded data. Click 'Download Data' to fetch from Open-Meteo.",
        }


@router.post("/download")
def download_data(
    city: str = Query(default=CITY_NAME),
    lat: float = Query(default=CITY_LAT),
    lon: float = Query(default=CITY_LON),
    years: int = Query(default=1, ge=0, le=5),
    months: int = Query(default=0, ge=0, le=11),
):
    """Download weather data from Open-Meteo and store to Blob Storage."""
    fetcher = WeatherDataFetcher(lat=lat, lon=lon)

    total_days = years * 365 + months * 30
    if total_days < 30:
        total_days = 30  # minimum 1 month

    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=total_days)

    logger.info("Downloading weather data: %s to %s for %s (%.2f, %.2f)",
                start, end, city, lat, lon)

    t0 = time.time()
    try:
        # Chunk into 90-day segments to avoid Open-Meteo timeout
        chunks = []
        chunk_start = start
        while chunk_start < end:
            chunk_end = min(chunk_start + timedelta(days=90), end)
            logger.info("Fetching chunk: %s to %s", chunk_start, chunk_end)
            chunk_df = fetcher.fetch_historical(chunk_start.isoformat(), chunk_end.isoformat())
            chunks.append(chunk_df)
            chunk_start = chunk_end + timedelta(days=1)
        df = pd.concat(chunks).drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Open-Meteo API error: {exc}")

    # Also fetch recent data (last 7 days + forecast)
    try:
        recent = fetcher.fetch_recent(past_days=15, forecast_days=0)
        # Merge: keep historical, append any recent rows not in historical
        df = pd.concat([df, recent]).drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    except Exception:
        logger.warning("Could not fetch recent data, using historical only")

    elapsed = time.time() - t0

    # Save to Blob as Parquet
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    parquet_bytes = buf.getvalue()

    data_name = _data_key(city, lat, lon)
    _blob.upload_data(parquet_bytes, data_name)

    # Save metadata
    meta = {
        "city": city,
        "lat": lat,
        "lon": lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "records": len(df),
        "downloaded_at": date.today().isoformat(),
        "download_seconds": round(elapsed, 1),
        "size_bytes": len(parquet_bytes),
    }
    _blob.upload_data(json.dumps(meta).encode(), _meta_key(city, lat, lon))

    # Clear the in-memory cache so subsequent requests use blob data
    from app.services.data_fetcher import _cache
    _cache.clear()

    return {
        "status": "downloaded",
        "city": city,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "records": len(df),
        "size_kb": round(len(parquet_bytes) / 1024, 1),
        "download_seconds": round(elapsed, 1),
    }


@router.get("/list")
def list_data_files():
    """List all downloaded weather data files."""
    try:
        files = _blob.list_data_files(prefix="weather_")
        return {"files": files}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
