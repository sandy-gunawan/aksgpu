import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.config import CITY_LAT, CITY_LON, CITY_NAME
from app.routers.predict import predictor
from app.services.data_fetcher import WeatherDataFetcher
from app.services.predictor import VALID_MODEL_TYPES
from app.services.validator import ModelValidator

router = APIRouter(prefix="/api", tags=["validate"])

# Validation response cache (longer TTL since validation is expensive)
_val_cache: dict[str, tuple[float, dict]] = {}
_VAL_TTL = 900  # 15 min


def _val_get(key: str) -> dict | None:
    if key in _val_cache:
        ts, resp = _val_cache[key]
        if time.time() - ts < _VAL_TTL:
            return resp
        del _val_cache[key]
    return None


def _val_set(key: str, resp: dict) -> None:
    _val_cache[key] = (time.time(), resp)
    if len(_val_cache) > 50:
        oldest = min(_val_cache, key=lambda k: _val_cache[k][0])
        del _val_cache[oldest]


@router.get("/validate")
def get_validation(
    city: str = Query(default=CITY_NAME),
    lookback_days: int = Query(default=14, ge=2, le=30),
    lat: Optional[float] = Query(default=None, ge=-90, le=90),
    lon: Optional[float] = Query(default=None, ge=-180, le=180),
    model_type: str = Query(default="lstm", description="Model type: lstm, xgboost, or arima"),
):
    """Backtest a model for any coordinates worldwide."""
    if model_type not in VALID_MODEL_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid model_type. Choose from: {VALID_MODEL_TYPES}")

    use_lat = lat if lat is not None else CITY_LAT
    use_lon = lon if lon is not None else CITY_LON

    cache_key = f"val_{city}_{use_lat}_{use_lon}_{lookback_days}_{model_type}"
    cached = _val_get(cache_key)
    if cached is not None:
        return cached

    # Always attempt load — picks up newer models or different city
    predictor.load_model(model_type, city=city)
    if not predictor.is_model_loaded(model_type):
        raise HTTPException(
            status_code=404,
            detail=f"No trained {model_type} model available for {city}. Run training first.",
        )

    fetcher = WeatherDataFetcher(lat=use_lat, lon=use_lon)
    validator = ModelValidator(predictor=predictor, fetcher=fetcher)
    try:
        result = validator.validate(lookback_days=lookback_days, model_type=model_type)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    _val_set(cache_key, result)
    return result


@router.get("/validate/compare")
def compare_validations(
    city: str = Query(default=CITY_NAME),
    lookback_days: int = Query(default=14, ge=2, le=30),
    lat: Optional[float] = Query(default=None, ge=-90, le=90),
    lon: Optional[float] = Query(default=None, ge=-180, le=180),
):
    """Run validation for all loaded models and return combined results."""
    use_lat = lat if lat is not None else CITY_LAT
    use_lon = lon if lon is not None else CITY_LON

    cache_key = f"valcmp_{city}_{use_lat}_{use_lon}_{lookback_days}"
    cached = _val_get(cache_key)
    if cached is not None:
        return cached

    predictor.load_all_models(city=city)
    loaded = predictor.loaded_model_types()
    if not loaded:
        raise HTTPException(status_code=404, detail=f"No models available for {city}. Run training first.")

    fetcher = WeatherDataFetcher(lat=use_lat, lon=use_lon)
    validator = ModelValidator(predictor=predictor, fetcher=fetcher)
    results = validator.validate_all(lookback_days=lookback_days)

    result = {
        "city": city,
        "lat": use_lat,
        "lon": use_lon,
        "models": results,
    }
    _val_set(cache_key, result)
    return result
