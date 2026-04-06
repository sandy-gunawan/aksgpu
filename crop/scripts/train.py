"""
Standalone training script for crop health models.
  python -m scripts.train --model-type lstm --location palm-riau --lat 1.5 --lon 102.1
  python -m scripts.train --model-type all
"""
import argparse
import logging
import time

import torch

from app.config import LOCATION_LAT, LOCATION_LON, LOCATION_NAME, DEVICE, ALL_FEATURES
from app.services.blob_storage import BlobStorageService
from app.services.data_fetcher import CropDataFetcher
from app.services.satellite_fetcher import SatelliteFetcher
from app.services.trainer import ModelTrainer, VALID_MODEL_TYPES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure.identity").setLevel(logging.WARNING)
logging.getLogger("azure.core").setLevel(logging.WARNING)
logger = logging.getLogger("train")


def main():
    parser = argparse.ArgumentParser(description="Train crop health prediction model")
    parser.add_argument("--location", type=str, default=LOCATION_NAME)
    parser.add_argument("--lat", type=float, default=LOCATION_LAT)
    parser.add_argument("--lon", type=float, default=LOCATION_LON)
    parser.add_argument("--years", type=int, default=2)
    parser.add_argument("--months", type=int, default=0)
    parser.add_argument("--model-type", type=str, default="all", choices=list(VALID_MODEL_TYPES) + ["all"])
    args = parser.parse_args()

    model_types = list(VALID_MODEL_TYPES) if args.model_type == "all" else [args.model_type]

    logger.info("=" * 60)
    logger.info("Crop Health Model Training")
    logger.info("Location: %s (%.2f, %.2f)", args.location, args.lat, args.lon)
    logger.info("Device: %s", DEVICE)
    if torch.cuda.is_available():
        logger.info("GPU: %s", torch.cuda.get_device_name(0))
    logger.info("Models to train: %s", ", ".join(model_types))
    logger.info("=" * 60)

    # Fetch multi-source data
    logger.info("Fetching weather + soil + air quality data...")
    fetcher = CropDataFetcher(lat=args.lat, lon=args.lon)
    df = fetcher.fetch_training_data(years=args.years, months=args.months)  

    logger.info("Fetching satellite NDVI/EVI data...")
    sat = SatelliteFetcher(lat=args.lat, lon=args.lon)
    from datetime import date, timedelta
    end = date.today() - timedelta(days=1)
    total_days = args.years * 365 + args.months * 30
    start = end - timedelta(days=total_days)
    sat_df = sat.fetch_ndvi_evi(start.isoformat(), end.isoformat())

    df = df.merge(sat_df[["date", "ndvi", "evi"]], on="date", how="left")
    df["ndvi"] = df["ndvi"].interpolate().bfill().ffill()
    df["evi"] = df["evi"].interpolate().bfill().ffill()

    for col in ALL_FEATURES:
        if col not in df.columns:
            df[col] = 0.0

    logger.info("Total training data: %d days, %d features", len(df), len(ALL_FEATURES))

    blob = BlobStorageService()
    trainer = ModelTrainer(blob_service=blob)
    total_start = time.time()

    for mt in model_types:
        logger.info("-" * 40)
        logger.info("Training %s model...", mt.upper())
        logger.info("-" * 40)
        start_time = time.time()

        try:
            if mt == "lstm":
                train_loader, test_loader, scaler = trainer.prepare_data(df)
                model, history = trainer.train(train_loader, test_loader)
            else:
                model, history, scaler = trainer.train_alternative(mt, df)

            duration = time.time() - start_time
            filename = trainer.save_model(
                model, scaler, history, duration,
                location_name=args.location, lat=args.lat, lon=args.lon,
                model_type=mt,
            )
            final = history[-1] if history else {}
            logger.info("%s complete: %s (%.1f min)", mt.upper(), filename, duration / 60)
            logger.info("  Final loss: train=%.6f  test=%.6f",
                        final.get("train_loss", 0), final.get("test_loss", 0))
        except Exception:
            logger.exception("Failed to train %s", mt)

    total_duration = time.time() - total_start
    logger.info("=" * 60)
    logger.info("All training complete!")
    logger.info("Models trained: %s", ", ".join(mt.upper() for mt in model_types))
    logger.info("Location: %s (%.2f, %.2f)", args.location, args.lat, args.lon)
    logger.info("Total duration: %.1f minutes", total_duration / 60)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
