import { useEffect, useRef, useState } from "react";
import { useFolders } from "@/hooks/useFolders";
import { childrenOf, folderLabel, folderPath } from "@/lib/folderTree";
import { cn } from "@/lib/utils";

const itemClass = "flex w-full items-center justify-between rounded px-2 py-1 text-left text-sm hover:bg-zinc-100";

export function FolderPicker({
  value,
  onChange,
  id,
}: {
  value: number | null;
  onChange: (id: number | null) => void;
  id?: string;
}) {
  const { data } = useFolders();
  const folders = data ?? [];
  const [open, setOpen] = useState(false);
  const [level, setLevel] = useState<number | null>(null); // parent whose children are listed
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation(); // capture phase on document: an enclosing Dialog (window listener) stays open
      setOpen(false);
    };
    const onPointer = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("mousedown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("mousedown", onPointer);
    };
  }, [open]);

  const toggle = () => {
    if (open) {
      setOpen(false);
      return;
    }
    // browse the selected folder's level so its siblings are visible
    const selectedPath = folderPath(folders, value);
    setLevel(selectedPath.length >= 2 ? selectedPath[selectedPath.length - 2].id : null);
    setOpen(true);
  };

  const crumbs = folderPath(folders, level);
  const entries = childrenOf(folders, level);
  const back = () => setLevel(crumbs.length >= 2 ? crumbs[crumbs.length - 2].id : null);

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        id={id}
        onClick={toggle}
        aria-haspopup="dialog"
        aria-expanded={open}
        className="flex h-9 w-full items-center justify-between rounded-md border border-zinc-300 bg-white px-2 text-left text-sm focus:outline-none focus:ring-2 focus:ring-brand-300"
      >
        <span className="truncate">{folderLabel(folders, value)}</span>
        <span aria-hidden="true" className="ml-2 text-zinc-400">
          ▾
        </span>
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="Choose folder"
          className="absolute top-full right-0 left-0 z-20 mt-1 rounded-md border border-zinc-200 bg-white p-2 shadow-lg"
        >
          <div className="mb-1 flex items-center gap-2 border-b border-zinc-100 pb-1 text-xs text-zinc-500">
            {level !== null && (
              <button type="button" onClick={back} className="shrink-0 text-zinc-700 hover:underline">
                ↑ Back
              </button>
            )}
            <span className="truncate">{["Top level", ...crumbs.map((f) => f.name)].join(" / ")}</span>
          </div>
          <ul className="max-h-60 overflow-y-auto">
            {level === null && (
              <li>
                <button
                  type="button"
                  onClick={() => onChange(null)}
                  className={cn(itemClass, value === null && "bg-zinc-100 font-medium")}
                >
                  (root)
                </button>
              </li>
            )}
            {entries.map((f) => (
              <li key={f.id}>
                <button
                  type="button"
                  onClick={() => {
                    onChange(f.id);
                    if (childrenOf(folders, f.id).length > 0) setLevel(f.id);
                    else setOpen(false); // nothing below: the choice is final
                  }}
                  className={cn(itemClass, value === f.id && "bg-zinc-100 font-medium")}
                >
                  <span className="truncate">{f.name}</span>
                  {childrenOf(folders, f.id).length > 0 && (
                    <span aria-hidden="true" className="text-zinc-400">
                      ›
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
          {entries.length === 0 && <p className="px-2 py-1 text-sm text-zinc-400">No subfolders</p>}
          <div className="mt-1 flex justify-end border-t border-zinc-100 pt-1">
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="rounded px-2 py-1 text-sm font-medium hover:bg-zinc-100"
            >
              Done
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
