import { Select } from "@/components/ui/select";
import { scanDeviceHint } from "@/lib/scanDevices";
import type { ScanDevice, ScanStatus } from "@/lib/types";

function StatusPill({ status }: { status: ScanStatus | undefined }) {
  if (!status) return <span className="rounded-full bg-zinc-100 px-3 py-1 text-zinc-500">Checking scanner…</span>;
  if (!status.available)
    return <span className="rounded-full bg-red-50 px-3 py-1 text-red-700">● Scanner offline</span>;
  return (
    <span className="rounded-full bg-green-50 px-3 py-1 text-green-700">
      ● Scanner ready{status.busy ? " (busy)" : ""}
    </span>
  );
}

export function ScanToolbar({
  status,
  devices,
  device,
  onDeviceChange,
}: {
  status: ScanStatus | undefined;
  devices: ScanDevice[];
  device: string | null;
  onDeviceChange: (device: string | null) => void;
}) {
  const hint = scanDeviceHint(devices);
  return (
    <div className="flex flex-wrap items-center justify-end gap-3 text-sm">
      <StatusPill status={status} />
      {hint === "none" && <span className="text-red-600">No scanner detected — check power and USB.</span>}
      {hint === "single" && <span className="text-zinc-600">{devices[0].name}</span>}
      {hint === "multiple" && (
        <Select
          aria-label="Scanner"
          className="w-56"
          value={device ?? ""}
          onChange={(e) => onDeviceChange(e.target.value || null)}
        >
          {devices.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </Select>
      )}
    </div>
  );
}
