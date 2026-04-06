import axios from "axios";

const api = axios.create({ baseURL: "" });

export type ModelType = "lstm" | "xgboost" | "arima";
export const MODEL_TYPES: ModelType[] = ["lstm", "xgboost", "arima"];

export interface CropPreset {
  name: string;
  lat: number;
  lon: number;
  crop: string;
}

export interface StressEntry {
  day: number;
  ndvi: number;
  evi: number | null;
  level: string;
  description: string;
}

export interface CropPrediction {
  predictions: number[][];
  features: string[];
  days: number;
  model_type: string;
  stress_timeline: StressEntry[];
  current_stress: { level: string; description: string; ndvi: number } | null;
  location: string;
  lat: number;
  lon: number;
}

export interface CropValidation {
  model_type: string;
  location: string;
  dates: string[];
  predicted_ndvi: number[];
  actual_ndvi: number[];
  metrics: { mae: number; rmse: number; r2: number; bias: number };
}

export interface CropTrainingStatus {
  status: "no_model" | "ready";
  model_type: string;
  last_trained?: string;
  model_file?: string;
  duration_minutes?: number;
  final_loss?: number;
  epochs_completed?: number;
  device?: string;
  num_features?: number;
  features?: string[];
}

export interface CropDataStatus {
  available: boolean;
  location: string;
  lat: number;
  lon: number;
  start_date?: string;
  end_date?: string;
  records?: number;
  features?: number;
  downloaded_at?: string;
  data_sources?: string[];
  message?: string;
}

export async function getCropHealth(): Promise<Record<string, unknown>> {
  const { data } = await api.get("/api/crop/health");
  return data;
}

export async function getCropPrediction(
  lat: number, lon: number, location: string, days = 32, modelType: ModelType = "lstm"
): Promise<CropPrediction> {
  const { data } = await api.get("/api/crop/predict", {
    params: { lat, lon, location, days, model_type: modelType },
  });
  return data;
}

export async function getCropValidation(
  lat: number, lon: number, location: string, lookbackDays = 60, modelType: ModelType = "lstm"
): Promise<CropValidation> {
  const { data } = await api.get("/api/crop/validate", {
    params: { lat, lon, location, lookback_days: lookbackDays, model_type: modelType },
  });
  return data;
}

export async function getCropCompareValidation(
  lat: number, lon: number, location: string, lookbackDays = 60
): Promise<Record<string, CropValidation>> {
  const { data } = await api.get("/api/crop/validate/compare", {
    params: { lat, lon, location, lookback_days: lookbackDays },
  });
  return data;
}

export async function getAllCropTrainingStatus(
  location?: string
): Promise<Record<string, CropTrainingStatus>> {
  const params: Record<string, string> = {};
  if (location) params.location = location;
  const { data } = await api.get("/api/crop/training/status/all", { params });
  return data;
}

export async function triggerCropTraining(
  modelType: ModelType | "all" = "all", location?: string, lat?: number, lon?: number
): Promise<{ status: string; message: string; jobs: Array<{ model_type: string; job_name: string | null; status: string }> }> {
  const params: Record<string, string | number> = { model_type: modelType };
  if (location) params.location = location;
  if (lat !== undefined) params.lat = lat;
  if (lon !== undefined) params.lon = lon;
  const { data } = await api.post("/api/crop/training/trigger", null, { params });
  return data;
}

export async function getCropTrainingJobs(): Promise<{ jobs: Array<{ job_name: string; model_type: string; status: string }> }> {
  const { data } = await api.get("/api/crop/training/jobs");
  return data;
}

export async function getCropTrainingLogs(jobName: string, tail = 50): Promise<{
  job_name: string; pod_name?: string; pod_phase?: string; logs: string[]; status?: string;
}> {
  const { data } = await api.get(`/api/crop/training/logs/${jobName}`, { params: { tail } });
  return data;
}

export async function getCropTrainingParams(): Promise<Record<string, unknown>> {
  const { data } = await api.get("/api/crop/training/params");
  return data;
}

export async function getCropDataStatus(
  location: string, lat: number, lon: number
): Promise<CropDataStatus> {
  const { data } = await api.get("/api/crop/data/status", { params: { location, lat, lon } });
  return data;
}

export async function downloadCropData(
  location: string, lat: number, lon: number, years = 2, months = 0
): Promise<Record<string, unknown>> {
  const { data } = await api.post("/api/crop/data/download", null, {
    params: { location, lat, lon, years, months },
    timeout: 900000,
  });
  return data;
}

export async function getCropPresets(): Promise<Record<string, CropPreset>> {
  const { data } = await api.get("/api/crop/data/presets");
  return data;
}

export interface DataPreview {
  location: string;
  total_records: number;
  showing: { offset: number; count: number };
  columns: string[];
  column_groups: Record<string, string[]>;
  data: Record<string, unknown>[];
  stats: Record<string, { min: number; max: number; mean: number }>;
}

export async function getCropDataPreview(
  location: string, lat: number, lon: number, rows = 20, offset = 0
): Promise<DataPreview> {
  const { data } = await api.get("/api/crop/data/preview", {
    params: { location, lat, lon, rows, offset },
  });
  return data;
}

export function getCropDataXlsxUrl(location: string, lat: number, lon: number): string {
  return `/api/crop/data/download-xlsx?location=${encodeURIComponent(location)}&lat=${lat}&lon=${lon}`;
}
