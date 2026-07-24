import type { ScanDevice } from "./types";

export function scanDeviceHint(devices: ScanDevice[]): "none" | "single" | "multiple" {
  if (devices.length === 0) return "none";
  return devices.length === 1 ? "single" : "multiple";
}
