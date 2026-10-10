import { Fragment } from "react";
import { childrenOf, folderPath, subtreeDocumentCount } from "@/lib/folderTree";
import { folderKey } from "@/lib/selection";
import type { Folder } from "@/lib/types";
import { cn } from "@/lib/utils";

export function FolderTiles({
  folders,
  parentId,
  onOpen,
  selected,
  onToggleSelect,
}: {
  folders: Folder[];
  parentId: number | null;
  onOpen: (id: number) => void;
  selected?: ReadonlySet<string>;
  onToggleSelect?: (key: string, shift: boolean) => void;
}) {
  const children = childrenOf(folders, parentId);
  if (children.length === 0) return null;
  const selecting = (selected?.size ?? 0) > 0;
  return (
    <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
      {children.map((f) => {
        const key = folderKey(f.id);
        const isSelected = selected?.has(key) ?? false;
        const count = subtreeDocumentCount(folders, f.id);
        return (
          <div
            key={f.id}
            className={cn(
              "group relative rounded-lg border bg-white hover:shadow",
              isSelected ? "border-brand-500 ring-1 ring-brand-500" : "border-zinc-200",
            )}
          >
            {onToggleSelect && (
              <input
                type="checkbox"
                aria-label={`Select folder ${f.name}`}
                checked={isSelected}
                onChange={() => {}}
                onClick={(e) => onToggleSelect(key, e.shiftKey)}
                className={cn(
                  "absolute top-2 left-2 z-10 h-4 w-4",
                  selecting || isSelected ? "opacity-100" : "opacity-100 focus:opacity-100 md:opacity-0 md:group-hover:opacity-100",
                )}
              />
            )}
            <button
              type="button"
              onClick={() => onOpen(f.id)}
              className={cn("flex w-full cursor-pointer items-center gap-2 p-3 text-left", onToggleSelect && "pl-7")}
            >
              <span aria-hidden="true" className="text-xl">
                📁
              </span>
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium">{f.name}</span>
                <span className="block text-xs text-zinc-400">
                  {count} {count === 1 ? "document" : "documents"}
                </span>
              </span>
            </button>
          </div>
        );
      })}
    </div>
  );
}

export function Breadcrumb({
  folders,
  folderId,
  onNavigate,
}: {
  folders: Folder[];
  folderId: number | null;
  onNavigate: (id: number | null) => void;
}) {
  const path = folderPath(folders, folderId);
  const crumb = "cursor-pointer text-sm text-zinc-500 hover:text-zinc-900 hover:underline";
  return (
    <nav aria-label="Breadcrumb" className="mb-3 flex flex-wrap items-center gap-1">
      {path.length === 0 ? (
        <span aria-current="page" className="text-sm font-medium">
          Root
        </span>
      ) : (
        <button type="button" className={crumb} onClick={() => onNavigate(null)}>
          Root
        </button>
      )}
      {path.map((f, index) => (
        <Fragment key={f.id}>
          <span className="text-zinc-300">/</span>
          {index === path.length - 1 ? (
            <span aria-current="page" className="text-sm font-medium">
              {f.name}
            </span>
          ) : (
            <button type="button" className={crumb} onClick={() => onNavigate(f.id)}>
              {f.name}
            </button>
          )}
        </Fragment>
      ))}
    </nav>
  );
}
