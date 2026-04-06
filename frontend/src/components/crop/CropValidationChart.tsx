import { useEffect, useState } from "react";
import { getCropValidation, type CropValidation, type ModelType } from "../../services/cropApi";
import { addLog } from "../ActivityLog";
import type { LocationCoords } from "./CropSelector";

interface Props {
  location: LocationCoords;
  modelType: ModelType;
}

export default function CropValidationChart({ location, modelType }: Props) {
  const [data, setData] = useState<CropValidation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const fetchValidation = () => {
    setLoading(true);
    setError("");
    addLog(`Running ${modelType.toUpperCase()} crop validation for ${location.name}...`, "loading");
    getCropValidation(location.lat, location.lon, location.name, 60, modelType)
      .then((d) => {
        setData(d);
        addLog(`Validation: MAE=${d.metrics.mae.toFixed(4)}, R²=${d.metrics.r2.toFixed(3)}`, "success");
      })
      .catch((e) => {
        setError(e.response?.data?.detail || e.message);
        addLog(`Validation failed: ${e.response?.data?.detail || e.message}`, "error");
      })
      .finally(() => setLoading(false));
  };

  useEffect(fetchValidation, [location.lat, location.lon, modelType]);

  if (loading) return <div className="text-center py-12 text-gray-400">Running validation...</div>;
  if (error) return <div className="text-center py-12 text-red-400">{error}</div>;
  if (!data) return null;

  const metricColor = (name: string, val: number): string => {
    if (name === "mae") return val < 0.05 ? "text-green-400" : val < 0.1 ? "text-yellow-400" : "text-red-400";
    if (name === "rmse") return val < 0.08 ? "text-green-400" : val < 0.15 ? "text-yellow-400" : "text-red-400";
    if (name === "r2") return val > 0.7 ? "text-green-400" : val > 0.4 ? "text-yellow-400" : "text-red-400";
    if (name === "bias") return Math.abs(val) < 0.03 ? "text-green-400" : Math.abs(val) < 0.08 ? "text-yellow-400" : "text-red-400";
    return "text-gray-400";
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold">Predicted vs Actual NDVI — {data.model_type.toUpperCase()}</h3>
        <button onClick={fetchValidation} className="bg-blue-600 text-white px-3 py-1.5 rounded text-sm">
          Run Validation
        </button>
      </div>

      {/* Metrics Cards */}
      <div className="grid grid-cols-4 gap-4">
        {Object.entries(data.metrics).map(([key, val]) => (
          <div key={key} className="bg-gray-800 rounded-lg p-4 text-center">
            <p className="text-xs text-gray-400 uppercase">{key}</p>
            <p className={`text-2xl font-bold mt-1 ${metricColor(key, val)}`}>
              {val.toFixed(4)}
            </p>
            <p className="text-xs text-gray-500 mt-1">
              {key === "mae" ? "Target: < 0.05" :
               key === "rmse" ? "Target: < 0.08" :
               key === "r2" ? "Target: > 0.7" : "Target: ~0"}
            </p>
          </div>
        ))}
      </div>

      {/* Predicted vs Actual Table */}
      <div className="overflow-x-auto max-h-64 overflow-y-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-gray-900">
            <tr className="text-gray-400 border-b border-gray-700">
              <th className="text-left py-2 px-2">Date</th>
              <th className="text-right py-2 px-2">Actual NDVI</th>
              <th className="text-right py-2 px-2">Predicted NDVI</th>
              <th className="text-right py-2 px-2">Error</th>
            </tr>
          </thead>
          <tbody>
            {data.dates.map((d, i) => {
              const err = Math.abs(data.predicted_ndvi[i] - data.actual_ndvi[i]);
              return (
                <tr key={d} className="border-b border-gray-800">
                  <td className="py-1 px-2">{d}</td>
                  <td className="py-1 px-2 text-right font-mono text-green-400">{data.actual_ndvi[i].toFixed(4)}</td>
                  <td className="py-1 px-2 text-right font-mono text-blue-400">{data.predicted_ndvi[i].toFixed(4)}</td>
                  <td className={`py-1 px-2 text-right font-mono ${err < 0.05 ? "text-green-400" : err < 0.1 ? "text-yellow-400" : "text-red-400"}`}>
                    {err.toFixed(4)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
