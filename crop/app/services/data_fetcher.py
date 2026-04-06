"""
Multi-source data fetcher for crop health prediction.

Calls two Open-Meteo endpoints:
  1. Weather + Soil (archive + forecast) → daily aggregates
  2. Air Quality → daily aggregates

Merges into a single DataFrame with ~17 features (before satellite).
"""
import logging
import time
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import requests

from app.config import (
    ARCHIVE_API_URL, FORECAST_API_URL,
    AIR_QUALITY_API_URL,
    WEATHER_DAILY_VARIABLES, SOIL_HOURLY_VARIABLES,
    AIR_QUALITY_VARIABLES,
    WEATHER_DAILY_COL_MAP, SOIL_HOURLY_COL_MAP, AIR_COL_MAP,
    TEMPORAL_FEATURES,
)

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAY = 2


def _request_with_retry(url: str, params: dict) -> dict:
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=60)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt == _MAX_RETRIES:
                raise
            logger.warning("API request failed (attempt %d): %s", attempt, e)
            time.sleep(_RETRY_DELAY * attempt)


class CropDataFetcher:
    """Fetches and merges weather+soil+air quality data from Open-Meteo."""

    def __init__(self, lat: float, lon: float):
        self.lat = lat
        self.lon = lon

    def fetch_weather_soil(self, start_date: str, end_date: str) -> pd.DataFrame:
        """Fetch daily weather + hourly soil data from Open-Meteo Archive API, merge to daily."""
        # 1. Daily weather variables
        params_daily = {
            "latitude": self.lat,
            "longitude": self.lon,
            "start_date": start_date,
            "end_date": end_date,
            "daily": WEATHER_DAILY_VARIABLES,
            "timezone": "auto",
        }
        data_daily = _request_with_retry(ARCHIVE_API_URL, params_daily)
        daily = data_daily.get("daily", {})
        if not daily or "time" not in daily:
            raise ValueError(f"No weather data returned for {start_date}..{end_date}")

        df = pd.DataFrame(daily)
        df["date"] = pd.to_datetime(df["time"])
        df = df.drop(columns=["time"])
        df = df.rename(columns=WEATHER_DAILY_COL_MAP)

        # 2. Hourly soil + humidity variables → aggregate to daily mean
        params_hourly = {
            "latitude": self.lat,
            "longitude": self.lon,
            "start_date": start_date,
            "end_date": end_date,
            "hourly": SOIL_HOURLY_VARIABLES,
            "timezone": "auto",
        }
        try:
            data_hourly = _request_with_retry(ARCHIVE_API_URL, params_hourly)
            hourly = data_hourly.get("hourly", {})
            if hourly and "time" in hourly:
                hdf = pd.DataFrame(hourly)
                hdf["datetime"] = pd.to_datetime(hdf["time"])
                hdf["date"] = hdf["datetime"].dt.normalize()
                hdf = hdf.drop(columns=["time", "datetime"])
                hdf = hdf.rename(columns=SOIL_HOURLY_COL_MAP)
                hdf = hdf.groupby("date").mean().reset_index()
                df = df.merge(hdf, on="date", how="left")
                logger.info("Merged %d days of soil data", len(hdf))
        except Exception as e:
            logger.warning("Soil data fetch failed: %s — filling with zeros", e)
            for col in SOIL_HOURLY_COL_MAP.values():
                df[col] = 0.0

        return df

    def fetch_air_quality(self, start_date: str, end_date: str) -> pd.DataFrame:
        """Fetch hourly air quality from Open-Meteo, aggregate to daily."""
        params = {
            "latitude": self.lat,
            "longitude": self.lon,
            "start_date": start_date,
            "end_date": end_date,
            "hourly": AIR_QUALITY_VARIABLES,
            "timezone": "auto",
        }
        data = _request_with_retry(AIR_QUALITY_API_URL, params)
        hourly = data.get("hourly", {})
        if not hourly or "time" not in hourly:
            logger.warning("No air quality data returned; filling with zeros")
            return pd.DataFrame()

        df = pd.DataFrame(hourly)
        df["datetime"] = pd.to_datetime(df["time"])
        df["date"] = df["datetime"].dt.date
        df = df.drop(columns=["time", "datetime"])
        df = df.rename(columns=AIR_COL_MAP)

        # Aggregate hourly → daily means
        df = df.groupby("date").mean().reset_index()
        df["date"] = pd.to_datetime(df["date"])
        return df

    def fetch_recent_weather_soil(self, past_days: int = 90) -> pd.DataFrame:
        """Fetch recent weather+soil data using Forecast API (last N days)."""
        params = {
            "latitude": self.lat,
            "longitude": self.lon,
            "past_days": past_days,
            "forecast_days": 0,
            "daily": WEATHER_DAILY_VARIABLES,
            "hourly": SOIL_HOURLY_VARIABLES,
            "timezone": "auto",
        }
        data = _request_with_retry(FORECAST_API_URL, params)
        # Daily weather
        daily = data.get("daily", {})
        if not daily or "time" not in daily:
            raise ValueError("No recent weather data returned")
        df = pd.DataFrame(daily)
        df["date"] = pd.to_datetime(df["time"])
        df = df.drop(columns=["time"])
        df = df.rename(columns=WEATHER_DAILY_COL_MAP)
        # Hourly soil → daily
        hourly = data.get("hourly", {})
        if hourly and "time" in hourly:
            hdf = pd.DataFrame(hourly)
            hdf["datetime"] = pd.to_datetime(hdf["time"])
            hdf["date"] = hdf["datetime"].dt.normalize()
            hdf = hdf.drop(columns=["time", "datetime"])
            hdf = hdf.rename(columns=SOIL_HOURLY_COL_MAP)
            hdf = hdf.groupby("date").mean().reset_index()
            df = df.merge(hdf, on="date", how="left")
        return df

    def _add_temporal_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add day-of-year sin/cos encoding."""
        doy = df["date"].dt.dayofyear.values
        df["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
        df["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
        return df

    def fetch_all(self, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetch weather+soil+air quality, merge on date, add temporal features.
        Returns a daily DataFrame with ~19 features (no satellite yet).
        """
        logger.info("Fetching weather+soil for (%.2f, %.2f) %s..%s",
                     self.lat, self.lon, start_date, end_date)
        ws_df = self.fetch_weather_soil(start_date, end_date)

        logger.info("Fetching air quality...")
        aq_df = self.fetch_air_quality(start_date, end_date)

        if aq_df.empty:
            for col in AIR_COL_MAP.values():
                ws_df[col] = 0.0
            df = ws_df
        else:
            df = ws_df.merge(aq_df, on="date", how="left")

        df = self._add_temporal_features(df)

        # Fill any NaN with column median, then 0
        for col in df.columns:
            if col == "date":
                continue
            median_val = df[col].median()
            df[col] = df[col].fillna(median_val if pd.notna(median_val) else 0.0)

        df = df.sort_values("date").reset_index(drop=True)
        logger.info("Fetched %d daily records with %d columns", len(df), len(df.columns))
        return df

    def fetch_training_data(self, years: int = 2, months: int = 0) -> pd.DataFrame:
        """Fetch historical data for training."""
        end = date.today() - timedelta(days=1)
        total_days = years * 365 + months * 30
        start = end - timedelta(days=total_days)
        return self.fetch_all(start.isoformat(), end.isoformat())
