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
  type ValidationMetrics,
  type ForecastPoint,
  type ModelType,
  MODEL_LABELS,
  getValidation,
} from "../services/api";
import MetricsDashboard from "./MetricsDashboard";
import { type CityCoords } from "./CitySelector";
import { addLog } from "./ActivityLog";

interface Props {
  city: CityCoords;
  modelType: ModelType;
}

interface MergedPoint {
  label: string;
  predicted: number;
  actual: number;
}

export default function ValidationChart({ city, modelType }: Props) {
  const [metrics, setMetrics] = useState<ValidationMetrics | null>(null);
  const [chartData, setChartData] = useState<MergedPoint[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runValidation = () => {
    setLoading(true);
    setError(null);
    addLog(`Validation: running ${MODEL_LABELS[modelType]} backtest for ${city.name} (14 days)...`, "loading");
    addLog(`Step 1/3: Fetching weather data (Blob cache or Open-Meteo API)...`, "info");
    const t0 = Date.now();
    getValidation(city.name, 14, city.lat, city.lon, modelType)
      .then((res) => {
        const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
        addLog(`Step 2/3: Running ${MODEL_LABELS[modelType]} rolling predictions (14 x 24h windows)...`, "info");
        setMetrics(res.metrics);
        addLog(`Step 3/3: Computing metrics (MAE=${res.metrics.mae}, R2=${res.metrics.r2})`, "info");
        addLog(`Validation complete in ${elapsed}s - ${res.predicted.length} data points`, "success");
        const merged: MergedPoint[] = res.actual.map(
          (a: ForecastPoint, i: number) => ({
            label: new Date(a.time).toLocaleDateString("en-US", {
              month: "short",
              day: "numeric",
            }),
            actual: a.temperature,
            predicted: res.predicted[i]?.temperature ?? 0,
          })
        );
        // Downsample to daily averages for readability
        const daily = new Map<string, { actual: number[]; predicted: number[] }>();
        for (const m of merged) {
          const entry = daily.get(m.label) || { actual: [], predicted: [] };
          entry.actual.push(m.actual);
          entry.predicted.push(m.predicted);
          daily.set(m.label, entry);
        }
        setChartData(
          Array.from(daily.entries()).map(([label, v]) => ({
            label,
            actual: +(v.actual.reduce((a, b) => a + b, 0) / v.actual.length).toFixed(1),
            predicted: +(v.predicted.reduce((a, b) => a + b, 0) / v.predicted.length).toFixed(1),
          }))
        );
      })
      .catch((err) => {
        const msg = err.response?.data?.detail || err.message;
        setError(msg);
        addLog(`Validation error: ${msg}`, "error");
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    runValidation();
  }, [city.name, city.lat, city.lon, modelType]);

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-lg font-semibold">
          Model Validation — Predicted vs Actual (14 days)
          <span className="text-sm font-normal text-gray-400 ml-2">
            ({MODEL_LABELS[modelType]})
          </span>
        </h3>
        <button
          onClick={runValidation}
          disabled={loading}
          className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-sm px-4 py-1.5 rounded"
        >
          {loading ? "Running…" : "Run Validation"}
        </button>
      </div>

      {error && (
        <div className="text-red-400 text-center py-8">{error}</div>
      )}

      {!error && (
        <>
          <ResponsiveContainer width="100%" height={350}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
              <XAxis
                dataKey="label"
                tick={{ fontSize: 11, fill: "#9CA3AF" }}
              />
              <YAxis
                tick={{ fill: "#9CA3AF" }}
                label={{
                  value: "°C",
                  position: "insideTopLeft",
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
                type="monotone"
                dataKey="actual"
                stroke="#10B981"
                strokeWidth={2}
                dot={{ r: 3 }}
                name="Actual"
              />
              <Line
                type="monotone"
                dataKey="predicted"
                stroke="#3B82F6"
                strokeWidth={2}
                strokeDasharray="6 3"
                dot={{ r: 3 }}
                name="Predicted"
              />
            </LineChart>
          </ResponsiveContainer>

          <div className="mt-4">
            <MetricsDashboard metrics={metrics} />
          </div>
        </>
      )}
    </div>
  );
}
