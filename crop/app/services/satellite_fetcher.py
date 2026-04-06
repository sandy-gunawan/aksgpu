"""
NASA MODIS satellite data fetcher via ORNL DAAC TESViS REST API.

Fetches NDVI, EVI (MOD13Q1, 16-day, 250m) and interpolates to daily.
Free service — requires no API key for the subset/point API.
"""
import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests

from app.config import MODIS_API_URL

logger = logging.getLogger(__name__)

# MOD13Q1 = MODIS/Terra Vegetation Indices 16-Day L3 250m
MODIS_PRODUCT = "MOD13Q1"
MODIS_BAND_NDVI = "250m_16_days_NDVI"
MODIS_BAND_EVI = "250m_16_days_EVI"


def _fetch_modis_band(lat: float, lon: float, product: str, band: str,
                       start_date: str, end_date: str) -> list[dict]:
    """Fetch a single MODIS band time series for a point."""
    url = f"{MODIS_API_URL}/{product}/subset"
    params = {
        "latitude": lat,
        "longitude": lon,
        "band": band,
        "startDate": f"A{start_date.replace('-', '')}",
        "endDate": f"A{end_date.replace('-', '')}",
        "kmAboveBelow": 0,
        "kmLeftRight": 0,
    }
    headers = {"Accept": "application/json"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        return data.get("subset", [])
    except Exception as e:
        logger.warning("MODIS API error for %s/%s: %s", product, band, e)
        return []


def _parse_modis_date(modis_date: str) -> date:
    """Parse MODIS date format 'Ayyyyddd' to Python date."""
    year = int(modis_date[1:5])
    doy = int(modis_date[5:])
    return date(year, 1, 1) + timedelta(days=doy - 1)


class SatelliteFetcher:
    """Fetches NDVI/EVI from NASA MODIS and interpolates to daily."""

    def __init__(self, lat: float, lon: float):
        self.lat = lat
        self.lon = lon

    def fetch_ndvi_evi(self, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetch NDVI and EVI from MODIS MOD13Q1.
        Returns daily-interpolated DataFrame with 'date', 'ndvi', 'evi'.
        """
        logger.info("Fetching MODIS NDVI/EVI for (%.2f, %.2f) %s..%s",
                     self.lat, self.lon, start_date, end_date)

        ndvi_data = _fetch_modis_band(self.lat, self.lon, MODIS_PRODUCT,
                                       MODIS_BAND_NDVI, start_date, end_date)
        evi_data = _fetch_modis_band(self.lat, self.lon, MODIS_PRODUCT,
                                      MODIS_BAND_EVI, start_date, end_date)

        if not ndvi_data and not evi_data:
            logger.warning("No MODIS data returned; generating synthetic NDVI/EVI")
            return self._generate_synthetic(start_date, end_date)

        records = []
        for entry in ndvi_data:
            try:
                d = _parse_modis_date(entry["calendar_date"] if "calendar_date" in entry
                                       else entry.get("modis_date", ""))
                # MODIS NDVI is scaled by 10000
                val = float(entry.get("data", [0])[0] if isinstance(entry.get("data"), list)
                            else entry.get("value", 0))
                if val > 1:
                    val = val / 10000.0
                # Quality filter: valid NDVI range
                if -0.2 <= val <= 1.0:
                    records.append({"date": d, "ndvi": val})
            except (ValueError, KeyError, IndexError):
                continue

        evi_map = {}
        for entry in evi_data:
            try:
                d = _parse_modis_date(entry["calendar_date"] if "calendar_date" in entry
                                       else entry.get("modis_date", ""))
                val = float(entry.get("data", [0])[0] if isinstance(entry.get("data"), list)
                            else entry.get("value", 0))
                if val > 1:
                    val = val / 10000.0
                if -0.2 <= val <= 1.0:
                    evi_map[d] = val
            except (ValueError, KeyError, IndexError):
                continue

        if not records:
            logger.warning("No valid MODIS records parsed; using synthetic data")
            return self._generate_synthetic(start_date, end_date)

        df = pd.DataFrame(records)
        df["evi"] = df["date"].map(evi_map).fillna(df["ndvi"] * 0.9)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").drop_duplicates(subset="date")

        # Interpolate 16-day data to daily
        date_range = pd.date_range(start=start_date, end=end_date, freq="D")
        daily = pd.DataFrame({"date": date_range})
        daily = daily.merge(df, on="date", how="left")
        daily["ndvi"] = daily["ndvi"].interpolate(method="linear").bfill().ffill()
        daily["evi"] = daily["evi"].interpolate(method="linear").bfill().ffill()

        logger.info("MODIS: %d raw observations → %d daily interpolated", len(df), len(daily))
        return daily

    def _generate_synthetic(self, start_date: str, end_date: str) -> pd.DataFrame:
        """Generate synthetic NDVI/EVI using seasonal patterns when MODIS is unavailable."""
        date_range = pd.date_range(start=start_date, end=end_date, freq="D")
        doy = date_range.dayofyear.values
        # Tropical vegetation: relatively stable NDVI ~0.6-0.8 with slight seasonal variation
        ndvi = 0.70 + 0.08 * np.sin(2 * np.pi * doy / 365.25) + np.random.normal(0, 0.02, len(doy))
        ndvi = np.clip(ndvi, 0.1, 0.95)
        evi = ndvi * 0.85 + np.random.normal(0, 0.01, len(doy))
        evi = np.clip(evi, 0.1, 0.90)
        return pd.DataFrame({"date": date_range, "ndvi": ndvi, "evi": evi})
