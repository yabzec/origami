import { useState } from "react";
import { NewFolderDialog } from "@/components/NewFolderDialog";
import { useBulkDeleteItems } from "@/hooks/useDocuments";
import { useFolders, useRenameFolder } from "@/hooks/useFolders";
import { ApiError } from "@/lib/api";
import { plural } from "@/lib/counts";
import { ancestorIds, buildFolderTree, subtreeTotals, type FolderNode } from "@/lib/folderTree";
import { cn } from "@/lib/utils";

function report(err: unknown) {
  window.alert(err instanceof ApiError ? err.message : "Operation failed");
}

interface NodeActions {
  selectedId: number | null;
  isExpanded: (id: number) => boolean;
  onToggle: (id: number) => void;
  onSelect: (id: number | null) => void;
  onNewSubfolder: (parentId: number) => void;
  onDelete: (node: FolderNode) => void;
}

function Node({ node, depth, actions }: { node: FolderNode; depth: number; actions: NodeActions }) {
  const rename = useRenameFolder();
  const open = actions.isExpanded(node.id);
  const hasChildren = node.children.length > 0;

  return (
    <div>
      <div
        className={cn(
          "group flex items-center gap-1 rounded px-2 py-1 text-sm hover:bg-zinc-100",
          actions.selectedId === node.id && "bg-zinc-200 font-medium",
        )}
        style={{ paddingLeft: 8 + depth * 14 }}
      >
        <button
          onClick={() => actions.onToggle(node.id)}
          className="w-4 text-zinc-400"
          disabled={!hasChildren}
          aria-label={`${open ? "Collapse" : "Expand"} ${node.name}`}
          aria-expanded={hasChildren ? open : undefined}
        >
          {hasChildren ? (open ? "▾" : "▸") : "·"}
        </button>
        <button className="flex-1 truncate text-left" onClick={() => actions.onSelect(node.id)}>
          {node.name}
        </button>
        <span className="hidden gap-1 group-hover:flex">
          <button title="New subfolder" onClick={() => actions.onNewSubfolder(node.id)}>
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
          <button title="Delete" onClick={() => actions.onDelete(node)}>
            ×
          </button>
        </span>
      </div>
      {open && node.children.map((child) => <Node key={child.id} node={child} depth={depth + 1} actions={actions} />)}
    </div>
  );
}

export function FolderTree({
  selectedId,
  allSelected,
  onSelect,
  onSelectAll,
}: {
  selectedId: number | null;
  allSelected: boolean;
  onSelect: (id: number | null) => void;
  onSelectAll: () => void;
}) {
  const { data } = useFolders();
  const folders = data ?? [];
  const tree = buildFolderTree(folders);
  const bulkDelete = useBulkDeleteItems();
  const [newIn, setNewIn] = useState<number | null | undefined>(undefined); // undefined: dialog closed

  // the open folder's path is expanded; manual arrow toggles flip a node until the next navigation
  const onPath = ancestorIds(folders, selectedId);
  const [toggled, setToggled] = useState<Set<number>>(() => new Set());
  const [toggledFor, setToggledFor] = useState(selectedId);
  if (toggledFor !== selectedId) {
    setToggledFor(selectedId);
    setToggled(new Set());
  }

  const actions: NodeActions = {
    selectedId,
    isExpanded: (id) => onPath.has(id) !== toggled.has(id),
    onToggle: (id) =>
      setToggled((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      }),
    onSelect,
    onNewSubfolder: (parentId) => setNewIn(parentId),
    onDelete: (node) => {
      const totals = subtreeTotals(folders, [node.id]);
      const subfolders = totals.folders - 1;
      const contents = [
        subfolders > 0 ? plural(subfolders, "subfolder") : null,
        totals.documents > 0 ? plural(totals.documents, "document") : null,
      ].filter(Boolean);
      const message = contents.length
        ? `Delete folder "${node.name}" with ${contents.join(" and ")}? This cannot be undone.`
        : `Delete folder "${node.name}"?`;
      if (!window.confirm(message)) return;
      bulkDelete.mutate(
        { folder_ids: [node.id], document_ids: [] },
        { onError: report, onSuccess: () => onPath.has(node.id) && onSelect(node.parent_id) },
      );
    },
  };

  return (
    <div className="[&_button]:cursor-pointer">
      <div className="mb-1 flex items-center justify-between px-2">
        <span className="text-xs font-semibold uppercase text-zinc-400">Folders</span>
        <button title="New folder" className="text-zinc-400 hover:text-zinc-700" onClick={() => setNewIn(null)}>
          +
        </button>
      </div>
      <button
        className={cn(
          "w-full rounded px-2 py-1 text-left text-sm hover:bg-zinc-100",
          allSelected && "bg-zinc-200 font-medium",
        )}
        onClick={onSelectAll}
      >
        All documents
      </button>
      <button
        className={cn(
          "w-full rounded px-2 py-1 text-left text-sm hover:bg-zinc-100",
          !allSelected && selectedId === null && "bg-zinc-200 font-medium",
        )}
        onClick={() => onSelect(null)}
      >
        Root
      </button>
      {tree.map((node) => (
        <Node key={node.id} node={node} depth={0} actions={actions} />
      ))}
      <NewFolderDialog open={newIn !== undefined} parentId={newIn ?? null} onClose={() => setNewIn(undefined)} />
    </div>
  );
}
