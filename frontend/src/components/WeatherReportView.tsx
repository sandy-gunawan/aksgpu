import { useEffect, useState } from "react";
import { getWeatherReport, type WeatherReport, type ModelType } from "../services/api";
import { addLog } from "./ActivityLog";
import type { CityCoords } from "./CitySelector";

interface Props {
  city: CityCoords;
  modelType: ModelType;
}

const CONDITION_ICONS: Record<string, string> = {
  "Clear": "\u2600",
  "Partly cloudy": "\u26C5",
  "Overcast": "\u2601",
  "Light rain": "\uD83C\uDF26",
  "Rain": "\uD83C\uDF27",
  "Heavy rain": "\u26C8",
};

export default function WeatherReportView({ city, modelType }: Props) {
  const [report, setReport] = useState<WeatherReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const fetchReport = () => {
    setLoading(true);
    setError("");
    addLog(`Generating weather report for ${city.name}...`, "loading");
    getWeatherReport(city.name, 7, city.lat, city.lon, modelType)
      .then((r) => {
        setReport(r);
        addLog(`Report generated: ${r.report.stats.temp_trend} trend, ${r.report.stats.total_precipitation}mm rain`, "success");
      })
      .catch((e) => {
        setError(e.response?.data?.detail || e.message);
        addLog(`Report failed: ${e.response?.data?.detail || e.message}`, "error");
      })
      .finally(() => setLoading(false));
  };

  useEffect(fetchReport, [city.lat, city.lon, modelType]);

  if (loading) return <div className="text-center py-12 text-gray-400">Generating report...</div>;
  if (error) return <div className="text-center py-12 text-red-400">{error}</div>;
  if (!report) return null;

  const r = report.report;
  const trendColor = r.stats.temp_trend === "rising" ? "text-red-400" : r.stats.temp_trend === "falling" ? "text-blue-400" : "text-gray-400";

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-bold">Weather Report — {report.city}</h2>
        <button onClick={fetchReport} className="bg-blue-600 text-white px-3 py-1.5 rounded text-sm">
          Refresh Report
        </button>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-5 gap-3">
        <div className="bg-gray-800 rounded-lg p-3 text-center">
          <p className="text-xs text-gray-400">High</p>
          <p className="text-2xl font-bold text-red-400">{r.stats.high}°C</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-3 text-center">
          <p className="text-xs text-gray-400">Low</p>
          <p className="text-2xl font-bold text-blue-400">{r.stats.low}°C</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-3 text-center">
          <p className="text-xs text-gray-400">Rain</p>
          <p className="text-2xl font-bold text-cyan-400">{r.stats.total_precipitation}mm</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-3 text-center">
          <p className="text-xs text-gray-400">Max Wind</p>
          <p className="text-2xl font-bold">{r.stats.max_wind} km/h</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-3 text-center">
          <p className="text-xs text-gray-400">Trend</p>
          <p className={`text-2xl font-bold ${trendColor}`}>{r.stats.temp_trend === "rising" ? "\u2191" : r.stats.temp_trend === "falling" ? "\u2193" : "\u2194"} {r.stats.temp_trend}</p>
        </div>
      </div>

      {/* Summary */}
      <div className="bg-gray-800 rounded-lg p-4">
        <p className="text-sm leading-relaxed">{r.summary}</p>
      </div>

      {/* Daily Forecast Cards */}
      <div>
        <h3 className="font-semibold mb-3">Daily Forecast</h3>
        <div className="grid grid-cols-7 gap-2">
          {r.daily.map((d) => (
            <div key={d.day} className={`bg-gray-800 rounded-lg p-3 text-center ${
              d.precipitation > 5 ? "border border-blue-700" : ""}`}>
              <p className="text-xs text-gray-400 truncate">{d.day.split(",")[0]}</p>
              <p className="text-2xl my-1">{CONDITION_ICONS[d.condition] || "\u2601"}</p>
              <p className="text-xs">{d.condition}</p>
              <p className="text-sm font-bold mt-1">
                <span className="text-red-400">{d.high}°</span>
                {" / "}
                <span className="text-blue-400">{d.low}°</span>
              </p>
              {d.precipitation > 0.5 && (
                <p className="text-xs text-cyan-400 mt-1">{d.precipitation}mm</p>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Alerts */}
      {r.sections.filter((s) => s.title === "Alerts").map((s) => (
        <div key={s.title} className="bg-yellow-900/20 border border-yellow-700 rounded-lg p-4">
          <h3 className="font-semibold text-yellow-400 mb-2">Alerts</h3>
          <pre className="text-sm whitespace-pre-wrap text-yellow-300">{s.text}</pre>
        </div>
      ))}

      {/* Recommendations */}
      <div className="bg-green-900/20 border border-green-700 rounded-lg p-4">
        <h3 className="font-semibold text-green-400 mb-2">Recommendations</h3>
        <ul className="text-sm space-y-1">
          {r.recommendations.map((rec, i) => (
            <li key={i} className="text-green-300">{"\u2022"} {rec}</li>
          ))}
        </ul>
      </div>

      {/* Meta */}
      <div className="text-xs text-gray-500 flex gap-4">
        <span>Model: {report.model_type.toUpperCase()}</span>
        <span>Generated: {new Date(report.generated_at).toLocaleString()}</span>
        <span>Location: ({report.city})</span>
      </div>
    </div>
  );
}
