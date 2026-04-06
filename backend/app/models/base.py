from abc import ABC, abstractmethod
import numpy as np


class BaseWeatherModel(ABC):
    """Common interface for all weather forecasting models."""

    model_type: str = "base"

    @abstractmethod
    def fit(
        self,
        X_train: np.ndarray,
        Y_train: np.ndarray,
        X_test: np.ndarray,
        Y_test: np.ndarray,
    ) -> list[dict]:
        """
        Train the model.

        Args:
            X_train: (N, input_window, num_features)
            Y_train: (N, output_window, num_features)
            X_test:  (M, input_window, num_features)
            Y_test:  (M, output_window, num_features)

        Returns:
            Training history as list of epoch dicts.
        """

    @abstractmethod
    def predict(self, input_window: np.ndarray) -> np.ndarray:
        """
        Generate forecast from a single input window.

        Args:
            input_window: (input_window, num_features) – already scaled.

        Returns:
            (output_window, num_features) – scaled predictions.
        """

    @abstractmethod
    def save_bytes(self) -> bytes:
        """Serialize model to bytes for Blob Storage upload."""

    @abstractmethod
    def load_bytes(self, data: bytes) -> None:
        """Deserialize model from bytes downloaded from Blob Storage."""
