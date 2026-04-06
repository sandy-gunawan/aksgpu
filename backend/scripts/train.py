"""
Standalone training script -- run as:  python -m scripts.train
Used by the Kubernetes CronJob for weekly re-training.

Supports custom city/coordinates and model type via CLI args or env vars:
  python -m scripts.train --city tokyo --lat 35.68 --lon 139.69
  python -m scripts.train --model-type xgboost
  python -m scripts.train --model-type arima --city london --lat 51.51 --lon -0.13
  python -m scripts.train   # uses defaults: LSTM, CITY_NAME/CITY_LAT/CITY_LON from env
"""
import argparse
import logging
import time

import torch

from app.config import CITY_LAT, CITY_LON, CITY_NAME, DEVICE
from app.services.blob_storage import BlobStorageService
from app.services.data_fetcher import WeatherDataFetcher
from app.services.trainer import ModelTrainer, VALID_MODEL_TYPES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
# Silence verbose Azure SDK HTTP logging
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure.identity").setLevel(logging.WARNING)
logging.getLogger("azure.core").setLevel(logging.WARNING)
logger = logging.getLogger("train")


def main():
    parser = argparse.ArgumentParser(description="Train weather prediction model")
    parser.add_argument("--city", type=str, default=CITY_NAME, help="City name for model labeling")
    parser.add_argument("--lat", type=float, default=CITY_LAT, help="Latitude (-90 to 90)")
    parser.add_argument("--lon", type=float, default=CITY_LON, help="Longitude (-180 to 180)")
    parser.add_argument("--years", type=int, default=1, help="Years of historical data (default: 1)")
    parser.add_argument("--months", type=int, default=0, help="Additional months of data (e.g. --years 0 --months 6 for 6 months)")
    parser.add_argument(
        "--model-type", type=str, default="all",
        choices=["all"] + list(VALID_MODEL_TYPES),
        help="Model type to train: all (default), lstm, xgboost, or arima",
    )
    args = parser.parse_args()

    city_name = args.city
    lat = args.lat
    lon = args.lon
    model_type = args.model_type

    # Determine which models to train
    if model_type == "all":
        model_types = list(VALID_MODEL_TYPES)
    else:
        model_types = [model_type]

    logger.info("=" * 60)
    logger.info("GPU Weather Model Training")
    logger.info("=" * 60)
    logger.info("Models    : %s", ", ".join(mt.upper() for mt in model_types))
    logger.info("City      : %s", city_name)
    logger.info("Latitude  : %s", lat)
    logger.info("Longitude : %s", lon)
    logger.info("Years     : %d", args.years)
    logger.info("Months    : %d", args.months)
    logger.info("Device    : %s", DEVICE)
    if torch.cuda.is_available():
        logger.info("GPU       : %s", torch.cuda.get_device_name(0))
        logger.info("VRAM      : %.1f GB", torch.cuda.get_device_properties(0).total_memory / 1e9)
    else:
        logger.warning("No GPU detected -- training will be slow on CPU")

    total_start = time.time()

    # 1. Fetch data once (shared across all models)
    fetcher = WeatherDataFetcher(lat=lat, lon=lon)
    df = fetcher.fetch_training_data(years=args.years, months=args.months)

    blob = BlobStorageService()
    trainer = ModelTrainer(blob_service=blob)

    # 2. Train each model type
    for mt in model_types:
        logger.info("-" * 40)
        logger.info("Training %s model...", mt.upper())
        logger.info("-" * 40)

        start = time.time()

        if mt == "lstm":
            train_loader, test_loader, scaler = trainer.prepare_data(df)
            model, history = trainer.train(train_loader, test_loader)
        else:
            model, history, scaler = trainer.train_alternative(mt, df)

        elapsed = time.time() - start
        model_file = trainer.save_model(
            model, scaler, history, elapsed,
            city_name=city_name, lat=lat, lon=lon,
            model_type=mt,
        )

        logger.info("%s complete: %s (%.1f min)", mt.upper(), model_file, elapsed / 60)
        if history:
            logger.info("  Final loss: train=%.6f  test=%.6f",
                         history[-1].get("train_loss", 0), history[-1].get("test_loss", 0))

    total_elapsed = time.time() - total_start
    logger.info("=" * 60)
    logger.info("All training complete!")
    logger.info("Models trained: %s", ", ".join(mt.upper() for mt in model_types))
    logger.info("City          : %s (%.2f, %.2f)", city_name, lat, lon)
    logger.info("Total duration: %.1f minutes", total_elapsed / 60)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
