import { useEffect, useState, useRef } from "react";
import {
  type DataStatus,
  type DataDownloadResult,
  getDataStatus,
  downloadData,
  triggerTraining,
  type ModelType,
} from "../services/api";
import { type CityCoords } from "./CitySelector";
import { addLog } from "./ActivityLog";

interface Props {
  city: CityCoords;
}

export default function DataManager({ city }: Props) {
  const [status, setStatus] = useState<DataStatus | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [years, setYears] = useState(1);
  const [months, setMonths] = useState(0);
  const [result, setResult] = useState<DataDownloadResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [training, setTraining] = useState(false);
  const [trainMsg, setTrainMsg] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchStatus = () => {
    getDataStatus(city.name, city.lat, city.lon)
      .then(setStatus)
      .catch(() => setStatus(null));
  };

  useEffect(fetchStatus, [city.name, city.lat, city.lon]);
  useEffect(() => () => { if (timerRef.current) clearInterval(timerRef.current); }, []);

  const estSec = (years * 365 + months * 30) > 365 ? 60 : 20;

  const handleDownload = async () => {
    setDownloading(true);
    setElapsed(0);
    setError(null);
    setResult(null);
    const totalDays = years * 365 + months * 30;
    addLog(`[1/4] Connecting to Open-Meteo API for ${city.name} (${city.lat}, ${city.lon})...`, "loading");
    timerRef.current = setInterval(() => setElapsed((p) => p + 1), 1000);
    try {
      addLog(`[2/4] Fetching ${totalDays} days of hourly data (temp, humidity, wind, precip, pressure)...`, "info");
      const res = await downloadData(city.name, city.lat, city.lon, years, months);
      addLog(`[3/4] Saving ${res.records} records to Azure Blob Storage as Parquet...`, "info");
      addLog(`[4/4] Download complete! ${res.records} records (${res.size_kb} KB) in ${res.download_seconds}s`, "success");
      setResult(res);
      fetchStatus();
    } catch (err: any) {
      const msg = err.response?.data?.detail || err.message;
      setError(msg);
      addLog(`Download failed: ${msg}`, "error");
    } finally {
      setDownloading(false);
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    }
  };

  const handleTrainAll = async () => {
    setTraining(true);
    setTrainMsg(null);
    addLog("=== GPU Training Pipeline Started ===", "loading");
    try {
      const res = await triggerTraining("all" as ModelType, city.name, city.lat, city.lon);
      addLog(res.message, "success");
      if (res.commands?.length) {
        res.commands.forEach((cmd: string) => addLog(`> ${cmd}`, "info"));
      }
      setTrainMsg(res.message);
    } catch (err: any) {
      const msg = err.response?.data?.detail || err.message;
      setTrainMsg(`Error: ${msg}`);
      addLog(`Training trigger failed: ${msg}`, "error");
    } finally {
      setTraining(false);
    }
  };

  const progressPct = downloading ? Math.min(95, Math.round((elapsed / estSec) * 100)) : 0;

  return (
    <div className="bg-gray-800 rounded-lg p-5">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h4 className="font-semibold text-sm">
            Weather Data Cache
          </h4>
          <p className="text-xs text-gray-400 mt-0.5">
            Pre-download data from Open-Meteo to Blob Storage for instant page
            loads
          </p>
        </div>
        {status?.available && (
          <span className="text-xs bg-green-900 text-green-300 px-2 py-1 rounded">
            Data Available
          </span>
        )}
      </div>

      {/* Current data info */}
      {status?.available && (
        <div className="bg-gray-900 rounded p-3 mb-4 text-xs">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <span className="text-gray-400">City:</span>{" "}
              <span className="text-gray-200">{status.city}</span>
            </div>
            <div>
              <span className="text-gray-400">Downloaded:</span>{" "}
              <span className="text-gray-200">{status.downloaded_at}</span>
            </div>
            <div>
              <span className="text-gray-400">From:</span>{" "}
              <span className="text-green-400">{status.start_date}</span>
            </div>
            <div>
              <span className="text-gray-400">To:</span>{" "}
              <span className="text-green-400">{status.end_date}</span>
            </div>
            <div>
              <span className="text-gray-400">Records:</span>{" "}
              <span className="text-gray-200">
                {status.records?.toLocaleString()} hourly
              </span>
            </div>
            <div>
              <span className="text-gray-400">Size:</span>{" "}
              <span className="text-gray-200">
                {status.size_bytes
                  ? `${(status.size_bytes / 1024).toFixed(0)} KB`
                  : "-"}
              </span>
            </div>
          </div>
        </div>
      )}

      {!status?.available && (
        <div className="bg-gray-900 rounded p-3 mb-4 text-xs text-yellow-400">
          No cached data for {city.name}. Pages fetch live from Open-Meteo API
          (slower). Download data below for instant loading.
        </div>
      )}

      {/* Download controls */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-1 text-xs">
          <label className="text-gray-400">Years:</label>
          <select
            value={years}
            onChange={(e) => setYears(Number(e.target.value))}
            className="bg-gray-700 text-gray-200 rounded px-2 py-1 text-xs"
          >
            {[0, 1, 2, 3, 5].map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-1 text-xs">
          <label className="text-gray-400">Months:</label>
          <select
            value={months}
            onChange={(e) => setMonths(Number(e.target.value))}
            className="bg-gray-700 text-gray-200 rounded px-2 py-1 text-xs"
          >
            {[0, 1, 3, 6].map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
        <button
          onClick={handleDownload}
          disabled={downloading}
          className={`text-xs px-4 py-1.5 rounded ${
            downloading
              ? "bg-gray-600 text-gray-400 cursor-wait"
              : "bg-emerald-600 hover:bg-emerald-700 text-white"
          }`}
        >
          {downloading ? `Downloading... ${elapsed}s` : "Download Data"}
        </button>
      </div>

      {/* Progress bar during download */}
      {downloading && (
        <div className="mt-3">
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>Fetching from Open-Meteo API...</span>
            <span>{progressPct}% ({elapsed}s / ~{estSec}s est.)</span>
          </div>
          <div className="w-full bg-gray-700 rounded-full h-2">
            <div
              className="bg-emerald-500 h-2 rounded-full transition-all duration-1000"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </div>
      )}

      {/* Result */}
      {result && (
        <div className="mt-3">
          <div className="bg-green-900/30 border border-green-800 rounded p-3 text-xs text-green-300">
            <p className="font-semibold mb-1">Download Complete</p>
            <p>{result.records.toLocaleString()} records ({result.start_date} to {result.end_date}) in {result.download_seconds}s ({result.size_kb} KB)</p>
          </div>
        </div>
      )}

      {/* Train All - show when data is available */}
      {status?.available && (
        <div className="mt-3">
          <div className="bg-blue-900/20 border border-blue-800 rounded p-3 flex items-center justify-between">
            <div className="text-xs">
              <p className="text-blue-300 font-semibold">Ready to retrain with new data?</p>
              <p className="text-gray-400 mt-0.5">Trains LSTM + XGBoost + ARIMA on GPU ({status.records?.toLocaleString()} records)</p>
            </div>
            <button
              onClick={handleTrainAll}
              disabled={training}
              className={`text-xs px-4 py-2 rounded shrink-0 ml-3 ${
                training ? "bg-gray-600 text-gray-400 cursor-wait" : "bg-blue-600 hover:bg-blue-700 text-white"
              }`}
            >
              {training ? "Creating jobs..." : "Train All Models"}
            </button>
          </div>
        </div>
      )}
      {trainMsg && (
        <div className={`mt-3 rounded p-2 text-xs ${trainMsg.startsWith("Error") ? "bg-red-900/30 border border-red-800 text-red-300" : "bg-blue-900/30 border border-blue-800 text-blue-300"}`}>
          {trainMsg}
        </div>
      )}
      {error && (
        <div className="mt-3 bg-red-900/30 border border-red-800 rounded p-2 text-xs text-red-300">
          {error}
        </div>
      )}
    </div>
  );
}
