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
import {
  type ForecastPoint,
  type ModelType,
  MODEL_LABELS,
  getCompare,
} from "../services/api";
import { type CityCoords } from "./CitySelector";
import { addLog } from "./ActivityLog";

interface Props {
  city: CityCoords;
}

const MODEL_COLORS: Record<string, string> = {
  lstm: "#3B82F6",
  xgboost: "#F59E0B",
  arima: "#10B981",
};

interface ChartRow {
  label: string;
  lstm?: number;
  xgboost?: number;
  arima?: number;
}

export default function ComparisonChart({ city }: Props) {
  const [chartData, setChartData] = useState<ChartRow[]>([]);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    addLog(`Compare: fetching all models for ${city.name}...`, "loading");
    const t0 = Date.now();
    getCompare(city.name, 7, city.lat, city.lon)
      .then((res) => {
        const models = Object.keys(res.models).filter(
          (m) => res.models[m].forecast && !res.models[m].error
        );
        setAvailableModels(models);
        addLog(`Compare: loaded ${models.length} models (${models.map(m => m.toUpperCase()).join(", ")}) in ${((Date.now() - t0) / 1000).toFixed(1)}s`, "success");

        if (models.length === 0) {
          setError("No models available for comparison");
          return;
        }

        // Find the model with the most points to use as the time axis
        const maxLen = Math.max(
          ...models.map((m) => res.models[m].forecast?.length ?? 0)
        );
        const refModel = models.find(
          (m) => (res.models[m].forecast?.length ?? 0) === maxLen
        )!;
        const refForecast = res.models[refModel].forecast!;

        // Downsample to every 6 hours for readability
        const rows: ChartRow[] = [];
        for (let i = 0; i < refForecast.length; i += 6) {
          const row: ChartRow = {
            label: new Date(refForecast[i].time).toLocaleDateString("en-US", {
              month: "short",
              day: "numeric",
              hour: "2-digit",
            }),
          };
          for (const m of models) {
            const f = res.models[m].forecast;
            if (f && f[i]) {
              (row as unknown as Record<string, unknown>)[m] = f[i].temperature;
            }
          }
          rows.push(row);
        }
        setChartData(rows);
      })
      .catch((err) => setError(err.response?.data?.detail || err.message))
      .finally(() => setLoading(false));
  }, [city.name, city.lat, city.lon]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-400">
        Loading comparison…
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

  return (
    <div>
      <h3 className="text-lg font-semibold mb-1">
        7-Day Temperature Forecast — All Models
      </h3>
      <p className="text-xs text-gray-400 mb-3">
        Comparing {availableModels.map((m) => MODEL_LABELS[m as ModelType] ?? m).join(", ")}
      </p>
      <ResponsiveContainer width="100%" height={400}>
        <LineChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis
            dataKey="label"
            tick={{ fontSize: 10, fill: "#9CA3AF" }}
            interval={3}
          />
          <YAxis
            tick={{ fill: "#9CA3AF" }}
            label={{ value: "°C", position: "insideTopLeft", fill: "#9CA3AF" }}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "#1F2937",
              border: "1px solid #374151",
              borderRadius: 8,
            }}
          />
          <Legend />
          {availableModels.map((m) => (
            <Line
              key={m}
              type="monotone"
              dataKey={m}
              stroke={MODEL_COLORS[m] ?? "#9CA3AF"}
              strokeWidth={2}
              dot={false}
              name={MODEL_LABELS[m as ModelType] ?? m}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
