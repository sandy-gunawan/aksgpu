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
    ALL_FEATURES, BATCH_SIZE, DEVICE, DROPOUT, EPOCHS, HIDDEN_SIZE,
    INPUT_WINDOW, LEARNING_RATE, LOCATION_NAME, NUM_FEATURES, NUM_LAYERS,
    OUTPUT_WINDOW, PATIENCE, TRAIN_SPLIT,
)
from app.models.lstm_model import CropLSTM
from app.services.blob_storage import BlobStorageService

logger = logging.getLogger(__name__)

VALID_MODEL_TYPES = ("lstm", "xgboost", "arima")


class ModelTrainer:
    def __init__(self, blob_service: BlobStorageService | None = None):
        self.blob = blob_service or BlobStorageService()
        self.scaler = MinMaxScaler()

    def prepare_data(self, df: pd.DataFrame) -> tuple[DataLoader, DataLoader, MinMaxScaler]:
        features = df[ALL_FEATURES].values.astype(np.float32)
        scaled = self.scaler.fit_transform(features)
        X_list, Y_list = [], []
        total = len(scaled) - INPUT_WINDOW - OUTPUT_WINDOW + 1
        for i in range(total):
            X_list.append(scaled[i:i + INPUT_WINDOW])
            Y_list.append(scaled[i + INPUT_WINDOW:i + INPUT_WINDOW + OUTPUT_WINDOW])
        X = np.array(X_list, dtype=np.float32)
        Y = np.array(Y_list, dtype=np.float32)
        logger.info("Created %d sliding-window samples", len(X))
        split_idx = int(len(X) * TRAIN_SPLIT)
        train_ds = TensorDataset(torch.from_numpy(X[:split_idx]), torch.from_numpy(Y[:split_idx]))
        test_ds = TensorDataset(torch.from_numpy(X[split_idx:]), torch.from_numpy(Y[split_idx:]))
        return (DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True),
                DataLoader(test_ds, batch_size=BATCH_SIZE, shuffle=False),
                self.scaler)

    def prepare_arrays(self, df: pd.DataFrame):
        features = df[ALL_FEATURES].values.astype(np.float32)
        scaled = self.scaler.fit_transform(features)
        X_list, Y_list = [], []
        total = len(scaled) - INPUT_WINDOW - OUTPUT_WINDOW + 1
        for i in range(total):
            X_list.append(scaled[i:i + INPUT_WINDOW])
            Y_list.append(scaled[i + INPUT_WINDOW:i + INPUT_WINDOW + OUTPUT_WINDOW])
        X = np.array(X_list, dtype=np.float32)
        Y = np.array(Y_list, dtype=np.float32)
        logger.info("Created %d sliding-window samples (arrays)", len(X))
        split_idx = int(len(X) * TRAIN_SPLIT)
        return X[:split_idx], Y[:split_idx], X[split_idx:], Y[split_idx:], self.scaler

    def train(self, train_loader, test_loader) -> tuple[CropLSTM, list[dict]]:
        model = CropLSTM(num_features=NUM_FEATURES, hidden_size=HIDDEN_SIZE,
                         num_layers=NUM_LAYERS, dropout=DROPOUT,
                         output_window=OUTPUT_WINDOW).to(DEVICE)
        criterion = nn.MSELoss()
        optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="min", factor=0.5, patience=5)
        best_test_loss = float("inf")
        best_state = None
        epochs_no_improve = 0
        history = []
        logger.info("Training — device=%s, epochs=%d, patience=%d", DEVICE, EPOCHS, PATIENCE)
        for epoch in range(1, EPOCHS + 1):
            model.train()
            train_loss_sum = 0.0
            for bx, by in train_loader:
                bx, by = bx.to(DEVICE), by.to(DEVICE)
                out = model(bx)
                loss = criterion(out, by)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                train_loss_sum += loss.item() * bx.size(0)
            train_loss = train_loss_sum / len(train_loader.dataset)
            test_loss = self._evaluate(model, test_loader, criterion)
            scheduler.step(test_loss)
            history.append({"epoch": epoch, "train_loss": train_loss, "test_loss": test_loss})
            logger.info("Epoch %d/%d: train=%.6f test=%.6f", epoch, EPOCHS, train_loss, test_loss)
            if test_loss < best_test_loss:
                best_test_loss = test_loss
                best_state = model.state_dict().copy()
                epochs_no_improve = 0
            else:
                epochs_no_improve += 1
                if epochs_no_improve >= PATIENCE:
                    logger.info("Early stopping at epoch %d", epoch)
                    break
        if best_state:
            model.load_state_dict(best_state)
        return model, history

    def train_alternative(self, model_type: str, df: pd.DataFrame):
        X_train, Y_train, X_test, Y_test, scaler = self.prepare_arrays(df)
        if model_type == "xgboost":
            from app.models.xgboost_model import CropXGBoost
            model = CropXGBoost()
        elif model_type == "arima":
            from app.models.arima_model import CropARIMA
            model = CropARIMA()
        else:
            raise ValueError(f"Unknown model type: {model_type}")
        history = model.fit(X_train, Y_train, X_test, Y_test)
        return model, history, scaler

    def save_model(self, model, scaler, history, duration_seconds,
                   location_name=LOCATION_NAME, lat=None, lon=None, model_type="lstm"):
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        if model_type == "lstm":
            base = f"{location_name}_{timestamp}"
            buf = io.BytesIO()
            torch.save(model.state_dict(), buf)
            self.blob.upload_model(buf.getvalue(), f"{base}.pt")
            model_filename = f"{base}.pt"
        else:
            base = f"{model_type}_{location_name}_{timestamp}"
            self.blob.upload_model(model.save_bytes(), f"{base}.pkl")
            model_filename = f"{base}.pkl"
        buf = io.BytesIO()
        joblib.dump(scaler, buf)
        self.blob.upload_model(buf.getvalue(), f"{base}_scaler.pkl")
        final = history[-1] if history else {}
        metrics = {
            "model_type": model_type, "location": location_name, "lat": lat, "lon": lon,
            "timestamp": timestamp, "epochs_completed": len(history),
            "final_train_loss": final.get("train_loss"),
            "final_test_loss": final.get("test_loss"),
            "duration_minutes": round(duration_seconds / 60, 1),
            "device": DEVICE if model_type == "lstm" else ("cuda" if DEVICE == "cuda" else "cpu"),
            "input_window": INPUT_WINDOW, "output_window": OUTPUT_WINDOW,
            "hidden_size": HIDDEN_SIZE if model_type == "lstm" else None,
            "num_features": NUM_FEATURES, "features": ALL_FEATURES,
        }
        self.blob.upload_metrics(metrics, f"{base}_metrics.json")
        logger.info("Saved %s model artifacts: %s", model_type, base)
        return model_filename

    @staticmethod
    def _evaluate(model, loader, criterion):
        model.eval()
        loss_sum = 0.0
        with torch.no_grad():
            for bx, by in loader:
                bx, by = bx.to(DEVICE), by.to(DEVICE)
                loss_sum += criterion(model(bx), by).item() * bx.size(0)
        return loss_sum / len(loader.dataset)
