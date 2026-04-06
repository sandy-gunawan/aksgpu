from typing import Optional
import logging
import os
import time

from fastapi import APIRouter, HTTPException, Query

from app.services.blob_storage import BlobStorageService
from app.services.predictor import VALID_MODEL_TYPES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/training", tags=["training"])

blob = BlobStorageService()

# --------------- K8s Job helpers ---------------

_k8s_loaded = False
_batch_v1 = None
NAMESPACE = "gpu-weather"
TRAINER_IMAGE = os.getenv("TRAINER_IMAGE", "weather-api:v1")


def _load_k8s():
    """Load K8s client using in-cluster config (runs inside a pod)."""
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
        logger.warning(f"K8s client not available: {exc}")
        _k8s_loaded = False


def _create_training_job(model_type: str, city: str, lat: float | None, lon: float | None) -> dict:
    """Create a K8s Job that runs training for the given model type."""
    from kubernetes import client

    _load_k8s()
    if _batch_v1 is None:
        raise HTTPException(status_code=503, detail="Kubernetes API not available from this pod")

    ts = time.strftime("%Y%m%d-%H%M%S")
    job_name = f"train-{model_type}-{ts}"

    cmd = ["python", "-m", "scripts.train", "--model-type", model_type, "--city", city]
    if lat is not None and lon is not None:
        cmd += ["--lat", str(lat), "--lon", str(lon)]

    job = client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=client.V1ObjectMeta(
            name=job_name,
            namespace=NAMESPACE,
            labels={"app": "weather-training", "model-type": model_type},
        ),
        spec=client.V1JobSpec(
            backoff_limit=2,
            active_deadline_seconds=7200,
            template=client.V1PodTemplateSpec(
                spec=client.V1PodSpec(
                    containers=[
                        client.V1Container(
                            name="trainer",
                            image=TRAINER_IMAGE,
                            image_pull_policy="Always",
                            command=cmd,
                            resources=client.V1ResourceRequirements(
                                requests={"cpu": "2", "memory": "8Gi", "nvidia.com/gpu": "1"},
                                limits={"cpu": "4", "memory": "16Gi", "nvidia.com/gpu": "1"},
                            ),
                            env_from=[
                                client.V1EnvFromSource(config_map_ref=client.V1ConfigMapEnvSource(name="weather-config")),
                                client.V1EnvFromSource(secret_ref=client.V1SecretEnvSource(name="weather-secrets")),
                            ],
                        )
                    ],
                    tolerations=[
                        client.V1Toleration(
                            key="sku",
                            operator="Equal",
                            value="gpu",
                            effect="NoSchedule",
                        )
                    ],
                    node_selector={"kubernetes.azure.com/accelerator": "nvidia"},
                    restart_policy="OnFailure",
                )
            ),
        ),
    )

    _batch_v1.create_namespaced_job(namespace=NAMESPACE, body=job)
    kubectl_equiv = f"kubectl create job {job_name} -n {NAMESPACE} -- {' '.join(cmd)}"
    return {"job_name": job_name, "command": kubectl_equiv}


@router.get("/status")
def get_training_status(
    model_type: str = Query(default="lstm", description="Model type: lstm, xgboost, or arima"),
    city: str = Query(default="", description="Filter by city name"),
):
    """Return info about the latest trained model of the given type."""
    if model_type not in VALID_MODEL_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid model_type. Choose from: {VALID_MODEL_TYPES}")

    try:
        metrics = blob.get_latest_metrics(model_type=model_type, city=city)
    except Exception:
        metrics = None

    if metrics is None:
        return {
            "status": "no_model",
            "model_type": model_type,
            "last_trained": None,
            "model_file": None,
            "duration_minutes": None,
            "final_loss": None,
            "epochs_completed": None,
        }

    model_name = blob.get_latest_model_name(model_type=model_type, city=city)
    return {
        "status": "ready",
        "model_type": model_type,
        "last_trained": metrics.get("timestamp"),
        "model_file": model_name,
        "city": metrics.get("city"),
        "lat": metrics.get("lat"),
        "lon": metrics.get("lon"),
        "duration_minutes": metrics.get("duration_minutes"),
        "final_loss": metrics.get("final_test_loss"),
        "epochs_completed": metrics.get("epochs_completed"),
        "device": metrics.get("device"),
    }


@router.get("/status/all")
def get_all_training_status(
    city: str = Query(default="", description="Filter by city name"),
):
    """Return training status for all model types."""
    results = {}
    for mt in VALID_MODEL_TYPES:
        try:
            metrics = blob.get_latest_metrics(model_type=mt, city=city)
        except Exception:
            metrics = None

        if metrics is None:
            results[mt] = {"status": "no_model", "model_type": mt}
        else:
            model_name = blob.get_latest_model_name(model_type=mt, city=city)
            results[mt] = {
                "status": "ready",
                "model_type": mt,
                "last_trained": metrics.get("timestamp"),
                "model_file": model_name,
                "duration_minutes": metrics.get("duration_minutes"),
                "final_loss": metrics.get("final_test_loss"),
                "epochs_completed": metrics.get("epochs_completed"),
                "device": metrics.get("device"),
            }
    return results


@router.get("/params")
def get_training_params():
    """Return all training parameters, features, and model architecture info."""
    from app.config import (
        ALL_FEATURES, FEATURE_COLUMNS, SEASONAL_FEATURES,
        INPUT_WINDOW, OUTPUT_WINDOW, HIDDEN_SIZE, NUM_LAYERS, DROPOUT,
        BATCH_SIZE, EPOCHS, LEARNING_RATE, PATIENCE, TRAIN_SPLIT, NUM_FEATURES,
        DEVICE,
    )
    return {
        "features": {
            "weather": FEATURE_COLUMNS,
            "seasonal": SEASONAL_FEATURES,
            "total": NUM_FEATURES,
            "description": {
                "temperature": "Surface temperature (2m) in Celsius",
                "humidity": "Relative humidity (2m) in %",
                "wind_speed": "Wind speed (10m) in m/s",
                "precipitation": "Hourly precipitation in mm",
                "pressure": "Surface pressure in hPa",
                "day_sin": "sin(day_of_year / 365 * 2pi) - seasonal cycle",
                "day_cos": "cos(day_of_year / 365 * 2pi) - seasonal cycle",
                "hour_sin": "sin(hour / 24 * 2pi) - daily cycle",
                "hour_cos": "cos(hour / 24 * 2pi) - daily cycle",
            },
        },
        "data": {
            "source": "Open-Meteo API (free, no key)",
            "input_window_hours": INPUT_WINDOW,
            "input_window_days": INPUT_WINDOW // 24,
            "output_window_hours": OUTPUT_WINDOW,
            "output_window_days": OUTPUT_WINDOW // 24,
            "normalization": "MinMaxScaler (0-1 range per feature)",
            "train_test_split": f"{int(TRAIN_SPLIT*100)}% train / {int((1-TRAIN_SPLIT)*100)}% test",
        },
        "models": {
            "lstm": {
                "name": "Long Short-Term Memory (LSTM)",
                "framework": "PyTorch",
                "architecture": f"{NUM_LAYERS}-layer LSTM -> Linear",
                "hidden_size": HIDDEN_SIZE,
                "num_layers": NUM_LAYERS,
                "dropout": DROPOUT,
                "output": f"{OUTPUT_WINDOW}h x {NUM_FEATURES} features",
                "optimizer": "Adam",
                "loss_function": "MSELoss (Mean Squared Error)",
                "learning_rate": LEARNING_RATE,
                "lr_scheduler": "ReduceLROnPlateau (factor=0.5, patience=5)",
                "early_stopping": f"patience={PATIENCE} epochs",
                "device": DEVICE,
            },
            "xgboost": {
                "name": "Extreme Gradient Boosting (XGBoost)",
                "framework": "XGBoost 2.0+ (GPU CUDA)",
                "method": "One model per feature, gradient-boosted trees",
                "n_estimators": 300,
                "max_depth": 8,
                "learning_rate": 0.05,
                "tree_method": "hist (GPU)" if DEVICE == "cuda" else "hist (CPU)",
                "device": DEVICE,
            },
            "arima": {
                "name": "AutoRegressive Integrated Moving Average (ARIMA)",
                "framework": "statsmodels (SARIMAX)",
                "method": "One SARIMAX model per feature (univariate)",
                "order": "(2, 1, 2) = (AR=2, differencing=1, MA=2)",
                "works_on": "Raw data (no scaling needed)",
                "device": "CPU (no GPU acceleration for ARIMA)",
            },
        },
        "training": {
            "batch_size": BATCH_SIZE,
            "max_epochs": EPOCHS,
            "early_stopping_patience": PATIENCE,
        },
    }


@router.get("/models")
def list_trained_models():
    """List all trained models in Blob Storage."""
    pt_models = blob.list_models(".pt")
    pkl_models = [m for m in blob.list_models(".pkl") if "_scaler.pkl" not in m]
    return {"models": pt_models + pkl_models}


@router.post("/trigger")
def trigger_training(
    city: str = Query(default="new-york"),
    lat: Optional[float] = Query(default=None, ge=-90, le=90),
    lon: Optional[float] = Query(default=None, ge=-180, le=180),
    model_type: str = Query(default="lstm", description="Model type: lstm, xgboost, arima, or all"),
):
    """Create a K8s Job to train the specified model(s) on GPU."""
    valid = set(VALID_MODEL_TYPES) | {"all"}
    if model_type not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid model_type. Choose from: {tuple(valid)}")

    types_to_train = list(VALID_MODEL_TYPES) if model_type == "all" else [model_type]
    jobs = []
    commands = []

    for mt in types_to_train:
        try:
            info = _create_training_job(mt, city, lat, lon)
            jobs.append({"model_type": mt, "job_name": info["job_name"], "status": "created"})
            commands.append(info["command"])
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Failed to create job for {mt}: {exc}")
            jobs.append({"model_type": mt, "job_name": None, "status": f"error: {exc}"})

    label = model_type.upper() if model_type != "all" else "LSTM + XGBoost + ARIMA"
    return {
        "status": "training_started",
        "model_type": model_type,
        "city": city,
        "jobs": jobs,
        "commands": commands,
        "message": f"Training job(s) created for {label} ({city}). GPU node will scale up if needed (~3-5 min).",
    }


@router.get("/jobs")
def list_training_jobs():
    """List recent training jobs and their status."""
    _load_k8s()
    if _batch_v1 is None:
        return {"jobs": [], "error": "Kubernetes API not available"}

    try:
        job_list = _batch_v1.list_namespaced_job(
            namespace=NAMESPACE,
            label_selector="app=weather-training",
        )
    except Exception as exc:
        return {"jobs": [], "error": str(exc)}

    results = []
    for job in job_list.items:
        status = "unknown"
        if job.status.succeeded and job.status.succeeded > 0:
            status = "completed"
        elif job.status.failed and job.status.failed > 0:
            status = "failed"
        elif job.status.active and job.status.active > 0:
            status = "running"
        else:
            status = "pending"

        results.append({
            "name": job.metadata.name,
            "model_type": job.metadata.labels.get("model-type", "unknown"),
            "status": status,
            "created": job.metadata.creation_timestamp.isoformat() if job.metadata.creation_timestamp else None,
            "started": job.status.start_time.isoformat() if job.status.start_time else None,
            "completed": job.status.completion_time.isoformat() if job.status.completion_time else None,
        })

    results.sort(key=lambda j: j["created"] or "", reverse=True)
    return {"jobs": results[:15]}


@router.get("/jobs/{job_name}/logs")
def get_job_logs(
    job_name: str,
    tail: int = Query(default=100, ge=1, le=5000),
):
    """Get logs from a training job's pod."""
    from kubernetes import client

    _load_k8s()
    if _batch_v1 is None:
        raise HTTPException(status_code=503, detail="Kubernetes API not available")

    core_v1 = client.CoreV1Api()

    # Find pods belonging to this job
    try:
        pods = core_v1.list_namespaced_pod(
            namespace=NAMESPACE,
            label_selector=f"job-name={job_name}",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to list pods: {exc}")

    if not pods.items:
        return {
            "job_name": job_name,
            "status": "waiting",
            "logs": "Waiting for pod to be scheduled...\n(GPU node may need ~3-5 min to scale up)",
            "pod_name": None,
            "phase": "Pending",
        }

    pod = pods.items[0]
    pod_name = pod.metadata.name
    phase = pod.status.phase  # Pending, Running, Succeeded, Failed

    if phase == "Pending":
        # Check if waiting for GPU node
        conditions = []
        if pod.status.container_statuses:
            for cs in pod.status.container_statuses:
                if cs.state.waiting:
                    conditions.append(f"{cs.state.waiting.reason}: {cs.state.waiting.message or ''}")
        if not conditions and pod.status.conditions:
            for c in pod.status.conditions:
                if c.status != "True":
                    conditions.append(f"{c.type}: {c.message or c.reason or ''}")
        msg = "Pod is pending...\n"
        if conditions:
            msg += "\n".join(conditions)
        else:
            msg += "Waiting for resources (GPU node may be scaling up ~3-5 min)"
        return {
            "job_name": job_name,
            "status": "pending",
            "logs": msg,
            "pod_name": pod_name,
            "phase": phase,
        }

    # Pod is Running, Succeeded, or Failed - get logs
    try:
        logs = core_v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=NAMESPACE,
            tail_lines=tail,
        )
    except Exception as exc:
        logs = f"Failed to read logs: {exc}"

    return {
        "job_name": job_name,
        "status": phase.lower(),
        "logs": logs or "(no output yet)",
        "pod_name": pod_name,
        "phase": phase,
    }
