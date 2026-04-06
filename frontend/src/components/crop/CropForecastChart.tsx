import { useEffect, useState } from "react";
import { getCropPrediction, type CropPrediction, type ModelType } from "../../services/cropApi";
import { addLog } from "../ActivityLog";
import type { LocationCoords } from "./CropSelector";

interface Props {
  location: LocationCoords;
  modelType: ModelType;
}

const STRESS_COLORS: Record<string, string> = {
  healthy: "bg-green-500",
  moderate: "bg-yellow-500",
  stressed: "bg-orange-500",
  critical: "bg-red-500",
};

export default function CropForecastChart({ location, modelType }: Props) {
  const [data, setData] = useState<CropPrediction | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setLoading(true);
    setError("");
    addLog(`Fetching ${modelType.toUpperCase()} crop prediction for ${location.name}...`, "loading");
    getCropPrediction(location.lat, location.lon, location.name, 32, modelType)
      .then((d) => {
        setData(d);
        addLog(`Crop prediction loaded: ${d.days} days, ${d.stress_timeline.length} stress points`, "success");
      })
      .catch((e) => {
        setError(e.response?.data?.detail || e.message);
        addLog(`Crop prediction failed: ${e.response?.data?.detail || e.message}`, "error");
      })
      .finally(() => setLoading(false));
  }, [location.lat, location.lon, modelType]);

  if (loading) return <div className="text-center py-12 text-gray-400">Loading prediction...</div>;
  if (error) return <div className="text-center py-12 text-red-400">{error}</div>;
  if (!data) return <div className="text-center py-12 text-gray-400">No data</div>;

  const ndviIdx = data.features.indexOf("ndvi");
  const eviIdx = data.features.indexOf("evi");

  return (
    <div className="space-y-6">
      {/* Current Stress Indicator */}
      {data.current_stress && (
        <div className={`p-4 rounded-lg border ${
          data.current_stress.level === "healthy" ? "border-green-600 bg-green-900/20" :
          data.current_stress.level === "moderate" ? "border-yellow-600 bg-yellow-900/20" :
          data.current_stress.level === "stressed" ? "border-orange-600 bg-orange-900/20" :
          "border-red-600 bg-red-900/20"
        }`}>
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-semibold">Current Vegetation Status</h3>
              <p className="text-sm text-gray-400">{data.current_stress.description}</p>
            </div>
            <div className="text-right">
              <p className="text-2xl font-bold">{data.current_stress.ndvi.toFixed(3)}</p>
              <p className="text-xs text-gray-400">NDVI</p>
            </div>
          </div>
        </div>
      )}

      {/* NDVI Prediction Timeline */}
      <div>
        <h3 className="text-lg font-semibold mb-3">NDVI/EVI Forecast — Next {data.days} Days</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-gray-400 border-b border-gray-700">
                <th className="text-left py-2 px-2">Day</th>
                <th className="text-right py-2 px-2">NDVI</th>
                <th className="text-right py-2 px-2">EVI</th>
                <th className="text-left py-2 px-2">Status</th>
                <th className="py-2 px-2 w-48">Health Bar</th>
              </tr>
            </thead>
            <tbody>
              {data.stress_timeline.filter((_, i) => i % 4 === 0).map((s) => (
                <tr key={s.day} className="border-b border-gray-800">
                  <td className="py-1.5 px-2">{s.day}</td>
                  <td className="py-1.5 px-2 text-right font-mono">{s.ndvi.toFixed(3)}</td>
                  <td className="py-1.5 px-2 text-right font-mono">{s.evi?.toFixed(3) ?? "—"}</td>
                  <td className="py-1.5 px-2">
                    <span className={`inline-block w-2 h-2 rounded-full ${STRESS_COLORS[s.level]} mr-1`} />
                    {s.level}
                  </td>
                  <td className="py-1.5 px-2">
                    <div className="w-full bg-gray-800 rounded-full h-2">
                      <div
                        className={`h-2 rounded-full ${STRESS_COLORS[s.level]}`}
                        style={{ width: `${Math.max(5, s.ndvi * 100)}%` }}
                      />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Data Sources */}
      <div className="text-xs text-gray-500 flex gap-4">
        <span>Model: {data.model_type.toUpperCase()}</span>
        <span>Features: {data.features.length}</span>
        <span>Location: {data.location} ({data.lat}, {data.lon})</span>
      </div>
    </div>
  );
}
