import { useState } from "react";
import { useCreateFolder, useDeleteFolder, useFolders, useRenameFolder } from "@/hooks/useFolders";
import { ApiError } from "@/lib/api";
import { buildFolderTree, type FolderNode } from "@/lib/folderTree";
import { cn } from "@/lib/utils";

function report(err: unknown) {
  window.alert(err instanceof ApiError ? err.message : "Operation failed");
}

function Node({
  node,
  depth,
  selectedId,
  onSelect,
}: {
  node: FolderNode;
  depth: number;
  selectedId: number | null;
  onSelect: (id: number | null) => void;
}) {
  const [open, setOpen] = useState(true);
  const create = useCreateFolder();
  const rename = useRenameFolder();
  const remove = useDeleteFolder();

  return (
    <div>
      <div
        className={cn(
          "group flex items-center gap-1 rounded px-2 py-1 text-sm hover:bg-zinc-100",
          selectedId === node.id && "bg-zinc-200 font-medium",
        )}
        style={{ paddingLeft: 8 + depth * 14 }}
      >
        <button onClick={() => setOpen(!open)} className="w-4 text-zinc-400" aria-label="toggle">
          {node.children.length > 0 ? (open ? "▾" : "▸") : "·"}
        </button>
        <button className="flex-1 truncate text-left" onClick={() => onSelect(node.id)}>
          {node.name}
        </button>
        <span className="hidden gap-1 group-hover:flex">
          <button
            title="New subfolder"
            onClick={() => {
              const name = window.prompt("Subfolder name");
              if (name) create.mutate({ name, parent_id: node.id }, { onError: report });
            }}
          >
            +
          </button>
          <button
            title="Rename"
            onClick={() => {
              const name = window.prompt("New name", node.name);
              if (name && name !== node.name) rename.mutate({ id: node.id, name }, { onError: report });
            }}
          >
            ✎
          </button>
          <button
            title="Delete"
            onClick={() => {
              if (window.confirm(`Delete folder "${node.name}"?`))
                remove.mutate(node.id, {
                  onError: report,
                  onSuccess: () => selectedId === node.id && onSelect(null),
                });
            }}
          >
            ×
          </button>
        </span>
      </div>
      {open &&
        node.children.map((child) => (
          <Node key={child.id} node={child} depth={depth + 1} selectedId={selectedId} onSelect={onSelect} />
        ))}
    </div>
  );
}

export function FolderTree({
  selectedId,
  onSelect,
}: {
  selectedId: number | null;
  onSelect: (id: number | null) => void;
}) {
  const { data: folders } = useFolders();
  const create = useCreateFolder();
  const tree = buildFolderTree(folders ?? []);

  return (
    <div>
      <div className="mb-1 flex items-center justify-between px-2">
        <span className="text-xs font-semibold uppercase text-zinc-400">Folders</span>
        <button
          title="New folder"
          className="text-zinc-400 hover:text-zinc-700"
          onClick={() => {
            const name = window.prompt("Folder name");
            if (name) create.mutate({ name, parent_id: null }, { onError: report });
          }}
        >
          +
        </button>
      </div>
      <button
        className={cn(
          "w-full rounded px-2 py-1 text-left text-sm hover:bg-zinc-100",
          selectedId === null && "bg-zinc-200 font-medium",
        )}
        onClick={() => onSelect(null)}
      >
        All documents
      </button>
      {tree.map((node) => (
        <Node key={node.id} node={node} depth={0} selectedId={selectedId} onSelect={onSelect} />
      ))}
    </div>
  );
}
