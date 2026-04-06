export interface CityCoords {
  name: string;
  lat: number;
  lon: number;
}

interface Props {
  selected: CityCoords;
  onChange: (city: CityCoords) => void;
}

const PRESET_CITIES: CityCoords[] = [
  { name: "New York", lat: 40.71, lon: -74.01 },
  { name: "London", lat: 51.51, lon: -0.13 },
  { name: "Tokyo", lat: 35.68, lon: 139.69 },
  { name: "Sydney", lat: -33.87, lon: 151.21 },
  { name: "Sao Paulo", lat: -23.55, lon: -46.63 },
  { name: "Paris", lat: 48.86, lon: 2.35 },
  { name: "Singapore", lat: 1.35, lon: 103.82 },
  { name: "Dubai", lat: 25.28, lon: 55.3 },
  { name: "Mumbai", lat: 19.08, lon: 72.88 },
  { name: "Jakarta", lat: -6.21, lon: 106.85 },
];

import { useState } from "react";

export default function CitySelector({ selected, onChange }: Props) {
  const [showCustom, setShowCustom] = useState(false);
  const [customLat, setCustomLat] = useState("");
  const [customLon, setCustomLon] = useState("");
  const [customName, setCustomName] = useState("");

  const handlePreset = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const val = e.target.value;
    if (val === "__custom__") {
      setShowCustom(true);
      return;
    }
    setShowCustom(false);
    const city = PRESET_CITIES.find((c) => c.name === val);
    if (city) onChange(city);
  };

  const handleCustomSubmit = () => {
    const lat = parseFloat(customLat);
    const lon = parseFloat(customLon);
    if (isNaN(lat) || isNaN(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 180) return;
    onChange({ name: customName || `${lat}, ${lon}`, lat, lon });
    setShowCustom(false);
  };

  return (
    <div className="flex items-center gap-2">
      <select
        value={PRESET_CITIES.some((c) => c.name === selected.name) ? selected.name : "__custom__"}
        onChange={handlePreset}
        className="bg-gray-800 text-gray-100 border border-gray-600 rounded px-3 py-1.5 text-sm"
      >
        {PRESET_CITIES.map((c) => (
          <option key={c.name} value={c.name}>
            {c.name}
          </option>
        ))}
        <option value="__custom__">Custom coordinates...</option>
      </select>

      {showCustom && (
        <div className="flex items-center gap-1">
          <input
            type="text"
            placeholder="Name"
            value={customName}
            onChange={(e) => setCustomName(e.target.value)}
            className="bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm w-24"
          />
          <input
            type="number"
            placeholder="Lat"
            value={customLat}
            onChange={(e) => setCustomLat(e.target.value)}
            className="bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm w-20"
            min={-90}
            max={90}
            step={0.01}
          />
          <input
            type="number"
            placeholder="Lon"
            value={customLon}
            onChange={(e) => setCustomLon(e.target.value)}
            className="bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm w-20"
            min={-180}
            max={180}
            step={0.01}
          />
          <button
            onClick={handleCustomSubmit}
            className="bg-blue-600 hover:bg-blue-700 text-white text-sm px-3 py-1 rounded"
          >
            Go
          </button>
        </div>
      )}

      <span className="text-xs text-gray-500">
        {selected.lat.toFixed(2)}, {selected.lon.toFixed(2)}
      </span>
    </div>
  );
}
