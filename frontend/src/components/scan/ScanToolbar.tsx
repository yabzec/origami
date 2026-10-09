import { AgentInstallPanel } from "@/components/scan/AgentInstallPanel";
import { DeviceListbox } from "@/components/scan/DeviceListbox";
import type { SearchState } from "@/lib/localScan";
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
  search,
  onSearch,
  onOpen,
  onInstalled,
}: {
  status: ScanStatus | undefined;
  devices: ScanDevice[];
  device: string | null;
  onDeviceChange: (device: string | null) => void;
  search: SearchState;
  onSearch: () => void;
  onOpen: () => void;
  onInstalled: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center justify-end gap-3 text-sm">
      <StatusPill status={status} />
      <DeviceListbox
        devices={devices}
        value={device}
        onChange={onDeviceChange}
        search={search}
        onSearch={onSearch}
        onOpen={onOpen}
        installPanel={<AgentInstallPanel onInstalled={onInstalled} />}
      />
    </div>
  );
}
