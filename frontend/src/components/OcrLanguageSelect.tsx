import { useEffect, useRef, useState } from "react";
import { useOcrLanguages } from "@/hooks/useOcrLanguages";
import { languagesLabel, splitLanguages, toggleLanguage } from "@/lib/ocrLanguages";

export function OcrLanguageSelect({
  id,
  value,
  onChange,
  disabled,
}: {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const { data } = useOcrLanguages();
  const languages = data?.languages ?? [];
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const selected = splitLanguages(value);

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation(); // keep an enclosing Dialog open
      setOpen(false);
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open]);

  if (data && languages.length === 0)
    return <p className="text-sm text-red-600">No OCR languages installed</p>;

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        id={id}
        disabled={disabled}
        onClick={() => setOpen(!open)}
        aria-haspopup="dialog"
        aria-expanded={open}
        className="flex h-9 w-full items-center justify-between rounded-md border border-zinc-300 bg-white px-2 text-left text-sm disabled:opacity-50"
      >
        <span className="truncate">{languagesLabel(value, languages)}</span>
        <span aria-hidden="true" className="ml-2 text-zinc-400">
          ▾
        </span>
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="OCR languages"
          className="absolute top-full right-0 left-0 z-20 mt-1 max-h-64 overflow-y-auto rounded-md border border-zinc-200 bg-white p-2 shadow-lg"
        >
          {languages.map((l) => {
            const checked = selected.includes(l.code);
            return (
              <label key={l.code} className="flex items-center gap-2 px-1 py-0.5 text-sm">
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={checked && selected.length === 1}
                  onChange={() => onChange(toggleLanguage(value, l.code))}
                />
                <span className="flex-1">{l.name}</span>
                {checked && (
                  <span aria-hidden="true" className="text-xs text-zinc-400">
                    {selected.indexOf(l.code) + 1}
                  </span>
                )}
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}
