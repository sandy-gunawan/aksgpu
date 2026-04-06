import io
import json
import logging
from typing import Optional

from azure.storage.blob import BlobServiceClient

from app.config import BLOB_CONNECTION_STRING, DATA_CONTAINER, MODEL_CONTAINER

logger = logging.getLogger(__name__)


def _create_blob_client(connection_string: str, account_name: str) -> Optional[BlobServiceClient]:
    if connection_string and "REPLACE" not in connection_string and "PLACEHOLDER" not in connection_string:
        try:
            client = BlobServiceClient.from_connection_string(connection_string)
            container = client.get_container_client(MODEL_CONTAINER)
            next(container.list_blobs(results_per_page=1).__iter__(), None)
            return client
        except Exception as e:
            logger.warning("Connection string auth failed: %s. Trying managed identity...", e)

    try:
        import os
        account_url = f"https://{account_name}.blob.core.windows.net"
        azure_client_id = os.getenv("AZURE_CLIENT_ID", "")
        if azure_client_id:
            from azure.identity import ManagedIdentityCredential
            credential = ManagedIdentityCredential(client_id=azure_client_id)
        else:
            from azure.identity import DefaultAzureCredential
            credential = DefaultAzureCredential()
        client = BlobServiceClient(account_url, credential=credential)
        container = client.get_container_client(MODEL_CONTAINER)
        next(container.list_blobs(results_per_page=1).__iter__(), None)
        logger.info("Connected to Blob Storage via managed identity")
        return client
    except Exception as e:
        logger.warning("Managed identity auth failed: %s", e)
    return None


class BlobStorageService:
    def __init__(self, connection_string: str = BLOB_CONNECTION_STRING):
        from app.config import BLOB_ACCOUNT_NAME
        self._client = _create_blob_client(connection_string, BLOB_ACCOUNT_NAME)
        if self._client is None:
            logger.warning("Could not connect to Blob Storage")

    def _ensure_client(self) -> BlobServiceClient:
        if self._client is None:
            raise RuntimeError("BlobStorageService not connected.")
        return self._client

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
        client = self._ensure_client()
        container = client.get_container_client(MODEL_CONTAINER)
        blobs = [b.name for b in container.list_blobs() if b.name.endswith(suffix)]
        blobs.sort(reverse=True)
        return blobs

    def get_latest_model_name(self, model_type: str = "lstm", location: str = "") -> Optional[str]:
        if model_type == "lstm":
            models = self.list_models(".pt")
            models = [m for m in models if not m.startswith(("xgboost_", "arima_"))]
        else:
            models = self.list_models(".pkl")
            models = [m for m in models if m.startswith(f"{model_type}_") and "_scaler.pkl" not in m]
        if location:
            loc = location.lower().replace(" ", "-")
            models = [m for m in models if loc in m.lower()]
        return models[0] if models else None

    def upload_metrics(self, metrics: dict, filename: str) -> None:
        data = json.dumps(metrics, indent=2).encode()
        self.upload_model(data, filename)

    def get_latest_metrics(self, model_type: str = "lstm", location: str = "") -> Optional[dict]:
        client = self._ensure_client()
        container = client.get_container_client(MODEL_CONTAINER)
        blobs = [b.name for b in container.list_blobs() if b.name.endswith("_metrics.json")]
        if model_type == "lstm":
            blobs = [b for b in blobs if not b.startswith(("xgboost_", "arima_"))]
        else:
            blobs = [b for b in blobs if b.startswith(f"{model_type}_")]
        if location:
            loc = location.lower().replace(" ", "-")
            blobs = [b for b in blobs if loc in b.lower()]
        if not blobs:
            return None
        blobs.sort(reverse=True)
        data = self.download_model(blobs[0])
        return json.loads(data)

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
        client = self._ensure_client()
        container = client.get_container_client(DATA_CONTAINER)
        result = []
        for b in container.list_blobs(name_starts_with=prefix):
            result.append({"name": b.name, "size": b.size,
                           "last_modified": b.last_modified.isoformat() if b.last_modified else None})
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
