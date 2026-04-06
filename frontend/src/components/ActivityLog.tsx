import { useState, useEffect, useRef } from "react";

export interface LogEntry {
  id: number;
  time: string;
  message: string;
  type: "info" | "success" | "warning" | "error" | "loading";
}

let _nextId = 1;
let _listeners: Array<(entry: LogEntry) => void> = [];
let _history: LogEntry[] = [];

/** Call from anywhere to add a log entry */
export function addLog(
  message: string,
  type: LogEntry["type"] = "info"
): void {
  const entry: LogEntry = {
    id: _nextId++,
    time: new Date().toLocaleTimeString("en-US", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }),
    message,
    type,
  };
  _history = [..._history.slice(-99), entry]; // keep last 100
  _listeners.forEach((fn) => fn(entry));
}

const TYPE_STYLES: Record<LogEntry["type"], string> = {
  info: "text-blue-400",
  success: "text-green-400",
  warning: "text-yellow-400",
  error: "text-red-400",
  loading: "text-cyan-400",
};

const TYPE_ICONS: Record<LogEntry["type"], string> = {
  info: "i",
  success: "\u2713",
  warning: "!",
  error: "\u2717",
  loading: "\u25CB",
};

export default function ActivityLog() {
  const [logs, setLogs] = useState<LogEntry[]>(_history);
  const [open, setOpen] = useState(false);
  const [hasNew, setHasNew] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (entry: LogEntry) => {
      setLogs((prev) => [...prev.slice(-99), entry]);
      if (!open) setHasNew(true);
    };
    _listeners.push(handler);
    return () => {
      _listeners = _listeners.filter((fn) => fn !== handler);
    };
  }, [open]);

  useEffect(() => {
    if (open && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs, open]);

  const handleToggle = () => {
    setOpen((v) => !v);
    setHasNew(false);
  };

  return (
    <div className="fixed bottom-0 left-0 right-0 z-50">
      {/* Toggle bar */}
      <button
        onClick={handleToggle}
        className="w-full bg-gray-800 border-t border-gray-700 px-6 py-2 flex items-center justify-between text-xs hover:bg-gray-750 transition"
      >
        <div className="flex items-center gap-2">
          <span className="text-gray-400">Activity Log</span>
          {hasNew && (
            <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
          )}
          {logs.length > 0 && (
            <span className="text-gray-600">
              {logs[logs.length - 1]?.message}
            </span>
          )}
        </div>
        <span className="text-gray-500">{open ? "\u25BC" : "\u25B2"}</span>
      </button>

      {/* Log panel */}
      {open && (
        <div className="bg-gray-900 border-t border-gray-700 max-h-48 overflow-y-auto px-6 py-2 font-mono text-xs">
          {logs.length === 0 ? (
            <div className="text-gray-600 py-2">No activity yet</div>
          ) : (
            logs.map((entry) => (
              <div key={entry.id} className="flex gap-3 py-0.5">
                <span className="text-gray-600 shrink-0">{entry.time}</span>
                <span
                  className={`shrink-0 w-4 text-center ${TYPE_STYLES[entry.type]}`}
                >
                  {TYPE_ICONS[entry.type]}
                </span>
                <span
                  className={
                    entry.type === "error"
                      ? "text-red-300"
                      : entry.type === "success"
                      ? "text-green-300"
                      : "text-gray-300"
                  }
                >
                  {entry.message}
                </span>
              </div>
            ))
          )}
          <div ref={bottomRef} />
        </div>
      )}
    </div>
  );
}
