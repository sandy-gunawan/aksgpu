import os
import torch

# City configuration
CITY_NAME = os.getenv("CITY_NAME", "new-york")
CITY_LAT = float(os.getenv("CITY_LAT", "40.71"))
CITY_LON = float(os.getenv("CITY_LON", "-74.01"))

# Azure Blob Storage
BLOB_CONNECTION_STRING = os.getenv("BLOB_CONNECTION_STRING", "")
BLOB_ACCOUNT_NAME = os.getenv("BLOB_ACCOUNT_NAME", "stgpuweather")
MODEL_CONTAINER = os.getenv("MODEL_CONTAINER", "models")
DATA_CONTAINER = os.getenv("DATA_CONTAINER", "weather-data")
PREDICTIONS_CONTAINER = os.getenv("PREDICTIONS_CONTAINER", "predictions")

# Device — auto-detect GPU
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# Feature columns used for training and prediction
# Feature columns used for training and prediction
# Base weather features + seasonal features (sin/cos of day-of-year)
FEATURE_COLUMNS = ["temperature", "humidity", "wind_speed", "precipitation", "pressure"]
SEASONAL_FEATURES = ["day_sin", "day_cos", "hour_sin", "hour_cos"]
ALL_FEATURES = FEATURE_COLUMNS + SEASONAL_FEATURES
NUM_FEATURES = len(ALL_FEATURES)

# Model hyperparameters
INPUT_WINDOW = int(os.getenv("INPUT_WINDOW", "336"))      # 14 days of hourly data
OUTPUT_WINDOW = int(os.getenv("OUTPUT_WINDOW", "336"))     # predict next 14 days directly (no autoregressive)
HIDDEN_SIZE = int(os.getenv("HIDDEN_SIZE", "256"))
NUM_LAYERS = int(os.getenv("NUM_LAYERS", "2"))
DROPOUT = float(os.getenv("DROPOUT", "0.2"))

# Training hyperparameters
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "64"))
EPOCHS = int(os.getenv("EPOCHS", "50"))
LEARNING_RATE = float(os.getenv("LEARNING_RATE", "0.001"))
PATIENCE = int(os.getenv("PATIENCE", "10"))
TRAIN_SPLIT = 0.8

# Open-Meteo API
ARCHIVE_API_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_API_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_VARIABLES = "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation,surface_pressure"
