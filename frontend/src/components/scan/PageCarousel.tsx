import {
  closestCenter,
  DndContext,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  horizontalListSortingStrategy,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { usePreviewImage } from "@/hooks/usePreviewImage";
import { cn } from "@/lib/utils";
import type { ScanPageInfo } from "@/lib/types";

function Thumb({
  page,
  index,
  count,
  selected,
  disabled,
  onSelect,
  onDelete,
  onMove,
}: {
  page: ScanPageInfo;
  index: number;
  count: number;
  selected: boolean;
  disabled: boolean;
  onSelect: () => void;
  onDelete: () => void;
  onMove: (toIndex: number) => void;
}) {
  const url = usePreviewImage(page.id);
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: page.id,
    disabled,
  });
  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn(
        "w-28 shrink-0 rounded border bg-white p-1",
        selected ? "border-zinc-900 ring-2 ring-zinc-900" : "border-zinc-200",
        isDragging && "opacity-60",
      )}
    >
      <button
        type="button"
        className="block w-full cursor-grab active:cursor-grabbing"
        onClick={onSelect}
        title={`Show page ${page.page_number} (drag to reorder)`}
        {...attributes}
        {...listeners}
      >
        {url ? (
          <img src={url} alt={`Page ${page.page_number}`} className="h-32 w-full rounded object-cover" />
        ) : (
          <div className="flex h-32 items-center justify-center text-zinc-300">…</div>
        )}
      </button>
      <div className="mt-1 flex items-center justify-between text-xs text-zinc-500">
        {selected ? (
          <button
            type="button"
            aria-label={`Move page ${page.page_number} left`}
            disabled={disabled || index === 0}
            onClick={() => onMove(index - 1)}
            className="h-6 w-6 rounded hover:bg-zinc-100 disabled:opacity-30"
          >
            ←
          </button>
        ) : (
          <span className="w-6" />
        )}
        <span>p. {page.page_number}</span>
        {selected && (
          <button
            type="button"
            aria-label={`Move page ${page.page_number} right`}
            disabled={disabled || index === count - 1}
            onClick={() => onMove(index + 1)}
            className="h-6 w-6 rounded hover:bg-zinc-100 disabled:opacity-30"
          >
            →
          </button>
        )}
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
  onMove,
}: {
  pages: ScanPageInfo[];
  selectedPageId: number | null;
  disabled: boolean;
  onSelect: (pageId: number) => void;
  onDelete: (pageId: number) => void;
  onMove: (pageId: number, toIndex: number) => void;
}) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }), // a click still selects
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  if (pages.length === 0) return null;

  const onDragEnd = ({ active, over }: DragEndEvent) => {
    if (!over || active.id === over.id) return;
    onMove(Number(active.id), pages.findIndex((p) => p.id === over.id));
  };

  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
      <SortableContext items={pages.map((p) => p.id)} strategy={horizontalListSortingStrategy}>
        <div className="flex gap-3 overflow-x-auto pb-2">
          {pages.map((page, index) => (
            <Thumb
              key={page.id}
              page={page}
              index={index}
              count={pages.length}
              selected={page.id === selectedPageId}
              disabled={disabled}
              onSelect={() => onSelect(page.id)}
              onDelete={() => onDelete(page.id)}
              onMove={(toIndex) => onMove(page.id, toIndex)}
            />
          ))}
        </div>
      </SortableContext>
    </DndContext>
  );
}
