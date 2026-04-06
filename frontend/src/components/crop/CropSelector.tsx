import { useState, useEffect } from "react";
import { getCropPresets, type CropPreset } from "../../services/cropApi";

export interface LocationCoords {
  name: string;
  lat: number;
  lon: number;
  crop?: string;
}

interface Props {
  selected: LocationCoords;
  onChange: (loc: LocationCoords) => void;
}

const FALLBACK_PRESETS: Record<string, CropPreset> = {
  "palm-riau": { name: "Palm Oil - Riau, Indonesia", lat: 1.5, lon: 102.1, crop: "palm" },
  "palm-kalimantan": { name: "Palm Oil - Central Kalimantan", lat: -1.68, lon: 113.38, crop: "palm" },
  "palm-sabah": { name: "Palm Oil - Sabah, Malaysia", lat: 5.3, lon: 117.6, crop: "palm" },
  "rice-mekong": { name: "Rice - Mekong Delta, Vietnam", lat: 10.03, lon: 105.78, crop: "rice" },
  "corn-iowa": { name: "Corn - Iowa, USA", lat: 42.03, lon: -93.47, crop: "corn" },
  "wheat-punjab": { name: "Wheat - Punjab, India", lat: 30.9, lon: 75.85, crop: "wheat" },
};

export default function CropSelector({ selected, onChange }: Props) {
  const [presets, setPresets] = useState<Record<string, CropPreset>>(FALLBACK_PRESETS);
  const [custom, setCustom] = useState(false);
  const [customName, setCustomName] = useState("");
  const [customLat, setCustomLat] = useState("");
  const [customLon, setCustomLon] = useState("");

  useEffect(() => {
    getCropPresets().then(setPresets).catch(() => {});
  }, []);

  const handlePreset = (key: string) => {
    if (key === "__custom__") {
      setCustom(true);
      return;
    }
    setCustom(false);
    const p = presets[key];
    if (p) onChange({ name: key, lat: p.lat, lon: p.lon, crop: p.crop });
  };

  const handleCustomSubmit = () => {
    const lat = parseFloat(customLat);
    const lon = parseFloat(customLon);
    if (isNaN(lat) || isNaN(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 180) return;
    onChange({ name: customName || `custom-${lat}-${lon}`, lat, lon });
    setCustom(false);
  };

  return (
    <div className="flex items-center gap-2">
      <select
        value={custom ? "__custom__" : selected.name}
        onChange={(e) => handlePreset(e.target.value)}
        className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm"
      >
        {Object.entries(presets).map(([key, p]) => (
          <option key={key} value={key}>{p.name}</option>
        ))}
        <option value="__custom__">Custom coordinates...</option>
      </select>
      {custom && (
        <div className="flex items-center gap-1">
          <input value={customName} onChange={(e) => setCustomName(e.target.value)}
                 placeholder="Name" className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm w-24" />
          <input value={customLat} onChange={(e) => setCustomLat(e.target.value)}
                 placeholder="Lat" className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm w-16" />
          <input value={customLon} onChange={(e) => setCustomLon(e.target.value)}
                 placeholder="Lon" className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm w-16" />
          <button onClick={handleCustomSubmit}
                  className="bg-green-600 text-white px-2 py-1 rounded text-sm">Go</button>
        </div>
      )}
    </div>
  );
}
