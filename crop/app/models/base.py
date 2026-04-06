from abc import ABC, abstractmethod
import numpy as np


class BaseCropModel(ABC):
    model_type: str = "base"

    @abstractmethod
    def fit(self, X_train: np.ndarray, Y_train: np.ndarray,
            X_test: np.ndarray, Y_test: np.ndarray) -> list[dict]:
        """Train. X/Y shape: (N, window, features)."""

    @abstractmethod
    def predict(self, input_window: np.ndarray) -> np.ndarray:
        """Predict from (input_window, features) → (output_window, features)."""

    @abstractmethod
    def save_bytes(self) -> bytes:
        """Serialize to bytes."""

    @abstractmethod
    def load_bytes(self, data: bytes) -> None:
        """Deserialize from bytes."""
