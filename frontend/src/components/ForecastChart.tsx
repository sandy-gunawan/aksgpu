import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { type ForecastPoint, type ModelType, MODEL_LABELS, getForecast } from "../services/api";
import { type CityCoords } from "./CitySelector";
import { addLog } from "./ActivityLog";

interface Props {
  city: CityCoords;
  modelType: ModelType;
}

export default function ForecastChart({ city, modelType }: Props) {
  const [data, setData] = useState<ForecastPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    addLog(`Forecast: fetching 7-day ${MODEL_LABELS[modelType]} forecast for ${city.name}...`, "loading");
    const t0 = Date.now();
    getForecast(city.name, 7, city.lat, city.lon, modelType)
      .then((res) => {
        setData(res.forecast);
        addLog(`Forecast: ${MODEL_LABELS[modelType]} loaded ${res.forecast.length} hours in ${((Date.now() - t0) / 1000).toFixed(1)}s`, "success");
      })
      .catch((err) => {
        const msg = err.response?.data?.detail || err.message;
        setError(msg);
        addLog(`Forecast error: ${msg}`, "error");
      })
      .finally(() => setLoading(false));
  }, [city.name, city.lat, city.lon, modelType]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-400">
        Loading forecast…
      </div>
    );
  }
  if (error) {
    return (
      <div className="flex items-center justify-center h-64 text-red-400">
        {error}
      </div>
    );
  }

  const chartData = data.map((d) => ({
    ...d,
    label: new Date(d.time).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
    }),
  }));

  return (
    <div>
      <h3 className="text-lg font-semibold mb-3">
        7-Day Temperature & Humidity Forecast
        <span className="text-sm font-normal text-gray-400 ml-2">
          ({MODEL_LABELS[modelType]})
        </span>
      </h3>
      <ResponsiveContainer width="100%" height={350}>
        <LineChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis
            dataKey="label"
            tick={{ fontSize: 10, fill: "#9CA3AF" }}
            interval={23}
          />
          <YAxis
            yAxisId="temp"
            tick={{ fill: "#9CA3AF" }}
            label={{
              value: "°C",
              position: "insideTopLeft",
              fill: "#9CA3AF",
            }}
          />
          <YAxis
            yAxisId="hum"
            orientation="right"
            tick={{ fill: "#9CA3AF" }}
            label={{
              value: "%",
              position: "insideTopRight",
              fill: "#9CA3AF",
            }}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "#1F2937",
              border: "1px solid #374151",
              borderRadius: 8,
            }}
          />
          <Legend />
          <Line
            yAxisId="temp"
            type="monotone"
            dataKey="temperature"
            stroke="#3B82F6"
            dot={false}
            name="Temperature (°C)"
          />
          <Line
            yAxisId="hum"
            type="monotone"
            dataKey="humidity"
            stroke="#10B981"
            dot={false}
            name="Humidity (%)"
          />
        </LineChart>
      </ResponsiveContainer>

      <h3 className="text-lg font-semibold mt-6 mb-3">
        Wind Speed & Precipitation
      </h3>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis
            dataKey="label"
            tick={{ fontSize: 10, fill: "#9CA3AF" }}
            interval={23}
          />
          <YAxis tick={{ fill: "#9CA3AF" }} />
          <Tooltip
            contentStyle={{
              backgroundColor: "#1F2937",
              border: "1px solid #374151",
              borderRadius: 8,
            }}
          />
          <Legend />
          <Line
            type="monotone"
            dataKey="wind_speed"
            stroke="#F59E0B"
            dot={false}
            name="Wind (m/s)"
          />
          <Line
            type="monotone"
            dataKey="precipitation"
            stroke="#8B5CF6"
            dot={false}
            name="Precipitation (mm)"
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
