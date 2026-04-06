import { useEffect, useState, useCallback, useRef } from "react";
import {
  getAllCropTrainingStatus, triggerCropTraining, getCropTrainingJobs,
  getCropTrainingParams, getCropDataStatus, downloadCropData,
  getCropTrainingLogs,
  type CropTrainingStatus as TrainingStatusType, type ModelType, MODEL_TYPES,
} from "../../services/cropApi";
import { addLog } from "../ActivityLog";
import type { LocationCoords } from "./CropSelector";
import CropDataPreview from "./CropDataPreview";

interface TrainingJob {
  job_name: string;
  model_type: string;
  status: string;
  created?: string;
}

interface Props {
  location: LocationCoords;
}

export default function CropTrainingStatus({ location }: Props) {
  const [statuses, setStatuses] = useState<Record<string, TrainingStatusType>>({});
  const [params, setParams] = useState<Record<string, unknown> | null>(null);
  const [showParams, setShowParams] = useState(false);
  const [dataStatus, setDataStatus] = useState<{ available: boolean; records?: number; start_date?: string; end_date?: string; downloaded_at?: string; data_sources?: string[] } | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [years, setYears] = useState(2);
  const [months, setMonths] = useState(0);
  const [polling, setPolling] = useState(false);
  const [activeJobs, setActiveJobs] = useState<TrainingJob[]>([]);
  const [jobLogs, setJobLogs] = useState<Record<string, string[]>>({});
  const [expandedLogs, setExpandedLogs] = useState<string | null>(null);
  const logEndRef = useRef<HTMLDivElement>(null);

  const fetchStatus = useCallback(() => {
    getAllCropTrainingStatus(location.name).then(setStatuses).catch(() => {});
    getCropDataStatus(location.name, location.lat, location.lon).then(setDataStatus).catch(() => {});
  }, [location.name, location.lat, location.lon]);

  const fetchJobs = useCallback(() => {
    getCropTrainingJobs().then((r) => {
      setActiveJobs(r.jobs);
      const hasActive = r.jobs.some((j) => j.status === "running" || j.status === "pending");
      if (!hasActive && polling) {
        setPolling(false);
        fetchStatus();
        addLog("All training jobs completed", "success");
      }
      // Fetch logs for running/pending jobs
      r.jobs.forEach((j) => {
        if (j.status === "running" || j.status === "completed") {
          getCropTrainingLogs(j.job_name, 30).then((lr) => {
            setJobLogs((prev) => ({ ...prev, [j.job_name]: lr.logs }));
          }).catch(() => {});
        }
      });
    }).catch(() => {});
  }, [polling, fetchStatus]);

  useEffect(() => {
    fetchStatus();
    fetchJobs();
    getCropTrainingParams().then(setParams).catch(() => {});
  }, [fetchStatus, fetchJobs]);

  // Poll for active jobs
  useEffect(() => {
    if (!polling) return;
    const id = setInterval(() => {
      fetchJobs();
      fetchStatus();
    }, 5000);
    return () => clearInterval(id);
  }, [polling, fetchJobs, fetchStatus]);

  // Auto-scroll logs
  useEffect(() => {
    if (expandedLogs && logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [jobLogs, expandedLogs]);

  const handleDownload = async () => {
    setDownloading(true);
    addLog(`Downloading crop data for ${location.name} (${years}y${months}m)...`, "loading");
    try {
      const r = await downloadCropData(location.name, location.lat, location.lon, years, months);
      addLog(`Downloaded ${(r as any).records} records from ${((r as any).data_sources_fetched || []).length} sources`, "success");
      fetchStatus();
    } catch (e: any) {
      addLog(`Download failed: ${e.message}`, "error");
    } finally {
      setDownloading(false);
    }
  };

  const handleTrain = async (mt: ModelType | "all") => {
    addLog(`Starting ${mt.toUpperCase()} training for ${location.name}...`, "loading");
    try {
      const r = await triggerCropTraining(mt, location.name, location.lat, location.lon);
      addLog(r.message, "success");
      setPolling(true);
    } catch (e: any) {
      addLog(`Training trigger failed: ${e.response?.data?.detail || e.message}`, "error");
    }
  };

  return (
    <div className="space-y-6">
      {/* Data Section */}
      <div>
        <h2 className="text-xl font-bold mb-4">Crop Data</h2>
        <div className="bg-gray-800 rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold">Multi-Source Data Cache</h3>
            {dataStatus?.available && (
              <span className="bg-green-600 text-white text-xs px-2 py-1 rounded">Data Available</span>
            )}
          </div>
          {dataStatus?.available ? (
            <div className="grid grid-cols-2 gap-4 text-sm mb-3">
              <div><span className="text-gray-400">Location:</span> <strong>{location.name}</strong></div>
              <div><span className="text-gray-400">Downloaded:</span> <strong>{dataStatus.downloaded_at}</strong></div>
              <div><span className="text-gray-400">From:</span> <span className="text-green-400">{dataStatus.start_date}</span></div>
              <div><span className="text-gray-400">To:</span> <span className="text-green-400">{dataStatus.end_date}</span></div>
              <div><span className="text-gray-400">Records:</span> <strong>{dataStatus.records?.toLocaleString()} daily</strong></div>
              <div><span className="text-gray-400">Sources:</span> {dataStatus.data_sources?.length || 0}</div>
            </div>
          ) : (
            <p className="text-sm text-yellow-400 mb-3">No cached data. Download from Open-Meteo + NASA MODIS.</p>
          )}

          <div className="flex items-center gap-3">
            <label className="text-sm">Years:</label>
            <select value={years} onChange={(e) => setYears(+e.target.value)} className="bg-gray-700 rounded px-2 py-1 text-sm">
              {[1, 2, 3, 4, 5].map(y => <option key={y} value={y}>{y}</option>)}
            </select>
            <label className="text-sm">Months:</label>
            <select value={months} onChange={(e) => setMonths(+e.target.value)} className="bg-gray-700 rounded px-2 py-1 text-sm">
              {[0,1,2,3,4,5,6,7,8,9,10,11].map(m => <option key={m} value={m}>{m}</option>)}
            </select>
            <button onClick={handleDownload} disabled={downloading}
                    className="bg-green-600 text-white px-4 py-1.5 rounded text-sm disabled:opacity-50">
              {downloading ? "Downloading..." : "Download Data"}
            </button>
          </div>

          {dataStatus?.available && (
            <div className="mt-3 p-3 border border-green-700 bg-green-900/20 rounded">
              <p className="text-sm text-green-400 font-semibold">Ready to retrain with new data?</p>
              <p className="text-xs text-gray-400">Trains LSTM + XGBoost + ARIMA on GPU ({dataStatus.records?.toLocaleString()} records)</p>
              <button onClick={() => handleTrain("all")}
                      className="mt-2 bg-blue-600 text-white px-4 py-1.5 rounded text-sm font-semibold">
                Train All Models
              </button>
            </div>
          )}

          {/* Data Preview */}
          {dataStatus?.available && <CropDataPreview location={location} />}
        </div>
      </div>

      {/* Training Params */}
      <div>
        <button onClick={() => setShowParams(!showParams)} className="flex items-center gap-2 text-lg font-bold">
          <span>{showParams ? "▼" : "▶"}</span> Training Parameters
        </button>
        {showParams && params && (
          <div className="mt-2 bg-gray-800 rounded-lg p-4 text-sm">
            <pre className="text-xs text-gray-300 whitespace-pre-wrap">{JSON.stringify(params, null, 2)}</pre>
          </div>
        )}
      </div>

      {/* Active Training Jobs */}
      {activeJobs.length > 0 && (
        <div>
          <h2 className="text-xl font-bold mb-4">Active Training Jobs</h2>
          {activeJobs.map((j) => {
            const isRunning = j.status === "running";
            const isPending = j.status === "pending";
            const isCompleted = j.status === "completed";
            const isFailed = j.status === "failed";
            const logs = jobLogs[j.job_name] || [];
            const isExpanded = expandedLogs === j.job_name;
            return (
              <div key={j.job_name} className={`bg-gray-800 rounded-lg p-4 mb-3 border-l-4 ${
                isRunning ? "border-blue-500" : isPending ? "border-yellow-500" :
                isCompleted ? "border-green-500" : "border-red-500"}`}>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    {isRunning && <span className="w-3 h-3 rounded-full bg-blue-500 animate-pulse" />}
                    {isPending && <span className="w-3 h-3 rounded-full bg-yellow-500 animate-pulse" />}
                    {isCompleted && <span className="w-3 h-3 rounded-full bg-green-500" />}
                    {isFailed && <span className="w-3 h-3 rounded-full bg-red-500" />}
                    <div>
                      <span className="font-semibold">{j.model_type.toUpperCase()}</span>
                      <span className="text-xs text-gray-400 ml-2">{j.job_name}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`text-xs px-2 py-0.5 rounded ${
                      isRunning ? "bg-blue-600" : isPending ? "bg-yellow-600" :
                      isCompleted ? "bg-green-600" : "bg-red-600"} text-white`}>
                      {j.status}
                    </span>
                    {logs.length > 0 && (
                      <button onClick={() => setExpandedLogs(isExpanded ? null : j.job_name)}
                              className="text-xs bg-gray-700 px-2 py-0.5 rounded">
                        {isExpanded ? "Hide Logs" : `Logs (${logs.length})`}
                      </button>
                    )}
                  </div>
                </div>
                {/* Log Viewer */}
                {isExpanded && logs.length > 0 && (
                  <div className="mt-3 bg-gray-900 rounded p-3 max-h-60 overflow-y-auto font-mono text-xs">
                    {logs.map((line, i) => {
                      const isEpoch = line.includes("Epoch") || line.includes("epoch");
                      const isError = line.toLowerCase().includes("error") || line.toLowerCase().includes("fail");
                      const isComplete = line.includes("complete") || line.includes("Saved");
                      const isInfo = line.includes("INFO");
                      return (
                        <div key={i} className={`py-0.5 ${
                          isError ? "text-red-400" : isComplete ? "text-green-400" :
                          isEpoch ? "text-cyan-400" : isInfo ? "text-gray-300" : "text-gray-400"
                        }`}>
                          {line}
                        </div>
                      );
                    })}
                    <div ref={logEndRef} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Training Status */}
      <div>
        <h2 className="text-xl font-bold mb-4">Training Status</h2>
        {MODEL_TYPES.map((mt) => {
          const s = statuses[mt];
          if (!s) return null;
          const ready = s.status === "ready";
          return (
            <div key={mt} className="bg-gray-800 rounded-lg p-4 mb-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className={`w-3 h-3 rounded-full ${ready ? "bg-green-500" : "bg-red-500"}`} />
                  <span className="font-bold">{mt.toUpperCase()}</span>
                  <span className="text-sm text-gray-400">{ready ? "Trained & ready" : "Not trained"}</span>
                </div>
                <button onClick={() => handleTrain(mt)} className="bg-blue-600 text-white px-3 py-1 rounded text-sm">
                  Retrain
                </button>
              </div>
              {ready && (
                <div className="grid grid-cols-3 gap-4 mt-3 text-sm">
                  <div><span className="text-gray-400">Last Trained</span><br /><strong>{s.last_trained}</strong></div>
                  <div><span className="text-gray-400">Duration</span><br /><strong>{s.duration_minutes} min</strong></div>
                  <div><span className="text-gray-400">Final Loss</span><br /><strong>{s.final_loss?.toFixed(6)}</strong></div>
                  <div><span className="text-gray-400">Epochs</span><br /><strong>{s.epochs_completed}</strong></div>
                  <div><span className="text-gray-400">Device</span><br /><strong>{s.device}</strong></div>
                  <div><span className="text-gray-400">Model File</span><br /><strong className="text-xs break-all">{s.model_file}</strong></div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
