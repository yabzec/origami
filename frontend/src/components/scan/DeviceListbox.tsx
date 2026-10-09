import { useEffect, useId, useRef, useState, type FocusEvent, type KeyboardEvent, type ReactNode } from "react";
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
  const [activeId, setActiveId] = useState<string>("");
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const uid = useId();
  const { server, local } = groupDevices(devices);
  const ids = [...server.map((d) => d.id), ...local.map((d) => d.id), SEARCH_ID];
  const selected = devices.find((d) => d.id === value);
  // Track the highlight by id so devices arriving mid-search don't move it.
  const active = ids.includes(activeId) ? activeId : ids.includes(value ?? "") ? (value as string) : ids[0];
  const domId = (id: string) => `${uid}-opt-${id.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
  const listId = `${uid}-list`;

  const openList = () => {
    setActiveId(value ?? "");
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

  const move = (delta: number) => {
    const i = ids.indexOf(active);
    setActiveId(ids[Math.min(Math.max(i + delta, 0), ids.length - 1)]);
  };

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, [open]);

  useEffect(() => {
    if (open) document.getElementById(domId(active))?.scrollIntoView?.({ block: "nearest" });
  }); // eslint-disable-line react-hooks/exhaustive-deps

  // Escape closes from anywhere inside the popover; other keys only act on the trigger.
  const onRootKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (open && e.key === "Escape") {
      e.stopPropagation();
      setOpen(false);
      triggerRef.current?.focus();
      return;
    }
    if (e.target !== triggerRef.current) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) openList();
      else move(e.key === "ArrowDown" ? 1 : -1);
    } else if (e.key === "Enter" && open) {
      e.preventDefault();
      choose(active);
    }
  };

  const onRootBlur = (e: FocusEvent<HTMLDivElement>) => {
    const next = e.relatedTarget as Node | null;
    if (next && !e.currentTarget.contains(next)) setOpen(false);
  };

  const option = (d: ScanDevice) => (
    <li
      key={d.id}
      id={domId(d.id)}
      role="option"
      aria-selected={d.id === value}
      onMouseEnter={() => setActiveId(d.id)}
      onClick={() => choose(d.id)}
      className={`cursor-pointer rounded px-2 py-1.5 ${d.id === active ? "bg-zinc-100" : ""} ${d.id === value ? "font-medium" : ""}`}
    >
      {d.name}
    </li>
  );

  const group = (key: string, title: string, items: ScanDevice[], topPad: string) => (
    <li role="presentation">
      <div id={`${uid}-${key}`} className={`px-2 ${topPad} text-xs font-semibold text-zinc-500`}>
        {title}
      </div>
      <ul role="group" aria-labelledby={`${uid}-${key}`}>
        {items.map(option)}
      </ul>
    </li>
  );

  const status = searchLabel(search);
  
  return (
    <div ref={rootRef} className="relative w-64" onKeyDown={onRootKeyDown} onBlur={onRootBlur}>
      <button
        ref={triggerRef}
        type="button"
        role="combobox"
        aria-controls={open ? listId : undefined}
        aria-activedescendant={open ? domId(active) : undefined}
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
          <ul
            id={listId}
            role="listbox"
            aria-label="Scanners"
            onMouseDown={(e) => e.preventDefault() /* keep focus on the trigger */}
          >
            {server.length > 0 && group("server", "Server", server, "pt-1")}
            {local.length > 0 && group("local", "This computer's network", local, "pt-2")}
            <li
              id={domId(SEARCH_ID)}
              role="option"
              aria-selected={false}
              onMouseEnter={() => setActiveId(SEARCH_ID)}
              onClick={() => choose(SEARCH_ID)}
              className={`mt-1 flex cursor-pointer items-center justify-between rounded border-t border-zinc-100 px-2 py-1.5 text-brand-700 ${active === SEARCH_ID ? "bg-zinc-100" : ""}`}
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
