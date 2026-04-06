interface Props {
  name: string;
  value: number | null;
  unit: string;
  good: number;
  fair: number;
  higherIsBetter?: boolean;
}

export default function MetricsCard({
  name,
  value,
  unit,
  good,
  fair,
  higherIsBetter = false,
}: Props) {
  const getColor = () => {
    if (value === null) return "text-gray-400";
    if (higherIsBetter) {
      if (value >= good) return "text-green-400";
      if (value >= fair) return "text-yellow-400";
      return "text-red-400";
    }
    const absVal = Math.abs(value);
    if (absVal <= good) return "text-green-400";
    if (absVal <= fair) return "text-yellow-400";
    return "text-red-400";
  };

  return (
    <div className="bg-gray-800 rounded-lg p-4 text-center">
      <div className="text-xs text-gray-400 uppercase tracking-wide mb-1">
        {name}
      </div>
      <div className={`text-2xl font-bold ${getColor()}`}>
        {value !== null ? value.toFixed(2) : "—"}
      </div>
      <div className="text-xs text-gray-500">{unit}</div>
    </div>
  );
}
