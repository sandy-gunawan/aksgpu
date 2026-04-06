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
  getCompareValidation,
} from "../services/api";
import { type CityCoords } from "./CitySelector";

interface Props {
  city: CityCoords;
}

const MODEL_COLORS: Record<string, string> = {
  lstm: "#3B82F6",
  xgboost: "#F59E0B",
  arima: "#10B981",
  actual: "#E5E7EB",
};

interface ChartRow {
  label: string;
  actual?: number;
  [key: string]: unknown;
}

export default function ComparisonValidation({ city }: Props) {
  const [chartData, setChartData] = useState<ChartRow[]>([]);
  const [metricsMap, setMetricsMap] = useState<Record<string, ValidationMetrics>>({});
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runComparison = () => {
    setLoading(true);
    setError(null);
    getCompareValidation(city.name, 14, city.lat, city.lon)
      .then((res) => {
        const models = Object.keys(res.models).filter(
          (m) => res.models[m].metrics && !res.models[m].error
        );
        setAvailableModels(models);

        if (models.length === 0) {
          setError("No models available for comparison");
          return;
        }

        // Collect metrics
        const mMap: Record<string, ValidationMetrics> = {};
        for (const m of models) {
          mMap[m] = res.models[m].metrics;
        }
        setMetricsMap(mMap);

        // Use actual from first available model
        const firstModel = models[0];
        const actual = res.models[firstModel].actual || [];

        // Build daily data
        const daily = new Map<
          string,
          { actual: number[]; [key: string]: number[] }
        >();

        for (let i = 0; i < actual.length; i++) {
          const label = new Date(actual[i].time).toLocaleDateString("en-US", {
            month: "short",
            day: "numeric",
          });
          if (!daily.has(label)) {
            const entry: Record<string, number[]> = { actual: [] };
            for (const m of models) entry[m] = [];
            daily.set(label, entry as any);
          }
          const entry = daily.get(label)!;
          (entry as any).actual.push(actual[i].temperature);
          for (const m of models) {
            const pred = res.models[m].predicted;
            if (pred && pred[i]) {
              (entry as any)[m].push(pred[i].temperature);
            }
          }
        }

        const rows: ChartRow[] = Array.from(daily.entries()).map(
          ([label, vals]) => {
            const row: ChartRow = {
              label,
              actual: +(
                (vals as any).actual.reduce((a: number, b: number) => a + b, 0) /
                (vals as any).actual.length
              ).toFixed(1),
            };
            for (const m of models) {
              const arr = (vals as any)[m] as number[];
              if (arr.length > 0) {
                row[m] = +(arr.reduce((a, b) => a + b, 0) / arr.length).toFixed(1);
              }
            }
            return row;
          }
        );
        setChartData(rows);
      })
      .catch((err) => setError(err.response?.data?.detail || err.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    runComparison();
  }, [city.name, city.lat, city.lon]);

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-lg font-semibold">
            Model Comparison — Validation (14 days)
          </h3>
          <p className="text-xs text-gray-400">
            All models vs actual observed temperature
          </p>
        </div>
        <button
          onClick={runComparison}
          disabled={loading}
          className="bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white text-sm px-4 py-1.5 rounded"
        >
          {loading ? "Running…" : "Run Comparison"}
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
              <Line
                type="monotone"
                dataKey="actual"
                stroke={MODEL_COLORS.actual}
                strokeWidth={2}
                dot={{ r: 3 }}
                name="Actual"
              />
              {availableModels.map((m) => (
                <Line
                  key={m}
                  type="monotone"
                  dataKey={m}
                  stroke={MODEL_COLORS[m] ?? "#9CA3AF"}
                  strokeWidth={2}
                  strokeDasharray="6 3"
                  dot={{ r: 3 }}
                  name={MODEL_LABELS[m as ModelType] ?? m}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>

          {/* Metrics comparison table */}
          {Object.keys(metricsMap).length > 0 && (
            <div className="mt-6">
              <h4 className="text-sm font-semibold text-gray-300 mb-2">
                Metrics Comparison
              </h4>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-gray-400 border-b border-gray-700">
                      <th className="text-left py-2 px-3">Model</th>
                      <th className="text-right py-2 px-3">MAE (°C)</th>
                      <th className="text-right py-2 px-3">RMSE (°C)</th>
                      <th className="text-right py-2 px-3">R²</th>
                      <th className="text-right py-2 px-3">Bias (°C)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {availableModels.map((m) => {
                      const met = metricsMap[m];
                      if (!met) return null;
                      // Find the best value for each metric to highlight
                      const allMae = availableModels.map((x) => metricsMap[x]?.mae).filter(Boolean) as number[];
                      const allRmse = availableModels.map((x) => metricsMap[x]?.rmse).filter(Boolean) as number[];
                      const allR2 = availableModels.map((x) => metricsMap[x]?.r2).filter(Boolean) as number[];
                      const bestMae = Math.min(...allMae);
                      const bestRmse = Math.min(...allRmse);
                      const bestR2 = Math.max(...allR2);

                      return (
                        <tr
                          key={m}
                          className="border-b border-gray-800 hover:bg-gray-800/50"
                        >
                          <td className="py-2 px-3 font-medium" style={{ color: MODEL_COLORS[m] }}>
                            {MODEL_LABELS[m as ModelType] ?? m}
                          </td>
                          <td className={`text-right py-2 px-3 font-mono ${met.mae === bestMae ? "text-green-400 font-bold" : ""}`}>
                            {met.mae.toFixed(2)}
                          </td>
                          <td className={`text-right py-2 px-3 font-mono ${met.rmse === bestRmse ? "text-green-400 font-bold" : ""}`}>
                            {met.rmse.toFixed(2)}
                          </td>
                          <td className={`text-right py-2 px-3 font-mono ${met.r2 === bestR2 ? "text-green-400 font-bold" : ""}`}>
                            {met.r2.toFixed(3)}
                          </td>
                          <td className="text-right py-2 px-3 font-mono">
                            {met.bias.toFixed(2)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
