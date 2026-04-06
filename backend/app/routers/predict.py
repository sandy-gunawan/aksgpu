import time
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.config import CITY_LAT, CITY_LON, CITY_NAME
from app.services.data_fetcher import WeatherDataFetcher
from app.services.predictor import VALID_MODEL_TYPES, WeatherPredictor

router = APIRouter(prefix="/api", tags=["predict"])

# Shared predictor instance
predictor = WeatherPredictor()

# Response cache: key -> (timestamp, response_dict)
_resp_cache: dict[str, tuple[float, dict]] = {}
_RESP_TTL = 600  # 10 min


def _get_cached(key: str) -> dict | None:
    if key in _resp_cache:
        ts, resp = _resp_cache[key]
        if time.time() - ts < _RESP_TTL:
            return resp
        del _resp_cache[key]
    return None


def _set_cached(key: str, resp: dict) -> None:
    _resp_cache[key] = (time.time(), resp)
    if len(_resp_cache) > 100:
        oldest = min(_resp_cache, key=lambda k: _resp_cache[k][0])
        del _resp_cache[oldest]


@router.get("/predict")
def get_prediction(
    city: str = Query(default=CITY_NAME),
    days: int = Query(default=7, ge=1, le=14),
    lat: Optional[float] = Query(default=None, ge=-90, le=90),
    lon: Optional[float] = Query(default=None, ge=-180, le=180),
    model_type: str = Query(default="lstm", description="Model type: lstm, xgboost, or arima"),
):
    """Return an hourly weather forecast for any coordinates worldwide."""
    if model_type not in VALID_MODEL_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid model_type. Choose from: {VALID_MODEL_TYPES}")

    use_lat = lat if lat is not None else CITY_LAT
    use_lon = lon if lon is not None else CITY_LON

    # Check response cache first
    cache_key = f"predict_{city}_{use_lat}_{use_lon}_{days}_{model_type}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    # Always attempt load — picks up newer models or different city
    predictor.load_model(model_type, city=city)
    if not predictor.is_model_loaded(model_type):
        raise HTTPException(
            status_code=404,
            detail=f"No trained {model_type} model available for {city}. Run training first.",
        )

    try:
        forecast = predictor.predict(
            days=days, lat=use_lat, lon=use_lon, model_type=model_type
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    result = {
        "city": city,
        "lat": use_lat,
        "lon": use_lon,
        "model_type": model_type,
        "generated_at": datetime.utcnow().isoformat(),
        "forecast": forecast,
    }
    _set_cached(cache_key, result)
    return result


@router.get("/compare")
def compare_forecasts(
    city: str = Query(default=CITY_NAME),
    days: int = Query(default=7, ge=1, le=14),
    lat: Optional[float] = Query(default=None, ge=-90, le=90),
    lon: Optional[float] = Query(default=None, ge=-180, le=180),
):
    """Run all loaded models and return forecasts for comparison."""
    use_lat = lat if lat is not None else CITY_LAT
    use_lon = lon if lon is not None else CITY_LON

    cache_key = f"compare_{city}_{use_lat}_{use_lon}_{days}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    predictor.load_all_models(city=city)
    loaded = predictor.loaded_model_types()
    if not loaded:
        raise HTTPException(status_code=404, detail=f"No models available for {city}. Run training first.")

    results = {}
    for mt in loaded:
        try:
            forecast = predictor.predict(days=days, lat=use_lat, lon=use_lon, model_type=mt)
            results[mt] = {"forecast": forecast}
        except Exception as exc:
            results[mt] = {"error": str(exc)}

    result = {
        "city": city,
        "lat": use_lat,
        "lon": use_lon,
        "generated_at": datetime.utcnow().isoformat(),
        "models": results,
    }
    _set_cached(cache_key, result)
    return result


@router.get("/report")
def get_weather_report(
    city: str = Query(default=CITY_NAME),
    days: int = Query(default=7, ge=1, le=14),
    lat: Optional[float] = Query(default=None, ge=-90, le=90),
    lon: Optional[float] = Query(default=None, ge=-180, le=180),
    model_type: str = Query(default="lstm"),
):
    """Generate a human-readable weather report from forecast data."""
    use_lat = lat if lat is not None else CITY_LAT
    use_lon = lon if lon is not None else CITY_LON

    predictor.load_model(model_type, city=city)
    if not predictor.is_model_loaded(model_type):
        raise HTTPException(404, f"No trained {model_type} model. Train one first.")

    try:
        forecast = predictor.predict(days=days, lat=use_lat, lon=use_lon, model_type=model_type)
    except Exception as exc:
        raise HTTPException(500, str(exc))

    report = _generate_report(city, forecast, days)
    return {
        "city": city,
        "lat": use_lat,
        "lon": use_lon,
        "model_type": model_type,
        "generated_at": datetime.utcnow().isoformat(),
        "report": report,
    }


def _generate_report(city: str, forecast: list[dict], days: int) -> dict:
    """Analyze forecast data and produce structured weather narrative."""
    if not forecast:
        return {"summary": "No forecast data available.", "sections": [], "recommendations": []}

    daily: dict = {}
    for pt in forecast:
        dt = datetime.fromisoformat(pt["time"])
        day_key = dt.strftime("%A, %b %d")
        if day_key not in daily:
            daily[day_key] = {"temps": [], "humidity": [], "wind": [], "precip": [], "pressure": [], "date": dt.date()}
        daily[day_key]["temps"].append(pt["temperature"])
        daily[day_key]["humidity"].append(pt["humidity"])
        daily[day_key]["wind"].append(pt["wind_speed"])
        daily[day_key]["precip"].append(pt["precipitation"])
        daily[day_key]["pressure"].append(pt["pressure"])

    day_summaries = []
    all_temps: list[float] = []
    total_precip = 0.0
    max_wind = 0.0
    rain_days: list[str] = []

    for day_name, d in daily.items():
        t_min = round(min(d["temps"]), 1)
        t_max = round(max(d["temps"]), 1)
        t_avg = round(sum(d["temps"]) / len(d["temps"]), 1)
        precip = round(sum(d["precip"]), 1)
        wind_mx = round(max(d["wind"]), 1)
        hum_avg = round(sum(d["humidity"]) / len(d["humidity"]), 1)

        all_temps.extend(d["temps"])
        total_precip += precip
        max_wind = max(max_wind, wind_mx)
        if precip > 1.0:
            rain_days.append(day_name)

        cond = "Clear"
        if precip > 10:
            cond = "Heavy rain"
        elif precip > 2:
            cond = "Rain"
        elif precip > 0.5:
            cond = "Light rain"
        elif hum_avg > 85:
            cond = "Overcast"
        elif hum_avg > 70:
            cond = "Partly cloudy"

        day_summaries.append({
            "day": day_name, "high": t_max, "low": t_min, "avg": t_avg,
            "precipitation": precip, "max_wind": wind_mx, "humidity": hum_avg, "condition": cond,
        })

    temps_by_day = [s["avg"] for s in day_summaries]
    temp_trend = "stable"
    if len(temps_by_day) >= 3:
        h = len(temps_by_day) // 2
        first = sum(temps_by_day[:h]) / max(h, 1)
        second = sum(temps_by_day[h:]) / max(len(temps_by_day) - h, 1)
        temp_trend = "rising" if second > first + 1 else ("falling" if second < first - 1 else "stable")

    hi = round(max(all_temps), 1) if all_temps else 0
    lo = round(min(all_temps), 1) if all_temps else 0

    sections = []

    overview = f"{city} {days}-Day Outlook: "
    overview += f"Temperatures {lo}°C to {hi}°C ({temp_trend} trend). "
    if total_precip > 20:
        overview += f"Significant rain expected ({round(total_precip, 1)}mm). "
    elif total_precip > 5:
        overview += f"Some rain ({round(total_precip, 1)}mm). "
    elif total_precip > 0:
        overview += f"Mostly dry ({round(total_precip, 1)}mm). "
    else:
        overview += "No rain expected. "
    if max_wind > 40:
        overview += f"Strong winds up to {round(max_wind, 1)} km/h. "
    sections.append({"title": "Overview", "text": overview.strip()})

    daily_text = ""
    for s in day_summaries:
        daily_text += f"\u2022 {s['day']}: {s['condition']}, {s['low']}°C\u2013{s['high']}°C"
        if s["precipitation"] > 0.5:
            daily_text += f", {s['precipitation']}mm rain"
        if s["max_wind"] > 30:
            daily_text += f", wind {s['max_wind']} km/h"
        daily_text += "\n"
    sections.append({"title": "Daily Breakdown", "text": daily_text.strip()})

    alerts = []
    if total_precip > 30:
        alerts.append("Heavy rainfall warning \u2014 possible flooding in low-lying areas.")
    if max_wind > 50:
        alerts.append(f"High wind advisory \u2014 gusts up to {round(max_wind, 1)} km/h.")
    if hi > 38:
        alerts.append(f"Extreme heat warning \u2014 may reach {hi}°C.")
    if lo < 5:
        alerts.append(f"Frost risk \u2014 overnight lows near {lo}°C.")
    if alerts:
        sections.append({"title": "Alerts", "text": "\n".join(alerts)})

    recs = []
    if total_precip > 10:
        recs.append("Carry umbrella/rain gear for outdoor activities.")
    if total_precip > 20:
        recs.append("Agricultural: delay fertilizer application until dry spell.")
        recs.append("Consider postponing outdoor construction work.")
    if temp_trend == "rising" and hi > 32:
        recs.append("Stay hydrated. Avoid strenuous outdoor work during midday.")
    if max_wind > 30:
        recs.append("Secure loose outdoor items.")
    if not recs:
        recs.append("Favorable conditions for outdoor activities.")

    return {
        "summary": overview.strip(),
        "sections": sections,
        "daily": day_summaries,
        "stats": {"high": hi, "low": lo, "total_precipitation": round(total_precip, 1),
                  "max_wind": round(max_wind, 1), "rain_days": len(rain_days), "temp_trend": temp_trend},
        "recommendations": recs,
    }
