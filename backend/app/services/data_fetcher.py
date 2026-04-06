import io
import logging
import time
from datetime import date, timedelta
from functools import lru_cache
from hashlib import md5

import numpy as np
import pandas as pd
import requests

from app.config import (
    ARCHIVE_API_URL,
    CITY_LAT,
    CITY_LON,
    FORECAST_API_URL,
    HOURLY_VARIABLES,
)

logger = logging.getLogger(__name__)

# Column mapping: Open-Meteo names → our internal names
_COL_MAP = {
    "temperature_2m": "temperature",
    "relative_humidity_2m": "humidity",
    "wind_speed_10m": "wind_speed",
    "precipitation": "precipitation",
    "surface_pressure": "pressure",
}

# --- In-memory cache with TTL ---
_cache: dict[str, tuple[float, pd.DataFrame]] = {}
_CACHE_TTL = 600  # 10 minutes


def _cache_get(key: str) -> pd.DataFrame | None:
    if key in _cache:
        ts, df = _cache[key]
        if time.time() - ts < _CACHE_TTL:
            logger.debug("Cache HIT: %s", key)
            return df.copy()
        del _cache[key]
    return None


def _cache_set(key: str, df: pd.DataFrame) -> None:
    _cache[key] = (time.time(), df)
    # Evict old entries if cache grows too large
    if len(_cache) > 50:
        oldest = min(_cache, key=lambda k: _cache[k][0])
        del _cache[oldest]


class WeatherDataFetcher:
    """Fetches weather data from the Open-Meteo API (free, no key required)."""

    def __init__(self, lat: float = CITY_LAT, lon: float = CITY_LON):
        self.lat = lat
        self.lon = lon

    # ------------------------------------------------------------------
    # Blob-cached data (pre-downloaded)
    # ------------------------------------------------------------------

    def load_from_blob(self, city: str = "") -> pd.DataFrame | None:
        """Try to load pre-downloaded weather data from Blob Storage."""
        try:
            from app.services.blob_storage import BlobStorageService
            blob = BlobStorageService()
            if not city:
                from app.config import CITY_NAME
                city = CITY_NAME
            filename = f"weather_{city}_{self.lat}_{self.lon}.parquet"
            if not blob.data_file_exists(filename):
                return None
            data = blob.download_data(filename)
            df = pd.read_parquet(io.BytesIO(data))
            logger.info("Loaded %d records from Blob: %s", len(df), filename)
            return df
        except Exception as e:
            logger.debug("Blob data not available: %s", e)
            return None

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def fetch_historical(self, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetch hourly historical data from the Open-Meteo Archive API.
        Dates in "YYYY-MM-DD" format.
        """
        cache_key = f"hist_{self.lat}_{self.lon}_{start_date}_{end_date}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        params = {
            "latitude": self.lat,
            "longitude": self.lon,
            "start_date": start_date,
            "end_date": end_date,
            "hourly": HOURLY_VARIABLES,
        }
        data = self._request_with_retry(ARCHIVE_API_URL, params)
        df = self._parse_response(data)
        _cache_set(cache_key, df)
        return df

    def fetch_recent(self, past_days: int = 7, forecast_days: int = 0) -> pd.DataFrame:
        """
        Fetch recent observed data. Tries Blob cache first, falls back to API.
        """
        cache_key = f"recent_{self.lat}_{self.lon}_{past_days}_{forecast_days}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        # Try Blob-cached data first (much faster than API)
        if forecast_days == 0:
            blob_df = self.load_from_blob()
            if blob_df is not None and len(blob_df) > 0:
                cutoff = pd.Timestamp.now() - pd.Timedelta(days=past_days + 1)
                filtered = blob_df[blob_df["time"] >= cutoff].copy()
                if len(filtered) >= 24:  # at least 1 day of data
                    logger.info("Using Blob data: %d records (past %d days)", len(filtered), past_days)
                    _cache_set(cache_key, filtered)
                    return filtered
                logger.info("Blob data too old for past_%d days, falling back to API", past_days)

        params = {
            "latitude": self.lat,
            "longitude": self.lon,
            "hourly": HOURLY_VARIABLES,
            "past_days": past_days,
            "forecast_days": forecast_days,
        }
        data = self._request_with_retry(FORECAST_API_URL, params)
        df = self._parse_response(data)
        _cache_set(cache_key, df)
        return df

    def fetch_training_data(self, years: int = 1, months: int = 0) -> pd.DataFrame:
        """Convenience: fetch historical data ending yesterday.
        
        Args:
            years: Number of years of data (default: 1)
            months: Additional months of data (default: 0)
                    Total period = years * 365 + months * 30 days
        """
        end = date.today() - timedelta(days=1)
        total_days = years * 365 + months * 30
        start = end - timedelta(days=total_days)
        logger.info("Fetching historical data from %s to %s (%d days)...", start, end, total_days)
        df = self.fetch_historical(start.isoformat(), end.isoformat())
        logger.info("Fetched %d hourly records", len(df))
        return df

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _request_with_retry(url: str, params: dict, retries: int = 3) -> dict:
        """HTTP GET with exponential back-off (1 s, 2 s, 4 s)."""
        for attempt in range(retries):
            resp = requests.get(url, params=params, timeout=300)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                wait = 2**attempt
                logger.warning("Rate-limited (429). Retrying in %ds …", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
        raise RuntimeError(f"Failed after {retries} retries: {url}")

    @staticmethod
    def _parse_response(data: dict) -> pd.DataFrame:
        """Convert Open-Meteo JSON response to a clean DataFrame."""
        hourly = data.get("hourly", {})
        if not hourly or "time" not in hourly:
            raise ValueError("Invalid Open-Meteo response: 'hourly.time' missing")

        df = pd.DataFrame({"time": pd.to_datetime(hourly["time"])})
        for api_col, our_col in _COL_MAP.items():
            df[our_col] = hourly.get(api_col)

        # Add seasonal features (sin/cos encoding of time)
        day_of_year = df["time"].dt.dayofyear
        hour_of_day = df["time"].dt.hour
        df["day_sin"] = np.sin(2 * np.pi * day_of_year / 365.25)
        df["day_cos"] = np.cos(2 * np.pi * day_of_year / 365.25)
        df["hour_sin"] = np.sin(2 * np.pi * hour_of_day / 24)
        df["hour_cos"] = np.cos(2 * np.pi * hour_of_day / 24)

        # Handle missing values
        df = df.ffill().bfill()
        df = df.dropna()
        return df
