import io
import json
import logging
import os
import time
from datetime import date, timedelta

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from app.config import LOCATION_LAT, LOCATION_LON, LOCATION_NAME, ALL_FEATURES, CROP_PRESETS
from app.services.blob_storage import BlobStorageService
from app.services.data_fetcher import CropDataFetcher
from app.services.satellite_fetcher import SatelliteFetcher
from app.services.predictor import VALID_MODEL_TYPES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/crop/training", tags=["crop-training"])

blob = BlobStorageService()

_k8s_loaded = False
_batch_v1 = None
NAMESPACE = "gpu-weather"
TRAINER_IMAGE = os.getenv("CROP_TRAINER_IMAGE", "crop-api:v1")


def _load_k8s():
    global _k8s_loaded, _batch_v1
    if _k8s_loaded:
        return
    try:
        from kubernetes import client, config
        config.load_incluster_config()
        _batch_v1 = client.BatchV1Api()
        _k8s_loaded = True
        logger.info("Kubernetes client loaded (in-cluster)")
    except Exception as exc:
        logger.warning("K8s client not available: %s", exc)


def _create_training_job(model_type: str, location: str, lat: float, lon: float) -> dict:
    from kubernetes import client

    _load_k8s()
    if _batch_v1 is None:
        raise HTTPException(503, "Kubernetes API not available")

    ts = time.strftime("%Y%m%d-%H%M%S")
    job_name = f"crop-{model_type}-{ts}"
    cmd = ["python", "-m", "scripts.train", "--model-type", model_type,
           "--location", location, "--lat", str(lat), "--lon", str(lon)]

    job = client.V1Job(
        api_version="batch/v1", kind="Job",
        metadata=client.V1ObjectMeta(name=job_name, namespace=NAMESPACE,
                                      labels={"app": "crop-training", "model-type": model_type}),
        spec=client.V1JobSpec(
            backoff_limit=2, active_deadline_seconds=7200,
            template=client.V1PodTemplateSpec(
                spec=client.V1PodSpec(
                    containers=[client.V1Container(
                        name="trainer", image=TRAINER_IMAGE, image_pull_policy="Always",
                        command=cmd,
                        resources=client.V1ResourceRequirements(
                            requests={"cpu": "2", "memory": "8Gi", "nvidia.com/gpu": "1"},
                            limits={"cpu": "4", "memory": "16Gi", "nvidia.com/gpu": "1"},
                        ),
                        env_from=[
                            client.V1EnvFromSource(config_map_ref=client.V1ConfigMapEnvSource(name="crop-config")),
                            client.V1EnvFromSource(secret_ref=client.V1SecretEnvSource(name="weather-secrets")),
                        ],
                    )],
                    tolerations=[client.V1Toleration(key="sku", operator="Equal",
                                                      value="gpu", effect="NoSchedule")],
                    node_selector={"kubernetes.azure.com/accelerator": "nvidia"},
                    restart_policy="OnFailure",
                )
            ),
        ),
    )
    _batch_v1.create_namespaced_job(namespace=NAMESPACE, body=job)
    return {"job_name": job_name, "command": " ".join(cmd)}


@router.get("/status/all")
def get_all_training_status(location: str = Query(default="")):
    results = {}
    for mt in VALID_MODEL_TYPES:
        try:
            metrics = blob.get_latest_metrics(model_type=mt, location=location)
        except Exception:
            metrics = None
        if metrics is None:
            results[mt] = {"status": "no_model", "model_type": mt}
        else:
            model_name = blob.get_latest_model_name(model_type=mt, location=location)
            results[mt] = {
                "status": "ready", "model_type": mt,
                "last_trained": metrics.get("timestamp"),
                "model_file": model_name,
                "duration_minutes": metrics.get("duration_minutes"),
                "final_loss": metrics.get("final_test_loss"),
                "epochs_completed": metrics.get("epochs_completed"),
                "device": metrics.get("device"),
                "num_features": metrics.get("num_features"),
                "features": metrics.get("features"),
            }
    return results


@router.post("/trigger")
def trigger_training(
    model_type: str = Query(default="all"),
    location: str = Query(default=LOCATION_NAME),
    lat: float = Query(default=LOCATION_LAT),
    lon: float = Query(default=LOCATION_LON),
):
    types = list(VALID_MODEL_TYPES) if model_type == "all" else [model_type]
    jobs = []
    for mt in types:
        try:
            result = _create_training_job(mt, location, lat, lon)
            jobs.append({"model_type": mt, "job_name": result["job_name"], "status": "created"})
        except Exception as e:
            logger.error("Failed to create job for %s: %s", mt, e)
            jobs.append({"model_type": mt, "job_name": None, "status": f"failed: {e}"})
    return {"status": "ok", "message": f"Training job(s) created for {location}",
            "jobs": jobs, "commands": [j.get("command", "") for j in jobs if j.get("job_name")]}


@router.get("/jobs")
def list_training_jobs():
    _load_k8s()
    if _batch_v1 is None:
        return {"jobs": []}
    try:
        jobs = _batch_v1.list_namespaced_job(namespace=NAMESPACE, label_selector="app=crop-training")
        result = []
        for j in jobs.items:
            status = "unknown"
            if j.status.succeeded and j.status.succeeded > 0:
                status = "completed"
            elif j.status.active and j.status.active > 0:
                status = "running"
            elif j.status.failed and j.status.failed > 0:
                status = "failed"
            else:
                status = "pending"
            result.append({"job_name": j.metadata.name, "model_type": j.metadata.labels.get("model-type", ""),
                           "status": status, "created": j.metadata.creation_timestamp.isoformat() if j.metadata.creation_timestamp else None})
        return {"jobs": result}
    except Exception as e:
        logger.error("Failed to list jobs: %s", e)
        return {"jobs": []}


@router.get("/logs/{job_name}")
def get_job_logs(job_name: str, tail: int = Query(default=50, ge=5, le=500)):
    """Get logs from a training job's pod."""
    _load_k8s()
    if _batch_v1 is None:
        raise HTTPException(503, "Kubernetes API not available")
    try:
        from kubernetes import client
        core_v1 = client.CoreV1Api()
        pods = core_v1.list_namespaced_pod(
            namespace=NAMESPACE,
            label_selector=f"job-name={job_name}",
        )
        if not pods.items:
            return {"job_name": job_name, "logs": [], "status": "no_pod"}
        pod = pods.items[0]
        pod_phase = pod.status.phase
        logs = []
        if pod_phase in ("Running", "Succeeded", "Failed"):
            raw = core_v1.read_namespaced_pod_log(
                name=pod.metadata.name,
                namespace=NAMESPACE,
                tail_lines=tail,
            )
            logs = raw.strip().split("\n") if raw.strip() else []
        return {
            "job_name": job_name,
            "pod_name": pod.metadata.name,
            "pod_phase": pod_phase,
            "logs": logs,
        }
    except Exception as e:
        logger.error("Failed to get logs for %s: %s", job_name, e)
        return {"job_name": job_name, "logs": [str(e)], "status": "error"}
        return {"jobs": []}


@router.get("/params")
def get_training_params():
    from app.config import (
        WEATHER_FEATURES, SOIL_FEATURES, AIR_FEATURES, SATELLITE_FEATURES,
        TEMPORAL_FEATURES, INPUT_WINDOW, OUTPUT_WINDOW, HIDDEN_SIZE,
        NUM_LAYERS, DROPOUT, BATCH_SIZE, EPOCHS, LEARNING_RATE, PATIENCE,
        NUM_FEATURES, DEVICE,
    )
    return {
        "features": {
            "weather": WEATHER_FEATURES, "soil": SOIL_FEATURES,
            "air_quality": AIR_FEATURES, "satellite": SATELLITE_FEATURES,
            "temporal": TEMPORAL_FEATURES, "total": NUM_FEATURES,
        },
        "data_sources": [
            {"name": "Open-Meteo Weather+Soil", "url": "api.open-meteo.com", "interval": "daily"},
            {"name": "Open-Meteo Air Quality", "url": "air-quality-api.open-meteo.com", "interval": "daily"},
            {"name": "NASA MODIS (MOD13Q1)", "url": "modis.ornl.gov", "interval": "16-day → daily interp"},
        ],
        "model": {"input_window": INPUT_WINDOW, "output_window": OUTPUT_WINDOW,
                  "hidden_size": HIDDEN_SIZE, "num_layers": NUM_LAYERS, "dropout": DROPOUT},
        "training": {"batch_size": BATCH_SIZE, "epochs": EPOCHS,
                     "learning_rate": LEARNING_RATE, "patience": PATIENCE},
        "device": DEVICE,
        "crop_presets": CROP_PRESETS,
    }
