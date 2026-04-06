import { useEffect, useState, useRef, useCallback } from "react";
import {
  type TrainingStatus as TStatus,
  type TrainingJob,
  type ModelType,
  MODEL_TYPES,
  MODEL_LABELS,
  getAllTrainingStatus,
  getTrainingJobs,
  getTrainingParams,
  getJobLogs,
  triggerTraining,
} from "../services/api";
import DataManager from "./DataManager";
import { type CityCoords } from "./CitySelector";

interface Props {
  city: CityCoords;
}

const JOB_STATUS_COLORS: Record<string, string> = {
  completed: "text-green-400",
  running: "text-yellow-400",
  pending: "text-blue-400",
  failed: "text-red-400",
};

const JOB_STATUS_DOTS: Record<string, string> = {
  completed: "bg-green-400",
  running: "bg-yellow-400 animate-pulse",
  pending: "bg-blue-400 animate-pulse",
  failed: "bg-red-400",
};

export default function TrainingStatus({ city }: Props) {
  const [statuses, setStatuses] = useState<Record<string, TStatus>>({});
  const [jobs, setJobs] = useState<TrainingJob[]>([]);
  const [triggering, setTriggering] = useState<Record<string, boolean>>({});
  const [triggerResults, setTriggerResults] = useState<
    Record<string, { message: string; commands: string[]; error?: boolean }>
  >({});
  const [loading, setLoading] = useState(true);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [params, setParams] = useState<any>(null);
  const [paramsOpen, setParamsOpen] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchStatus = () => {
    getAllTrainingStatus(city.name)
      .then(setStatuses)
      .catch(() => setStatuses({}));
  };

  const fetchJobs = () => {
    getTrainingJobs()
      .then((res) => setJobs(res.jobs))
      .catch(() => {});
  };

  const fetchAll = () => {
    fetchStatus();
    fetchJobs();
  };

  useEffect(() => {
    setLoading(true);
    Promise.all([
      getAllTrainingStatus(city.name).then(setStatuses).catch(() => {}),
      getTrainingJobs().then((r) => setJobs(r.jobs)).catch(() => {}),
      getTrainingParams().then(setParams).catch(() => {}),
    ]).finally(() => setLoading(false));
  }, [city.name]);

  // Auto-poll when there are active jobs
  useEffect(() => {
    const hasActive = jobs.some((j) => j.status === "running" || j.status === "pending");
    if (hasActive && !pollRef.current) {
      pollRef.current = setInterval(fetchAll, 10000);
    } else if (!hasActive && pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [jobs]);

  const handleRetrain = async (mt: ModelType) => {
    setTriggering((prev) => ({ ...prev, [mt]: true }));
    try {
      const res = await triggerTraining(mt, city.name, city.lat, city.lon);
      setTriggerResults((prev) => ({
        ...prev,
        [mt]: { message: res.message, commands: res.commands },
      }));
      // Start polling for job status
      setTimeout(fetchAll, 2000);
      if (!pollRef.current) {
        pollRef.current = setInterval(fetchAll, 10000);
      }
    } catch (err: any) {
      setTriggerResults((prev) => ({
        ...prev,
        [mt]: {
          message: err.response?.data?.detail || "Failed to trigger training",
          commands: [],
          error: true,
        },
      }));
    } finally {
      setTriggering((prev) => ({ ...prev, [mt]: false }));
    }
  };

  if (loading) {
    return <div className="text-gray-400 text-center py-8">Loading...</div>;
  }

  const activeJobs = jobs.filter((j) => j.status === "running" || j.status === "pending");

  return (
    <div className="max-w-3xl mx-auto">
      {/* Data Management */}
      <h3 className="text-lg font-semibold mb-4">Weather Data</h3>
      <DataManager city={city} />

      <hr className="border-gray-700 my-6" />

      {/* Training Parameters (collapsible) */}
      {params && (
        <>
          <button
            onClick={() => setParamsOpen((p) => !p)}
            className="flex items-center gap-2 text-lg font-semibold mb-4 hover:text-blue-400 transition-colors"
          >
            <span className="text-gray-500 text-sm">{paramsOpen ? "▼" : "▶"}</span>
            Training Parameters
          </button>
          {paramsOpen && <TrainingParamsPanel params={params} />}
          <hr className="border-gray-700 my-6" />
        </>
      )}

      {/* Active Jobs Banner */}
      {activeJobs.length > 0 && (
        <div className="bg-yellow-900/20 border border-yellow-800 rounded-lg p-4 mb-4">
          <div className="flex items-center gap-2 mb-2">
            <span className="w-2 h-2 rounded-full bg-yellow-400 animate-pulse" />
            <span className="text-sm font-semibold text-yellow-300">
              Training in Progress ({activeJobs.length} job{activeJobs.length > 1 ? "s" : ""})
            </span>
            <span className="text-xs text-gray-400 ml-auto">Auto-refreshing every 10s</span>
          </div>
          <div className="space-y-1">
            {activeJobs.map((j) => (
              <div key={j.name} className="flex items-center gap-2 text-xs">
                <span className={`w-2 h-2 rounded-full ${JOB_STATUS_DOTS[j.status]}`} />
                <span className="font-mono text-gray-300">{j.name}</span>
                <span className={JOB_STATUS_COLORS[j.status]}>{j.status}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Training Status */}
      <h3 className="text-lg font-semibold mb-4">Training Status</h3>

      <div className="space-y-4">
        {MODEL_TYPES.map((mt) => {
          const st = statuses[mt];
          const isReady = st?.status === "ready";
          const isTriggering = triggering[mt];
          const result = triggerResults[mt];

          return (
            <div key={mt} className="bg-gray-800 rounded-lg p-5">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <span
                    className={`w-3 h-3 rounded-full ${
                      isReady ? "bg-green-400" : "bg-red-400"
                    }`}
                  />
                  <span className="font-semibold text-sm">
                    {MODEL_LABELS[mt]}
                  </span>
                  <span className="text-xs text-gray-400">
                    {isReady ? "Trained & ready" : "Not trained"}
                  </span>
                </div>
                <button
                  onClick={() => handleRetrain(mt)}
                  disabled={isTriggering}
                  className={`text-xs px-3 py-1.5 rounded ${
                    isTriggering
                      ? "bg-gray-600 text-gray-400 cursor-wait"
                      : "bg-blue-600 hover:bg-blue-700 text-white"
                  }`}
                >
                  {isTriggering ? "Creating job..." : "Retrain"}
                </button>
              </div>

              {isReady && st && (
                <div className="grid grid-cols-3 gap-3 text-sm">
                  <div>
                    <div className="text-gray-400 text-xs">Last Trained</div>
                    <div className="font-mono text-xs">
                      {st.last_trained ?? "Never"}
                    </div>
                  </div>
                  <div>
                    <div className="text-gray-400 text-xs">Duration</div>
                    <div className="font-mono text-xs">
                      {st.duration_minutes ? `${st.duration_minutes} min` : "—"}
                    </div>
                  </div>
                  <div>
                    <div className="text-gray-400 text-xs">Final Loss</div>
                    <div className="font-mono text-xs">
                      {st.final_loss?.toFixed(6) ?? "—"}
                    </div>
                  </div>
                  <div>
                    <div className="text-gray-400 text-xs">Epochs</div>
                    <div className="font-mono text-xs">
                      {st.epochs_completed ?? "—"}
                    </div>
                  </div>
                  <div>
                    <div className="text-gray-400 text-xs">Device</div>
                    <div className="font-mono text-xs">{st.device ?? "—"}</div>
                  </div>
                  <div>
                    <div className="text-gray-400 text-xs">Model File</div>
                    <div className="font-mono text-xs truncate">
                      {st.model_file ?? "—"}
                    </div>
                  </div>
                </div>
              )}

              {/* Trigger result: status + command */}
              {result && (
                <div
                  className={`mt-3 rounded p-3 text-xs ${
                    result.error
                      ? "bg-red-900/30 border border-red-800"
                      : "bg-green-900/20 border border-green-800"
                  }`}
                >
                  <p className={result.error ? "text-red-300" : "text-green-300"}>
                    {result.message}
                  </p>
                  {result.commands.length > 0 && (
                    <div className="mt-2 space-y-1">
                      <div className="text-gray-400 text-xs">Equivalent command:</div>
                      {result.commands.map((cmd, i) => (
                        <code
                          key={i}
                          className="block font-mono text-xs text-blue-300 bg-gray-900 rounded px-2 py-1 break-all select-all"
                        >
                          {cmd}
                        </code>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Recent Jobs with Live Logs */}
      {jobs.length > 0 && (
        <>
          <hr className="border-gray-700 my-6" />
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-lg font-semibold">Recent Training Jobs</h3>
            <button
              onClick={fetchAll}
              className="text-xs text-gray-400 hover:text-white px-2 py-1 rounded border border-gray-700 hover:border-gray-500"
            >
              Refresh
            </button>
          </div>
          <div className="space-y-2">
            {jobs.map((j) => (
              <JobCard key={j.name} job={j} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/* ---------- Job Card with expandable logs ---------- */

function JobCard({ job }: { job: TrainingJob }) {
  const [expanded, setExpanded] = useState(
    job.status === "running" || job.status === "pending"
  );
  const [logs, setLogs] = useState<string>("");
  const [logPhase, setLogPhase] = useState<string>("");
  const logPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const logEndRef = useRef<HTMLDivElement>(null);

  const fetchLogs = useCallback(async () => {
    try {
      const res = await getJobLogs(job.name, 300);
      setLogs(res.logs);
      setLogPhase(res.phase);
    } catch {
      setLogs("(failed to fetch logs)");
    }
  }, [job.name]);

  // When expanded, fetch logs immediately and poll if active
  useEffect(() => {
    if (!expanded) {
      if (logPollRef.current) {
        clearInterval(logPollRef.current);
        logPollRef.current = null;
      }
      return;
    }

    fetchLogs();
    const isActive = job.status === "running" || job.status === "pending";
    if (isActive) {
      logPollRef.current = setInterval(fetchLogs, 5000);
    }

    return () => {
      if (logPollRef.current) {
        clearInterval(logPollRef.current);
        logPollRef.current = null;
      }
    };
  }, [expanded, job.status, fetchLogs]);

  // Auto-scroll to bottom when logs update
  useEffect(() => {
    if (expanded && logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs, expanded]);

  const isActive = job.status === "running" || job.status === "pending";

  return (
    <div className="bg-gray-800 rounded-lg overflow-hidden">
      {/* Header row */}
      <button
        onClick={() => setExpanded((p) => !p)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-gray-750 transition-colors"
      >
        <span className="text-gray-500 text-xs">{expanded ? "▼" : "▶"}</span>
        <span
          className={`w-2 h-2 rounded-full shrink-0 ${
            JOB_STATUS_DOTS[job.status] || "bg-gray-500"
          }`}
        />
        <span className="font-mono text-xs text-gray-300 truncate flex-1">
          {job.name}
        </span>
        <span className="text-xs text-gray-400 uppercase w-16">{job.model_type}</span>
        <span
          className={`text-xs w-20 ${
            JOB_STATUS_COLORS[job.status] || "text-gray-400"
          }`}
        >
          {job.status}
        </span>
        <span className="text-xs text-gray-500 font-mono w-36 text-right">
          {job.created ? new Date(job.created).toLocaleString() : ""}
        </span>
      </button>

      {/* Expanded log panel */}
      {expanded && (
        <div className="border-t border-gray-700">
          <div className="flex items-center justify-between px-4 py-1.5 bg-gray-900/50">
            <span className="text-xs text-gray-400">
              {isActive ? (
                <>
                  <span className="inline-block w-1.5 h-1.5 rounded-full bg-yellow-400 animate-pulse mr-1.5" />
                  Live logs (refreshing every 5s)
                </>
              ) : (
                `Logs (${logPhase})`
              )}
            </span>
            <button
              onClick={(e) => {
                e.stopPropagation();
                fetchLogs();
              }}
              className="text-xs text-gray-500 hover:text-gray-300"
            >
              ↻ Refresh
            </button>
          </div>
          <pre className="px-4 py-3 text-xs font-mono text-gray-300 bg-gray-950 overflow-x-auto max-h-80 overflow-y-auto whitespace-pre-wrap leading-relaxed">
            {logs || "(waiting for output...)"}
            <div ref={logEndRef} />
          </pre>
        </div>
      )}
    </div>
  );
}

/* ---------- Training Parameters Panel ---------- */

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function TrainingParamsPanel({ params }: { params: any }) {
  const { features, data, models, training } = params;

  return (
    <div className="space-y-4">
      {/* Input Features */}
      <div className="bg-gray-800 rounded-lg p-4">
        <h4 className="text-sm font-semibold mb-3 text-blue-400">Input Features ({features.total} total)</h4>
        <div className="grid grid-cols-1 gap-1 text-xs">
          {Object.entries(features.description as Record<string, string>).map(([key, desc]) => (
            <div key={key} className="flex gap-2">
              <span className="font-mono text-green-400 w-28 shrink-0">{key}</span>
              <span className="text-gray-400">{desc}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Data Pipeline */}
      <div className="bg-gray-800 rounded-lg p-4">
        <h4 className="text-sm font-semibold mb-3 text-blue-400">Data Pipeline</h4>
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div><span className="text-gray-400">Source:</span> <span className="text-gray-200">{data.source}</span></div>
          <div><span className="text-gray-400">Normalization:</span> <span className="text-gray-200">{data.normalization}</span></div>
          <div><span className="text-gray-400">Input Window:</span> <span className="font-mono text-yellow-400">{data.input_window_hours}h ({data.input_window_days} days)</span></div>
          <div><span className="text-gray-400">Output Window:</span> <span className="font-mono text-yellow-400">{data.output_window_hours}h ({data.output_window_days} days)</span></div>
          <div><span className="text-gray-400">Split:</span> <span className="text-gray-200">{data.train_test_split}</span></div>
          <div><span className="text-gray-400">Batch Size:</span> <span className="font-mono text-gray-200">{training.batch_size}</span></div>
        </div>
      </div>

      {/* Model Architectures */}
      {Object.entries(models as Record<string, Record<string, unknown>>).map(([key, model]) => (
        <div key={key} className="bg-gray-800 rounded-lg p-4">
          <h4 className="text-sm font-semibold mb-3 text-blue-400">{model.name as string}</h4>
          <div className="grid grid-cols-2 gap-2 text-xs">
            {Object.entries(model).filter(([k]) => k !== "name").map(([k, v]) => (
              <div key={k}>
                <span className="text-gray-400">{k.replace(/_/g, " ")}:</span>{" "}
                <span className="font-mono text-gray-200">{String(v)}</span>
              </div>
            ))}
          </div>
        </div>
      ))}

      {/* Training Config */}
      <div className="bg-gray-800 rounded-lg p-4">
        <h4 className="text-sm font-semibold mb-3 text-blue-400">Training Config</h4>
        <div className="grid grid-cols-3 gap-2 text-xs">
          <div><span className="text-gray-400">Max Epochs:</span> <span className="font-mono text-gray-200">{training.max_epochs}</span></div>
          <div><span className="text-gray-400">Early Stopping:</span> <span className="font-mono text-gray-200">patience={training.early_stopping_patience}</span></div>
          <div><span className="text-gray-400">Batch Size:</span> <span className="font-mono text-gray-200">{training.batch_size}</span></div>
        </div>
      </div>
    </div>
  );
}
