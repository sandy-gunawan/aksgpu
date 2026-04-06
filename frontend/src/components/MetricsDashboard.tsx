import type { ValidationMetrics } from "../services/api";
import MetricsCard from "./MetricsCard";

interface Props {
  metrics: ValidationMetrics | null;
}

export default function MetricsDashboard({ metrics }: Props) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      <MetricsCard
        name="MAE"
        value={metrics?.mae ?? null}
        unit="°C"
        good={2}
        fair={4}
      />
      <MetricsCard
        name="RMSE"
        value={metrics?.rmse ?? null}
        unit="°C"
        good={3}
        fair={5}
      />
      <MetricsCard
        name="R²"
        value={metrics?.r2 ?? null}
        unit=""
        good={0.8}
        fair={0.6}
        higherIsBetter
      />
      <MetricsCard
        name="Bias"
        value={metrics?.bias ?? null}
        unit="°C"
        good={0.5}
        fair={1.5}
      />
    </div>
  );
}
