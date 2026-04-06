import io
import json
import logging
from typing import Optional

from azure.storage.blob import BlobServiceClient

from app.config import BLOB_CONNECTION_STRING, DATA_CONTAINER, MODEL_CONTAINER

logger = logging.getLogger(__name__)


def _create_blob_client(connection_string: str, account_name: str) -> Optional[BlobServiceClient]:
    """Try connection string first, fall back to DefaultAzureCredential."""
    # Try connection string (key-based) — validate with a real API call
    if connection_string and "REPLACE" not in connection_string and "PLACEHOLDER" not in connection_string:
        try:
            client = BlobServiceClient.from_connection_string(connection_string)
            # Verify with a blob-level operation (container list_blobs)
            container = client.get_container_client(MODEL_CONTAINER)
            next(container.list_blobs(results_per_page=1).__iter__(), None)
            return client
        except Exception as e:
            logger.warning("Connection string auth failed: %s. Trying managed identity...", e)

    # Fall back to Managed Identity (works when Azure policy blocks key auth)
    try:
        import os
        account_url = f"https://{account_name}.blob.core.windows.net"
        azure_client_id = os.getenv("AZURE_CLIENT_ID", "")

        if azure_client_id:
            # Explicit client ID avoids "multiple identities" error on AKS
            from azure.identity import ManagedIdentityCredential
            credential = ManagedIdentityCredential(client_id=azure_client_id)
        else:
            from azure.identity import DefaultAzureCredential
            credential = DefaultAzureCredential()

        client = BlobServiceClient(account_url, credential=credential)
        # Validate with a blob-level operation (works with Blob Data Contributor)
        container = client.get_container_client(MODEL_CONTAINER)
        next(container.list_blobs(results_per_page=1).__iter__(), None)
        logger.info("Connected to Blob Storage via managed identity")
        return client
    except Exception as e:
        logger.warning("Managed identity auth failed: %s", e)

    return None


class BlobStorageService:
    """Upload / download files to Azure Blob Storage."""

    def __init__(self, connection_string: str = BLOB_CONNECTION_STRING):
        from app.config import BLOB_ACCOUNT_NAME
        self._client = _create_blob_client(connection_string, BLOB_ACCOUNT_NAME)
        if self._client is None:
            logger.warning("Could not connect to Blob Storage")

    def _ensure_client(self) -> BlobServiceClient:
        if self._client is None:
            raise RuntimeError(
                "BlobStorageService not connected. "
                "Check BLOB_CONNECTION_STRING or managed identity config."
            )
        return self._client

    # ------------------------------------------------------------------
    # Model files (.pt / .pkl / .json)
    # ------------------------------------------------------------------

    def upload_model(self, data: bytes, filename: str) -> None:
        client = self._ensure_client()
        blob = client.get_blob_client(container=MODEL_CONTAINER, blob=filename)
        blob.upload_blob(data, overwrite=True)
        logger.info("Uploaded model file: %s (%d bytes)", filename, len(data))

    def download_model(self, filename: str) -> bytes:
        client = self._ensure_client()
        blob = client.get_blob_client(container=MODEL_CONTAINER, blob=filename)
        return blob.download_blob().readall()

    def list_models(self, suffix: str = ".pt") -> list[str]:
        """List model files sorted by name descending (newest first)."""
        client = self._ensure_client()
        container = client.get_container_client(MODEL_CONTAINER)
        blobs = [b.name for b in container.list_blobs() if b.name.endswith(suffix)]
        blobs.sort(reverse=True)
        return blobs

    def get_latest_model_name(self, model_type: str = "lstm", city: str = "") -> Optional[str]:
        if model_type == "lstm":
            # LSTM models: *.pt files NOT prefixed with xgboost_ or arima_
            models = self.list_models(".pt")
            models = [m for m in models if not m.startswith(("xgboost_", "arima_"))]
        else:
            # XGBoost/ARIMA: {model_type}_*.pkl (not *_scaler.pkl)
            models = self.list_models(".pkl")
            models = [
                m for m in models
                if m.startswith(f"{model_type}_") and "_scaler.pkl" not in m
            ]
        if city:
            city_lower = city.lower().replace(" ", "-")
            models = [m for m in models if m.lower().startswith(f"{city_lower}_") or m.lower().startswith(f"{model_type}_{city_lower}_")]
        return models[0] if models else None

    # ------------------------------------------------------------------
    # Metrics JSON
    # ------------------------------------------------------------------

    def upload_metrics(self, metrics: dict, filename: str) -> None:
        data = json.dumps(metrics, indent=2).encode()
        self.upload_model(data, filename)

    def download_metrics(self, filename: str) -> dict:
        data = self.download_model(filename)
        return json.loads(data)

    def get_latest_metrics(self, model_type: str = "lstm", city: str = "") -> Optional[dict]:
        """Download metrics JSON for the most recent model of given type."""
        client = self._ensure_client()
        container = client.get_container_client(MODEL_CONTAINER)
        blobs = [b.name for b in container.list_blobs() if b.name.endswith("_metrics.json")]
        if model_type == "lstm":
            blobs = [b for b in blobs if not b.startswith(("xgboost_", "arima_"))]
        else:
            blobs = [b for b in blobs if b.startswith(f"{model_type}_")]
        if city:
            city_lower = city.lower().replace(" ", "-")
            blobs = [b for b in blobs if b.lower().startswith(f"{city_lower}_") or b.lower().startswith(f"{model_type}_{city_lower}_")]
        if not blobs:
            return None
        blobs.sort(reverse=True)
        return self.download_metrics(blobs[0])

    # ------------------------------------------------------------------
    # Weather data files
    # ------------------------------------------------------------------

    def upload_data(self, data: bytes, filename: str) -> None:
        client = self._ensure_client()
        blob = client.get_blob_client(container=DATA_CONTAINER, blob=filename)
        blob.upload_blob(data, overwrite=True)
        logger.info("Uploaded data file: %s", filename)

    def download_data(self, filename: str) -> bytes:
        client = self._ensure_client()
        blob = client.get_blob_client(container=DATA_CONTAINER, blob=filename)
        return blob.download_blob().readall()

    def list_data_files(self, prefix: str = "") -> list[dict]:
        """List weather data files with metadata."""
        client = self._ensure_client()
        container = client.get_container_client(DATA_CONTAINER)
        result = []
        for b in container.list_blobs(name_starts_with=prefix):
            result.append({
                "name": b.name,
                "size": b.size,
                "last_modified": b.last_modified.isoformat() if b.last_modified else None,
            })
        result.sort(key=lambda x: x["name"], reverse=True)
        return result

    def data_file_exists(self, filename: str) -> bool:
        try:
            client = self._ensure_client()
            blob = client.get_blob_client(container=DATA_CONTAINER, blob=filename)
            blob.get_blob_properties()
            return True
        except Exception:
            return False
