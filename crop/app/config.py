import os
import torch

# Default location — Riau, Indonesia (palm oil region)
LOCATION_NAME = os.getenv("LOCATION_NAME", "riau-indonesia")
LOCATION_LAT = float(os.getenv("LOCATION_LAT", "1.50"))
LOCATION_LON = float(os.getenv("LOCATION_LON", "102.10"))

# Azure Blob Storage
BLOB_CONNECTION_STRING = os.getenv("BLOB_CONNECTION_STRING", "")
BLOB_ACCOUNT_NAME = os.getenv("BLOB_ACCOUNT_NAME", "stgpuweather")
MODEL_CONTAINER = os.getenv("MODEL_CONTAINER", "crop-models")
DATA_CONTAINER = os.getenv("DATA_CONTAINER", "crop-data")

# Device
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# --- Data Sources ---
# Open-Meteo: weather (daily aggregates) — archive API
ARCHIVE_API_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_API_URL = "https://api.open-meteo.com/v1/forecast"

# Daily variables for weather (archive API supports these as daily)
WEATHER_DAILY_VARIABLES = (
    "temperature_2m_max,temperature_2m_min,temperature_2m_mean,"
    "precipitation_sum,wind_speed_10m_max,"
    "shortwave_radiation_sum,et0_fao_evapotranspiration"
)

# Hourly variables for soil (not available as daily, must aggregate)
SOIL_HOURLY_VARIABLES = (
    "relative_humidity_2m,"
    "soil_moisture_0_to_7cm,soil_moisture_7_to_28cm,soil_moisture_28_to_100cm,"
    "soil_temperature_0_to_7cm,soil_temperature_7_to_28cm"
)

# Open-Meteo: air quality (separate endpoint)
AIR_QUALITY_API_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
AIR_QUALITY_VARIABLES = "pm2_5,pm10,ozone,uv_index,dust"

# NASA MODIS (ORNL DAAC TESViS REST API)
MODIS_API_URL = "https://modis.ornl.gov/rst/api/v1"

# --- Feature Definitions ---
WEATHER_FEATURES = [
    "temp_max", "temp_min", "temp_mean",
    "precipitation", "wind_max", "radiation", "et0",
]
SOIL_FEATURES = [
    "humidity",
    "soil_moisture_0_7cm", "soil_moisture_7_28cm", "soil_moisture_28_100cm",
    "soil_temp_0_7cm", "soil_temp_7_28cm",
]
AIR_FEATURES = ["pm2_5", "ozone", "uv_index", "dust"]
SATELLITE_FEATURES = ["ndvi", "evi"]
TEMPORAL_FEATURES = ["doy_sin", "doy_cos"]

ALL_FEATURES = WEATHER_FEATURES + SOIL_FEATURES + AIR_FEATURES + SATELLITE_FEATURES + TEMPORAL_FEATURES
NUM_FEATURES = len(ALL_FEATURES)  # 21

# Column mapping: Open-Meteo daily API names → internal names
WEATHER_DAILY_COL_MAP = {
    "temperature_2m_max": "temp_max",
    "temperature_2m_min": "temp_min",
    "temperature_2m_mean": "temp_mean",
    "precipitation_sum": "precipitation",
    "wind_speed_10m_max": "wind_max",
    "shortwave_radiation_sum": "radiation",
    "et0_fao_evapotranspiration": "et0",
}
# Column mapping for hourly soil variables (aggregated to daily mean)
SOIL_HOURLY_COL_MAP = {
    "relative_humidity_2m": "humidity",
    "soil_moisture_0_to_7cm": "soil_moisture_0_7cm",
    "soil_moisture_7_to_28cm": "soil_moisture_7_28cm",
    "soil_moisture_28_to_100cm": "soil_moisture_28_100cm",
    "soil_temperature_0_to_7cm": "soil_temp_0_7cm",
    "soil_temperature_7_to_28cm": "soil_temp_7_28cm",
}
AIR_COL_MAP = {
    "pm2_5": "pm2_5",
    "ozone": "ozone",
    "uv_index": "uv_index",
    "dust": "dust",
}

# --- Model Hyperparameters ---
INPUT_WINDOW = int(os.getenv("INPUT_WINDOW", "60"))    # 60 days lookback
OUTPUT_WINDOW = int(os.getenv("OUTPUT_WINDOW", "32"))   # predict 32 days ahead
HIDDEN_SIZE = int(os.getenv("HIDDEN_SIZE", "128"))
NUM_LAYERS = int(os.getenv("NUM_LAYERS", "2"))
DROPOUT = float(os.getenv("DROPOUT", "0.2"))

# Training
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "32"))
EPOCHS = int(os.getenv("EPOCHS", "80"))
LEARNING_RATE = float(os.getenv("LEARNING_RATE", "0.001"))
PATIENCE = int(os.getenv("PATIENCE", "15"))
TRAIN_SPLIT = 0.8

# Crop presets
CROP_PRESETS = {
    "palm-riau": {"name": "Palm Oil - Riau, Indonesia", "lat": 1.50, "lon": 102.10, "crop": "palm"},
    "palm-kalimantan": {"name": "Palm Oil - Central Kalimantan", "lat": -1.68, "lon": 113.38, "crop": "palm"},
    "palm-sabah": {"name": "Palm Oil - Sabah, Malaysia", "lat": 5.30, "lon": 117.60, "crop": "palm"},
    "rice-mekong": {"name": "Rice - Mekong Delta, Vietnam", "lat": 10.03, "lon": 105.78, "crop": "rice"},
    "corn-iowa": {"name": "Corn - Iowa, USA", "lat": 42.03, "lon": -93.47, "crop": "corn"},
    "wheat-punjab": {"name": "Wheat - Punjab, India", "lat": 30.90, "lon": 75.85, "crop": "wheat"},
}
