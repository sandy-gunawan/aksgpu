import axios from "axios";

const api = axios.create({ baseURL: "" });

export type ModelType = "lstm" | "xgboost" | "arima";
export const MODEL_TYPES: ModelType[] = ["lstm", "xgboost", "arima"];
export const MODEL_LABELS: Record<ModelType, string> = {
  lstm: "LSTM",
  xgboost: "XGBoost",
  arima: "ARIMA",
};

export interface ForecastPoint {
  time: string;
  temperature: number;
  humidity: number;
  wind_speed: number;
  precipitation: number;
  pressure: number;
}

export interface ForecastResponse {
  city: string;
  model_type: string;
  generated_at: string;
  forecast: ForecastPoint[];
}

export interface CompareResponse {
  city: string;
  generated_at: string;
  models: Record<string, { forecast?: ForecastPoint[]; error?: string }>;
}

export interface ValidationMetrics {
  mae: number;
  rmse: number;
  r2: number;
  bias: number;
}

export interface ValidationResponse {
  model_type: string;
  metrics: ValidationMetrics;
  predicted: ForecastPoint[];
  actual: ForecastPoint[];
}

export interface CompareValidationResponse {
  city: string;
  models: Record<string, ValidationResponse & { error?: string }>;
}

export interface TrainingStatus {
  status: string;
  model_type?: string;
  last_trained: string | null;
  model_file: string | null;
  duration_minutes: number | null;
  final_loss: number | null;
  epochs_completed: number | null;
  device?: string;
}

export interface HealthResponse {
  status: string;
  gpu_available: boolean;
  model_loaded: boolean;
  loaded_models: string[];
  cuda_device: string | null;
}

export async function getForecast(
  city: string,
  days: number,
  lat?: number,
  lon?: number,
  modelType: ModelType = "lstm"
): Promise<ForecastResponse> {
  const { data } = await api.get("/api/predict", {
    params: { city, days, lat, lon, model_type: modelType },
  });
  return data;
}

export async function getCompare(
  city: string,
  days: number,
  lat?: number,
  lon?: number
): Promise<CompareResponse> {
  const { data } = await api.get("/api/compare", {
    params: { city, days, lat, lon },
  });
  return data;
}

export async function getValidation(
  city: string,
  lookbackDays: number,
  lat?: number,
  lon?: number,
  modelType: ModelType = "lstm"
): Promise<ValidationResponse> {
  const { data } = await api.get("/api/validate", {
    params: { city, lookback_days: lookbackDays, lat, lon, model_type: modelType },
  });
  return data;
}

export async function getCompareValidation(
  city: string,
  lookbackDays: number,
  lat?: number,
  lon?: number
): Promise<CompareValidationResponse> {
  const { data } = await api.get("/api/validate/compare", {
    params: { city, lookback_days: lookbackDays, lat, lon },
  });
  return data;
}

export async function getTrainingStatus(
  modelType: ModelType = "lstm"
): Promise<TrainingStatus> {
  const { data } = await api.get("/api/training/status", {
    params: { model_type: modelType },
  });
  return data;
}

export async function getAllTrainingStatus(city?: string): Promise<Record<string, TrainingStatus>> {
  const params: Record<string, string> = {};
  if (city) params.city = city;
  const { data } = await api.get("/api/training/status/all", { params });
  return data;
}

export async function triggerTraining(
  modelType: ModelType = "lstm",
  city?: string,
  lat?: number,
  lon?: number,
): Promise<{
  status: string;
  message: string;
  jobs: Array<{ model_type: string; job_name: string | null; status: string }>;
  commands: string[];
}> {
  const params: Record<string, string | number> = { model_type: modelType };
  if (city) params.city = city;
  if (lat !== undefined) params.lat = lat;
  if (lon !== undefined) params.lon = lon;
  const { data } = await api.post("/api/training/trigger", null, { params });
  return data;
}

export interface TrainingJob {
  name: string;
  model_type: string;
  status: string;          // pending | running | completed | failed
  created: string | null;
  started: string | null;
  completed: string | null;
}

export async function getTrainingJobs(): Promise<{ jobs: TrainingJob[] }> {
  const { data } = await api.get("/api/training/jobs");
  return data;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function getTrainingParams(): Promise<any> {
  const { data } = await api.get("/api/training/params");
  return data;
}

export interface JobLogs {
  job_name: string;
  status: string;
  logs: string;
  pod_name: string | null;
  phase: string;
}

export async function getJobLogs(jobName: string, tail: number = 200): Promise<JobLogs> {
  const { data } = await api.get(`/api/training/jobs/${jobName}/logs`, {
    params: { tail },
  });
  return data;
}

export async function getHealth(): Promise<HealthResponse> {
  const { data } = await api.get("/api/health");
  return data;
}

// --- Weather Report ---

export interface WeatherReportSection {
  title: string;
  text: string;
}

export interface WeatherDaySummary {
  day: string;
  high: number;
  low: number;
  avg: number;
  precipitation: number;
  max_wind: number;
  humidity: number;
  condition: string;
}

export interface WeatherReport {
  city: string;
  model_type: string;
  generated_at: string;
  report: {
    summary: string;
    sections: WeatherReportSection[];
    daily: WeatherDaySummary[];
    stats: { high: number; low: number; total_precipitation: number; max_wind: number; rain_days: number; temp_trend: string };
    recommendations: string[];
  };
}

export async function getWeatherReport(
  city: string, days: number = 7, lat?: number, lon?: number, modelType: ModelType = "lstm"
): Promise<WeatherReport> {
  const params: Record<string, string | number> = { city, days, model_type: modelType };
  if (lat !== undefined) params.lat = lat;
  if (lon !== undefined) params.lon = lon;
  const { data } = await api.get("/api/report", { params });
  return data;
}

// --- Data Management ---

export interface DataStatus {
  available: boolean;
  city: string;
  lat: number;
  lon: number;
  start_date?: string;
  end_date?: string;
  records?: number;
  downloaded_at?: string;
  size_bytes?: number;
  message?: string;
}

export interface DataDownloadResult {
  status: string;
  city: string;
  start_date: string;
  end_date: string;
  records: number;
  size_kb: number;
  download_seconds: number;
}

export async function getDataStatus(
  city: string,
  lat: number,
  lon: number
): Promise<DataStatus> {
  const { data } = await api.get("/api/data/status", {
    params: { city, lat, lon },
  });
  return data;
}

export async function downloadData(
  city: string,
  lat: number,
  lon: number,
  years: number = 1,
  months: number = 0
): Promise<DataDownloadResult> {
  const { data } = await api.post("/api/data/download", null, {
    params: { city, lat, lon, years, months },
    timeout: 900000, // 15 min timeout for large downloads (2+ years)
  });
  return data;
}
