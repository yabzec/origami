import { useEffect, useRef, useState, type ReactNode } from "react";
import { groupDevices, type SearchState } from "@/lib/localScan";
import type { ScanDevice } from "@/lib/types";

const SEARCH_ID = "__search__";

function searchLabel(search: SearchState): string | null {
  switch (search.phase) {
    case "searching":
      return "Searching…";
    case "found":
      return `${search.found} found`;
    case "none":
      return "No scanners found";
    case "unresponsive":
      return "Agent not responding";
    default:
      return null;
  }
}

export function DeviceListbox({
  devices,
  value,
  onChange,
  search,
  onSearch,
  onOpen,
  installPanel,
}: {
  devices: ScanDevice[];
  value: string | null;
  onChange: (id: string) => void;
  search: SearchState;
  onSearch: () => void;
  onOpen: () => void;
  installPanel: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const { server, local } = groupDevices(devices);
  const ids = [...server.map((d) => d.id), ...local.map((d) => d.id), SEARCH_ID];
  const selected = devices.find((d) => d.id === value);

  const openList = () => {
    setActive(Math.max(0, ids.indexOf(value ?? "")));
    setOpen(true);
    onOpen();
  };

  const choose = (id: string) => {
    if (id === SEARCH_ID) {
      onSearch(); // keeps the list open
      return;
    }
    onChange(id);
    setOpen(false);
  };

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setOpen(false);
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive((i) => Math.min(i + 1, ids.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive((i) => Math.max(i - 1, 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        choose(ids[active]);
      }
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey, true);
    };
  }); // re-bind every render: handlers read the current ids/active

  const option = (d: ScanDevice) => {
    const index = ids.indexOf(d.id);
    return (
      <li
        key={d.id}
        role="option"
        aria-selected={d.id === value}
        onMouseEnter={() => setActive(index)}
        onClick={() => choose(d.id)}
        className={`cursor-pointer rounded px-2 py-1.5 ${index === active ? "bg-zinc-100" : ""} ${d.id === value ? "font-medium" : ""}`}
      >
        {d.name}
      </li>
    );
  };

  const status = searchLabel(search);
  const searchIndex = ids.length - 1;

  return (
    <div ref={rootRef} className="relative w-64">
      <button
        type="button"
        aria-label={`Scanner: ${selected?.name ?? "none selected"}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => (open ? setOpen(false) : openList())}
        className="flex h-9 w-full items-center justify-between rounded-md border border-zinc-300 bg-white px-2 text-left text-sm"
      >
        <span className="truncate">{selected?.name ?? "No scanner selected"}</span>
        <span aria-hidden="true" className="ml-2 text-zinc-400">
          ▾
        </span>
      </button>
      {open && (
        <div className="absolute top-full right-0 z-20 mt-1 w-80 rounded-md border border-zinc-200 bg-white p-1 text-sm shadow-lg">
          <ul role="listbox" aria-label="Scanners">
            {server.length > 0 && (
              <li role="presentation" className="px-2 pt-1 text-xs font-semibold text-zinc-500">
                Server
              </li>
            )}
            {server.map(option)}
            {local.length > 0 && (
              <li role="presentation" className="px-2 pt-2 text-xs font-semibold text-zinc-500">
                This computer's network
              </li>
            )}
            {local.map(option)}
            <li
              role="option"
              aria-selected={false}
              onMouseEnter={() => setActive(searchIndex)}
              onClick={() => choose(SEARCH_ID)}
              className={`mt-1 flex cursor-pointer items-center justify-between rounded border-t border-zinc-100 px-2 py-1.5 text-brand-700 ${active === searchIndex ? "bg-zinc-100" : ""}`}
            >
              <span>Search local scanners</span>
              {status && (
                <span className="text-xs text-zinc-500">
                  {search.phase === "searching" && <span aria-hidden="true" className="mr-1 inline-block animate-spin">◌</span>}
                  {status}
                </span>
              )}
            </li>
          </ul>
          {(search.phase === "install" || search.phase === "unresponsive") && installPanel}
        </div>
      )}
    </div>
  );
}
