import { Fragment } from "react";
import { childrenOf, folderPath } from "@/lib/folderTree";
import type { Folder } from "@/lib/types";

export function FolderTiles({
  folders,
  parentId,
  onOpen,
}: {
  folders: Folder[];
  parentId: number | null;
  onOpen: (id: number) => void;
}) {
  const children = childrenOf(folders, parentId);
  if (children.length === 0) return null;
  return (
    <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
      {children.map((f) => (
        <button
          key={f.id}
          type="button"
          onClick={() => onOpen(f.id)}
          className="flex items-center gap-2 rounded-lg border border-zinc-200 bg-white p-3 text-left hover:shadow"
        >
          <span aria-hidden="true" className="text-xl">
            📁
          </span>
          <span className="min-w-0">
            <span className="block truncate text-sm font-medium">{f.name}</span>
            <span className="block text-xs text-zinc-400">
              {f.document_count} {f.document_count === 1 ? "document" : "documents"}
            </span>
          </span>
        </button>
      ))}
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
  const crumb = "text-sm text-zinc-500 hover:text-zinc-900 hover:underline";
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
