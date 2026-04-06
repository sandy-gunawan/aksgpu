import { type ModelType, MODEL_TYPES, MODEL_LABELS } from "../services/api";

interface Props {
  selected: ModelType;
  onChange: (model: ModelType) => void;
  showCompare?: boolean;
  compareActive?: boolean;
  onCompareToggle?: () => void;
}

export default function ModelSelector({
  selected,
  onChange,
  showCompare = false,
  compareActive = false,
  onCompareToggle,
}: Props) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-gray-400">Model:</span>
      <div className="flex gap-1 bg-gray-800 rounded-lg p-0.5">
        {MODEL_TYPES.map((mt) => (
          <button
            key={mt}
            onClick={() => {
              onChange(mt);
              if (compareActive && onCompareToggle) onCompareToggle();
            }}
            className={`px-3 py-1 rounded text-xs font-medium transition ${
              !compareActive && selected === mt
                ? "bg-blue-600 text-white"
                : "text-gray-400 hover:text-gray-200"
            }`}
          >
            {MODEL_LABELS[mt]}
          </button>
        ))}
        {showCompare && (
          <button
            onClick={onCompareToggle}
            className={`px-3 py-1 rounded text-xs font-medium transition ${
              compareActive
                ? "bg-purple-600 text-white"
                : "text-gray-400 hover:text-gray-200"
            }`}
          >
            Compare All
          </button>
        )}
      </div>
    </div>
  );
}
