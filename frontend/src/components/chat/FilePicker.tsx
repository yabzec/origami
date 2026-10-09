import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { DocTypeIcon } from "@/components/DocTypeIcon";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/dates";
import type { Document, DocRef, SearchResponse } from "@/lib/types";

const DEBOUNCE_MS = 300;
const RESULT_LIMIT = 10;

export function FilePicker({ pinnedIds, onPick }: { pinnedIds: string[]; onPick: (doc: DocRef) => void }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const debounced = useDebouncedValue(query.trim(), DEBOUNCE_MS);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation(); // capture phase: an enclosing dialog stays open
      setOpen(false);
      setQuery("");
    };
    const onPointer = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    };
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("mousedown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("mousedown", onPointer);
    };
  }, [open]);

  const search = useQuery({
    queryKey: ["file-picker", debounced],
    queryFn: () =>
      api.post<SearchResponse>("/api/search", { query: debounced, mode: "hybrid", limit: RESULT_LIMIT }),
    enabled: open && debounced.length > 0,
  });
  const docs = (search.data?.results ?? [])
    .map((r) => r.document)
    .filter((d) => !pinnedIds.includes(d.id))
    .slice(0, RESULT_LIMIT);

  const close = () => {
    setOpen(false);
    setQuery("");
  };
  const pick = (doc: Document) => {
    onPick({ id: doc.id, title: doc.title, document_date: doc.document_date });
    close();
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => (open ? close() : setOpen(true))}
        aria-haspopup="dialog"
        aria-expanded={open}
        className="rounded-full border border-dashed border-zinc-300 px-2 py-0.5 text-xs text-zinc-600 hover:bg-zinc-100"
      >
        + Add file
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="Add file"
          className="absolute top-full left-0 z-20 mt-1 w-80 rounded-md border border-zinc-200 bg-white p-2 shadow-lg"
        >
          <input
            autoFocus
            aria-label="Search files"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by title or content…"
            className="h-8 w-full rounded-md border border-zinc-300 px-2 text-sm focus:ring-2 focus:ring-brand-300 focus:outline-none"
          />
          {debounced === "" ? (
            <p className="px-2 py-1 text-sm text-zinc-400">Type to search your documents</p>
          ) : search.isPending ? (
            <p className="px-2 py-1 text-sm text-zinc-400">Searching…</p>
          ) : search.isError ? (
            <p className="px-2 py-1 text-sm text-red-700">Search failed</p>
          ) : docs.length === 0 ? (
            <p className="px-2 py-1 text-sm text-zinc-400">No documents found</p>
          ) : (
            <ul className="mt-1 max-h-72 overflow-y-auto">
              {docs.map((doc) => (
                <li key={doc.id}>
                  <button
                    type="button"
                    onClick={() => pick(doc)}
                    className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm hover:bg-zinc-100"
                  >
                    <DocTypeIcon doc={doc} className="h-4 w-4" />
                    <span className="flex-1 truncate">{doc.title}</span>
                    <span className="shrink-0 text-xs text-zinc-400">{formatDate(doc.document_date)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
