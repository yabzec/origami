import { usePreviewImage } from "@/hooks/usePreviewImage";
import { cn } from "@/lib/utils";
import type { ScanPageInfo } from "@/lib/types";

function Thumb({
  page,
  selected,
  disabled,
  onSelect,
  onDelete,
}: {
  page: ScanPageInfo;
  selected: boolean;
  disabled: boolean;
  onSelect: () => void;
  onDelete: () => void;
}) {
  const url = usePreviewImage(page.id);
  return (
    <div
      className={cn(
        "w-28 shrink-0 rounded border bg-white p-1",
        selected ? "border-zinc-900 ring-2 ring-zinc-900" : "border-zinc-200",
      )}
    >
      <button type="button" className="block w-full" onClick={onSelect} title={`Show page ${page.page_number}`}>
        {url ? (
          <img src={url} alt={`Page ${page.page_number}`} className="h-32 w-full rounded object-cover" />
        ) : (
          <div className="flex h-32 items-center justify-center text-zinc-300">…</div>
        )}
      </button>
      <div className="mt-1 flex items-center justify-between text-xs text-zinc-500">
        <span>p. {page.page_number}</span>
        <button
          type="button"
          disabled={disabled}
          onClick={onDelete}
          aria-label="Delete page"
          title="Delete page"
          className="flex h-6 w-6 items-center justify-center rounded text-base leading-none text-red-600 hover:bg-red-50 disabled:opacity-40"
        >
          ×
        </button>
      </div>
    </div>
  );
}

export function PageCarousel({
  pages,
  selectedPageId,
  disabled,
  onSelect,
  onDelete,
}: {
  pages: ScanPageInfo[];
  selectedPageId: number | null;
  disabled: boolean;
  onSelect: (pageId: number) => void;
  onDelete: (pageId: number) => void;
}) {
  if (pages.length === 0) return null;
  return (
    <div className="flex gap-3 overflow-x-auto pb-2">
      {pages.map((page) => (
        <Thumb
          key={page.id}
          page={page}
          selected={page.id === selectedPageId}
          disabled={disabled}
          onSelect={() => onSelect(page.id)}
          onDelete={() => onDelete(page.id)}
        />
      ))}
    </div>
  );
}
