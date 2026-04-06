import { useEffect, useState } from "react";
import {
  getCropDataPreview, getCropDataXlsxUrl,
  type DataPreview,
} from "../../services/cropApi";
import { addLog } from "../ActivityLog";
import type { LocationCoords } from "./CropSelector";

interface Props {
  location: LocationCoords;
}

const SOURCE_COLORS: Record<string, string> = {
  weather: "text-blue-400",
  soil: "text-amber-400",
  air_quality: "text-purple-400",
  satellite: "text-green-400",
  temporal: "text-gray-400",
};

const SOURCE_LABELS: Record<string, string> = {
  weather: "Open-Meteo Weather",
  soil: "Open-Meteo Soil",
  air_quality: "Open-Meteo Air Quality",
  satellite: "NASA MODIS Satellite",
  temporal: "Temporal Encoding",
};

function getColumnSource(col: string, groups: Record<string, string[]>): string {
  for (const [source, cols] of Object.entries(groups)) {
    if (cols.includes(col)) return source;
  }
  return "other";
}

export default function CropDataPreview({ location }: Props) {
  const [preview, setPreview] = useState<DataPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [showStats, setShowStats] = useState(false);
  const [page, setPage] = useState(0);
  const pageSize = 15;

  useEffect(() => {
    setLoading(true);
    getCropDataPreview(location.name, location.lat, location.lon, pageSize, page * pageSize)
      .then((d) => {
        setPreview(d);
        if (page === 0) addLog(`Data preview: ${d.total_records} records, ${d.columns.length} columns`, "info");
      })
      .catch(() => setPreview(null))
      .finally(() => setLoading(false));
  }, [location.name, location.lat, location.lon, page]);

  if (loading) return <div className="text-gray-400 text-sm py-2">Loading preview...</div>;
  if (!preview) return null;

  const totalPages = Math.ceil(preview.total_records / pageSize);
  const displayCols = preview.columns.filter((c) => c !== "date");

  return (
    <div className="mt-4 space-y-4">
      {/* Header with download button */}
      <div className="flex items-center justify-between">
        <h3 className="font-semibold">Data Preview ({preview.total_records} records)</h3>
        <div className="flex gap-2">
          <button onClick={() => setShowStats(!showStats)}
                  className="bg-gray-700 text-white px-3 py-1 rounded text-xs">
            {showStats ? "Hide Stats" : "Show Stats"}
          </button>
          <a href={getCropDataXlsxUrl(location.name, location.lat, location.lon)}
             className="bg-green-600 text-white px-3 py-1 rounded text-xs no-underline"
             download>
            Download XLSX
          </a>
        </div>
      </div>

      {/* Source Legend */}
      <div className="flex flex-wrap gap-3 text-xs">
        {Object.entries(preview.column_groups).map(([source, cols]) => (
          cols.length > 0 && (
            <span key={source} className={SOURCE_COLORS[source] || "text-gray-400"}>
              {SOURCE_LABELS[source] || source}: {cols.length} cols
            </span>
          )
        ))}
      </div>

      {/* Stats Panel */}
      {showStats && preview.stats && (
        <div className="bg-gray-800 rounded p-3 overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-gray-400 border-b border-gray-700">
                <th className="text-left py-1 px-2">Feature</th>
                <th className="text-left py-1 px-2">Source</th>
                <th className="text-right py-1 px-2">Min</th>
                <th className="text-right py-1 px-2">Max</th>
                <th className="text-right py-1 px-2">Mean</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(preview.stats).map(([col, s]) => {
                const source = getColumnSource(col, preview.column_groups);
                return (
                  <tr key={col} className="border-b border-gray-800">
                    <td className="py-1 px-2 font-mono">{col}</td>
                    <td className={`py-1 px-2 ${SOURCE_COLORS[source] || ""}`}>
                      {SOURCE_LABELS[source]?.split(" ").pop() || source}
                    </td>
                    <td className="py-1 px-2 text-right font-mono">{s.min}</td>
                    <td className="py-1 px-2 text-right font-mono">{s.max}</td>
                    <td className="py-1 px-2 text-right font-mono">{s.mean}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Data Table */}
      <div className="overflow-x-auto max-h-80 overflow-y-auto bg-gray-800 rounded">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-gray-900">
            <tr>
              <th className="text-left py-1.5 px-2 text-gray-400 whitespace-nowrap">date</th>
              {displayCols.map((col) => {
                const source = getColumnSource(col, preview.column_groups);
                return (
                  <th key={col} className={`text-right py-1.5 px-2 whitespace-nowrap ${SOURCE_COLORS[source] || "text-gray-400"}`}>
                    {col}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {preview.data.map((row, i) => (
              <tr key={i} className="border-b border-gray-800 hover:bg-gray-750">
                <td className="py-1 px-2 whitespace-nowrap text-gray-300">{String(row.date || "")}</td>
                {displayCols.map((col) => (
                  <td key={col} className="py-1 px-2 text-right font-mono text-gray-300">
                    {row[col] != null ? Number(row[col]).toFixed(3) : "—"}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="flex items-center justify-between text-xs text-gray-400">
        <span>
          Showing {page * pageSize + 1}–{Math.min((page + 1) * pageSize, preview.total_records)} of {preview.total_records}
        </span>
        <div className="flex gap-1">
          <button onClick={() => setPage(0)} disabled={page === 0}
                  className="px-2 py-1 bg-gray-700 rounded disabled:opacity-30">First</button>
          <button onClick={() => setPage(page - 1)} disabled={page === 0}
                  className="px-2 py-1 bg-gray-700 rounded disabled:opacity-30">Prev</button>
          <span className="px-2 py-1">Page {page + 1}/{totalPages}</span>
          <button onClick={() => setPage(page + 1)} disabled={page >= totalPages - 1}
                  className="px-2 py-1 bg-gray-700 rounded disabled:opacity-30">Next</button>
          <button onClick={() => setPage(totalPages - 1)} disabled={page >= totalPages - 1}
                  className="px-2 py-1 bg-gray-700 rounded disabled:opacity-30">Last</button>
        </div>
      </div>
    </div>
  );
}
