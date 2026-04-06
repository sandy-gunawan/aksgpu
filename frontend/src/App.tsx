import { useEffect, useState } from "react";
import { getHealth, type HealthResponse, type ModelType } from "./services/api";
import { getCropHealth } from "./services/cropApi";
import CitySelector, { type CityCoords } from "./components/CitySelector";
import ModelSelector from "./components/ModelSelector";
import ForecastChart from "./components/ForecastChart";
import TrainingStatus from "./components/TrainingStatus";
import ValidationChart from "./components/ValidationChart";
import ComparisonChart from "./components/ComparisonChart";
import ComparisonValidation from "./components/ComparisonValidation";
import WeatherReportView from "./components/WeatherReportView";
import ActivityLog from "./components/ActivityLog";
import CropSelector, { type LocationCoords } from "./components/crop/CropSelector";
import CropForecastChart from "./components/crop/CropForecastChart";
import CropValidationChart from "./components/crop/CropValidationChart";
import CropTrainingStatus from "./components/crop/CropTrainingStatus";

type AppMode = "weather" | "crop";
type Tab = "forecast" | "validation" | "compare" | "training" | "report";

const DEFAULT_CITY: CityCoords = { name: "New York", lat: 40.71, lon: -74.01 };
const DEFAULT_LOCATION: LocationCoords = { name: "palm-riau", lat: 1.50, lon: 102.10, crop: "palm" };

export default function App() {
  const [appMode, setAppMode] = useState<AppMode>("weather");
  const [city, setCity] = useState<CityCoords>(DEFAULT_CITY);
  const [cropLocation, setCropLocation] = useState<LocationCoords>(DEFAULT_LOCATION);
  const [tab, setTab] = useState<Tab>("forecast");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [cropHealthy, setCropHealthy] = useState(false);
  const [modelType, setModelType] = useState<ModelType>("lstm");
  const [compareMode, setCompareMode] = useState(false);

  useEffect(() => {
    getHealth().then(setHealth).catch(() => {});
    getCropHealth().then(() => setCropHealthy(true)).catch(() => setCropHealthy(false));
  }, []);

  const weatherTabs: { key: Tab; label: string }[] = [
    { key: "forecast", label: "Forecast" },
    { key: "validation", label: "Validation" },
    { key: "compare", label: "Compare Models" },
    { key: "report", label: "Report" },
    { key: "training", label: "Training" },
  ];

  const cropTabs: { key: Tab; label: string }[] = [
    { key: "forecast", label: "Prediction" },
    { key: "validation", label: "Validation" },
    { key: "training", label: "Training" },
  ];

  const tabs = appMode === "weather" ? weatherTabs : cropTabs;

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      {/* App Mode Switcher */}
      <div className="bg-gray-950 border-b border-gray-800 px-6 py-1">
        <div className="max-w-6xl mx-auto flex items-center gap-1">
          <button onClick={() => { setAppMode("weather"); setTab("forecast"); }}
                  className={`px-4 py-1.5 rounded-t text-sm font-medium transition ${
                    appMode === "weather" ? "bg-gray-900 text-blue-400 border-t-2 border-blue-500" : "text-gray-500 hover:text-gray-300"}`}>
            Weather Prediction
          </button>
          <button onClick={() => { setAppMode("crop"); setTab("forecast"); }}
                  className={`px-4 py-1.5 rounded-t text-sm font-medium transition ${
                    appMode === "crop" ? "bg-gray-900 text-green-400 border-t-2 border-green-500" : "text-gray-500 hover:text-gray-300"}`}>
            Crop Health
          </button>
        </div>
      </div>

      {/* Header */}
      <header className="bg-gray-900 border-b border-gray-800 px-6 py-4">
        <div className="max-w-6xl mx-auto flex items-center justify-between">
          <div>
            {appMode === "weather" ? (
              <>
                <h1 className="text-xl font-bold">GPU Weather Prediction</h1>
                <p className="text-xs text-gray-400 mt-0.5">
                  LSTM · XGBoost · ARIMA on AKS &middot;{" "}
                  {health?.gpu_available ? (
                    <span className="text-green-400">GPU: {health.cuda_device}</span>
                  ) : (
                    <span className="text-yellow-400">CPU mode</span>
                  )}
                  {health?.loaded_models && health.loaded_models.length > 0 && (
                    <span className="text-gray-500 ml-2">Models: {health.loaded_models.map((m) => m.toUpperCase()).join(", ")}</span>
                  )}
                </p>
              </>
            ) : (
              <>
                <h1 className="text-xl font-bold">GPU Crop Health Prediction</h1>
                <p className="text-xs text-gray-400 mt-0.5">
                  NDVI/EVI Vegetation Index · Multi-Source Data · LSTM · XGBoost · ARIMA &middot;{" "}
                  {health?.gpu_available ? (
                    <span className="text-green-400">GPU: {health.cuda_device}</span>
                  ) : (
                    <span className="text-yellow-400">CPU mode</span>
                  )}
                </p>
              </>
            )}
          </div>
          {appMode === "weather" ? (
            <CitySelector selected={city} onChange={setCity} />
          ) : (
            <CropSelector selected={cropLocation} onChange={setCropLocation} />
          )}
        </div>
      </header>

      {/* Tabs + Model Selector */}
      <nav className="max-w-6xl mx-auto px-6 pt-4 flex items-center justify-between">
        <div className="flex gap-1 bg-gray-900 rounded-lg p-1 w-fit">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-4 py-1.5 rounded text-sm transition ${
                tab === t.key
                  ? t.key === "compare"
                    ? "bg-purple-600 text-white"
                    : "bg-blue-600 text-white"
                  : "text-gray-400 hover:text-gray-200"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        {(tab === "forecast" || tab === "validation") && (
          <ModelSelector
            selected={modelType}
            onChange={setModelType}
          />
        )}
      </nav>

      {/* Content */}
      <main className="max-w-6xl mx-auto px-6 py-6">
        <div className="bg-gray-900 rounded-xl p-6">
          {appMode === "weather" ? (
            <>
              {tab === "forecast" && <ForecastChart city={city} modelType={modelType} />}
              {tab === "validation" && <ValidationChart city={city} modelType={modelType} />}
              {tab === "compare" && (
                <div className="space-y-8">
                  <ComparisonChart city={city} />
                  <hr className="border-gray-700" />
                  <ComparisonValidation city={city} />
                </div>
              )}
              {tab === "training" && <TrainingStatus city={city} />}
              {tab === "report" && <WeatherReportView city={city} modelType={modelType} />}
            </>
          ) : (
            <>
              {tab === "forecast" && <CropForecastChart location={cropLocation} modelType={modelType} />}
              {tab === "validation" && <CropValidationChart location={cropLocation} modelType={modelType} />}
              {tab === "training" && <CropTrainingStatus location={cropLocation} />}
            </>
          )}
        </div>
      </main>

      {/* Activity Log - slides up from bottom */}
      <ActivityLog />
      {/* Spacer so content isn't hidden behind the log bar */}
      <div className="h-10" />
    </div>
  );
}
