import io
import logging
import time
from datetime import datetime

import joblib
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.preprocessing import MinMaxScaler
from torch.utils.data import DataLoader, TensorDataset

from app.config import (
    ALL_FEATURES,
    BATCH_SIZE,
    CITY_NAME,
    DEVICE,
    DROPOUT,
    EPOCHS,
    HIDDEN_SIZE,
    INPUT_WINDOW,
    LEARNING_RATE,
    NUM_FEATURES,
    NUM_LAYERS,
    OUTPUT_WINDOW,
    PATIENCE,
    TRAIN_SPLIT,
)
from app.models.lstm_model import WeatherLSTM
from app.services.blob_storage import BlobStorageService

logger = logging.getLogger(__name__)

VALID_MODEL_TYPES = ("lstm", "xgboost", "arima")


class ModelTrainer:
    """Handles data preparation, training, and saving for LSTM, XGBoost, and ARIMA models."""

    def __init__(self, blob_service: BlobStorageService | None = None):
        self.blob = blob_service or BlobStorageService()
        self.scaler = MinMaxScaler()

    # ------------------------------------------------------------------
    # Data preparation
    # ------------------------------------------------------------------

    def prepare_data(
        self, df: pd.DataFrame
    ) -> tuple[DataLoader, DataLoader, MinMaxScaler]:
        """
        Normalise features, create sliding-window samples, split, return loaders.
        """
        features = df[ALL_FEATURES].values.astype(np.float32)

        # Fit scaler on entire dataset then transform
        scaled = self.scaler.fit_transform(features)

        # Build sliding windows
        X_list, Y_list = [], []
        total = len(scaled) - INPUT_WINDOW - OUTPUT_WINDOW + 1
        for i in range(total):
            X_list.append(scaled[i : i + INPUT_WINDOW])
            Y_list.append(scaled[i + INPUT_WINDOW : i + INPUT_WINDOW + OUTPUT_WINDOW])

        X = np.array(X_list, dtype=np.float32)
        Y = np.array(Y_list, dtype=np.float32)
        logger.info("Created %d sliding-window samples", len(X))

        # Train / test split (chronological — no shuffle before split)
        split_idx = int(len(X) * TRAIN_SPLIT)
        X_train, X_test = X[:split_idx], X[split_idx:]
        Y_train, Y_test = Y[:split_idx], Y[split_idx:]

        train_ds = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(Y_train))
        test_ds = TensorDataset(torch.from_numpy(X_test), torch.from_numpy(Y_test))

        train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True)
        test_loader = DataLoader(test_ds, batch_size=BATCH_SIZE, shuffle=False)

        return train_loader, test_loader, self.scaler

    def prepare_arrays(
        self, df: pd.DataFrame
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, MinMaxScaler]:
        """
        Prepare numpy arrays (not DataLoaders) for XGBoost / ARIMA training.
        Returns (X_train, Y_train, X_test, Y_test, scaler).
        """
        features = df[ALL_FEATURES].values.astype(np.float32)
        scaled = self.scaler.fit_transform(features)

        X_list, Y_list = [], []
        total = len(scaled) - INPUT_WINDOW - OUTPUT_WINDOW + 1
        for i in range(total):
            X_list.append(scaled[i : i + INPUT_WINDOW])
            Y_list.append(scaled[i + INPUT_WINDOW : i + INPUT_WINDOW + OUTPUT_WINDOW])

        X = np.array(X_list, dtype=np.float32)
        Y = np.array(Y_list, dtype=np.float32)
        logger.info("Created %d sliding-window samples (arrays)", len(X))

        split_idx = int(len(X) * TRAIN_SPLIT)
        return X[:split_idx], Y[:split_idx], X[split_idx:], Y[split_idx:], self.scaler

    # ------------------------------------------------------------------
    # Training loop — LSTM
    # ------------------------------------------------------------------

    def train(
        self,
        train_loader: DataLoader,
        test_loader: DataLoader,
    ) -> tuple[WeatherLSTM, list[dict]]:
        """
        Train the LSTM model with early stopping.
        Returns (model, history) where history is a list of per-epoch dicts.
        """
        model = WeatherLSTM(
            num_features=NUM_FEATURES,
            hidden_size=HIDDEN_SIZE,
            num_layers=NUM_LAYERS,
            dropout=DROPOUT,
            output_window=OUTPUT_WINDOW,
        ).to(DEVICE)

        criterion = nn.MSELoss()
        optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer, mode="min", factor=0.5, patience=5
        )

        best_test_loss = float("inf")
        best_state = None
        epochs_no_improve = 0
        history: list[dict] = []

        logger.info(
            "Starting training — device=%s, epochs=%d, patience=%d",
            DEVICE, EPOCHS, PATIENCE,
        )

        for epoch in range(1, EPOCHS + 1):
            # --- Train ---
            model.train()
            train_loss_sum = 0.0
            for batch_x, batch_y in train_loader:
                batch_x = batch_x.to(DEVICE)
                batch_y = batch_y.to(DEVICE)
                outputs = model(batch_x)
                loss = criterion(outputs, batch_y)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                train_loss_sum += loss.item() * batch_x.size(0)
            train_loss = train_loss_sum / len(train_loader.dataset)

            # --- Evaluate ---
            test_loss = self._evaluate(model, test_loader, criterion)
            scheduler.step(test_loss)

            history.append(
                {"epoch": epoch, "train_loss": train_loss, "test_loss": test_loss}
            )
            logger.info(
                "Epoch %d/%d: train_loss=%.6f, test_loss=%.6f",
                epoch, EPOCHS, train_loss, test_loss,
            )

            # --- Early stopping ---
            if test_loss < best_test_loss:
                best_test_loss = test_loss
                best_state = model.state_dict().copy()
                epochs_no_improve = 0
            else:
                epochs_no_improve += 1
                if epochs_no_improve >= PATIENCE:
                    logger.info("Early stopping at epoch %d", epoch)
                    break

        # Restore best weights
        if best_state is not None:
            model.load_state_dict(best_state)
        return model, history

    # ------------------------------------------------------------------
    # Training — XGBoost / ARIMA
    # ------------------------------------------------------------------

    def train_alternative(
        self, model_type: str, df: pd.DataFrame
    ) -> tuple[object, list[dict], MinMaxScaler]:
        """
        Train XGBoost or ARIMA model.
        Returns (model, history, scaler).
        """
        X_train, Y_train, X_test, Y_test, scaler = self.prepare_arrays(df)

        if model_type == "xgboost":
            from app.models.xgboost_model import WeatherXGBoost
            model = WeatherXGBoost()
        elif model_type == "arima":
            from app.models.arima_model import WeatherARIMA
            model = WeatherARIMA()
        else:
            raise ValueError(f"Unknown model type: {model_type}")

        history = model.fit(X_train, Y_train, X_test, Y_test)
        return model, history, scaler

    # ------------------------------------------------------------------
    # Save model artifacts to Blob Storage
    # ------------------------------------------------------------------

    def save_model(
        self,
        model,
        scaler: MinMaxScaler,
        history: list[dict],
        duration_seconds: float,
        city_name: str = CITY_NAME,
        lat: float | None = None,
        lon: float | None = None,
        model_type: str = "lstm",
    ) -> str:
        """Save model, scaler, and metrics to Blob Storage. Returns the model filename."""
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

        if model_type == "lstm":
            base = f"{city_name}_{timestamp}"
            # 1. Model weights
            buf = io.BytesIO()
            torch.save(model.state_dict(), buf)
            self.blob.upload_model(buf.getvalue(), f"{base}.pt")
            model_filename = f"{base}.pt"
        else:
            base = f"{model_type}_{city_name}_{timestamp}"
            # 1. Model bytes
            self.blob.upload_model(model.save_bytes(), f"{base}.pkl")
            model_filename = f"{base}.pkl"

        # 2. Scaler
        buf = io.BytesIO()
        joblib.dump(scaler, buf)
        self.blob.upload_model(buf.getvalue(), f"{base}_scaler.pkl")

        # 3. Metrics
        final = history[-1] if history else {}
        metrics = {
            "model_type": model_type,
            "city": city_name,
            "lat": lat,
            "lon": lon,
            "timestamp": timestamp,
            "epochs_completed": len(history),
            "final_train_loss": final.get("train_loss"),
            "final_test_loss": final.get("test_loss"),
            "duration_minutes": round(duration_seconds / 60, 1),
            "device": DEVICE if model_type == "lstm" else ("cuda" if DEVICE == "cuda" else "cpu"),
            "input_window": INPUT_WINDOW,
            "output_window": OUTPUT_WINDOW,
            "hidden_size": HIDDEN_SIZE if model_type == "lstm" else None,
            "num_layers": NUM_LAYERS if model_type == "lstm" else None,
            "batch_size": BATCH_SIZE,
        }
        self.blob.upload_metrics(metrics, f"{base}_metrics.json")

        logger.info("Saved %s model artifacts: %s", model_type, base)
        return model_filename

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _evaluate(
        model: WeatherLSTM, loader: DataLoader, criterion: nn.Module
    ) -> float:
        model.eval()
        loss_sum = 0.0
        with torch.no_grad():
            for bx, by in loader:
                bx, by = bx.to(DEVICE), by.to(DEVICE)
                out = model(bx)
                loss_sum += criterion(out, by).item() * bx.size(0)
        return loss_sum / len(loader.dataset)
