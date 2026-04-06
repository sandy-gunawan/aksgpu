import logging

import torch
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import DEVICE
from app.routers import predict, training, validate
from app.routers.data import router as data_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure.identity").setLevel(logging.WARNING)
logging.getLogger("azure.core").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

app = FastAPI(title="GPU Crop Health Prediction API", version="1.0.0")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(predict.router)
app.include_router(validate.router)
app.include_router(training.router)
app.include_router(data_router)


@app.on_event("startup")
async def startup_load_model():
    logger.info("Device: %s", DEVICE)
    if torch.cuda.is_available():
        logger.info("CUDA device: %s", torch.cuda.get_device_name(0))
    try:
        results = predict.predictor.load_all_models()
        for mt, ok in results.items():
            if ok:
                logger.info("Loaded %s model at startup", mt)
            else:
                logger.info("No %s model available", mt)
    except Exception:
        logger.warning("No models loaded at startup — train one first")


@app.get("/api/crop/health")
def health():
    gpu_available = torch.cuda.is_available()
    return {
        "status": "healthy",
        "service": "crop-health",
        "gpu_available": gpu_available,
        "loaded_models": predict.predictor.loaded_model_types(),
        "cuda_device": torch.cuda.get_device_name(0) if gpu_available else None,
    }
