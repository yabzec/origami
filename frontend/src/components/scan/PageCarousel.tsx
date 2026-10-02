import { usePreviewImage } from "@/hooks/usePreviewImage";
import { cn } from "@/lib/utils";
import type { ScanPageInfo } from "@/lib/types";

function Thumb({
  page,
  selected,
  isFirst,
  isLast,
  disabled,
  onSelect,
  onMove,
  onDelete,
}: {
  page: ScanPageInfo;
  selected: boolean;
  isFirst: boolean;
  isLast: boolean;
  disabled: boolean;
  onSelect: () => void;
  onMove: (direction: -1 | 1) => void;
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
        <span className="flex gap-1">
          <button disabled={disabled || isFirst} onClick={() => onMove(-1)} title="Move left">
            ←
          </button>
          <button disabled={disabled || isLast} onClick={() => onMove(1)} title="Move right">
            →
          </button>
          <button disabled={disabled} onClick={onDelete} title="Delete page" className="text-red-500">
            ×
          </button>
        </span>
      </div>
    </div>
  );
}

export function PageCarousel({
  pages,
  selectedPageId,
  disabled,
  onSelect,
  onMove,
  onDelete,
}: {
  pages: ScanPageInfo[];
  selectedPageId: number | null;
  disabled: boolean;
  onSelect: (pageId: number) => void;
  onMove: (index: number, direction: -1 | 1) => void;
  onDelete: (pageId: number) => void;
}) {
  if (pages.length === 0) return null;
  return (
    <div className="flex gap-3 overflow-x-auto pb-2">
      {pages.map((page, index) => (
        <Thumb
          key={page.id}
          page={page}
          selected={page.id === selectedPageId}
          isFirst={index === 0}
          isLast={index === pages.length - 1}
          disabled={disabled}
          onSelect={() => onSelect(page.id)}
          onMove={(direction) => onMove(index, direction)}
          onDelete={() => onDelete(page.id)}
        />
      ))}
    </div>
  );
}
