import type { Folder } from "./types";

export interface FolderNode extends Folder {
  children: FolderNode[];
}

export function buildFolderTree(folders: Folder[]): FolderNode[] {
  const nodes = new Map<number, FolderNode>();
  folders.forEach((f) => nodes.set(f.id, { ...f, children: [] }));
  const roots: FolderNode[] = [];
  nodes.forEach((node) => {
    if (node.parent_id !== null && nodes.has(node.parent_id)) {
      nodes.get(node.parent_id)!.children.push(node);
    } else {
      roots.push(node);
    }
  });
  const sortRec = (list: FolderNode[]) => {
    list.sort((a, b) => a.name.localeCompare(b.name));
    list.forEach((n) => sortRec(n.children));
  };
  sortRec(roots);
  return roots;
}

export function folderPath(folders: Folder[], id: number | null): Folder[] {
  const byId = new Map(folders.map((f) => [f.id, f]));
  const path: Folder[] = [];
  const seen = new Set<number>();
  let current = id === null ? undefined : byId.get(id);
  while (current && !seen.has(current.id)) {
    seen.add(current.id);
    path.unshift(current);
    current = current.parent_id === null ? undefined : byId.get(current.parent_id);
  }
  return path;
}

export function childrenOf(folders: Folder[], parentId: number | null): Folder[] {
  const ids = new Set(folders.map((f) => f.id));
  return folders
    .filter((f) =>
      parentId === null ? f.parent_id === null || !ids.has(f.parent_id) : f.parent_id === parentId,
    )
    .sort((a, b) => a.name.localeCompare(b.name));
}

export function folderLabel(folders: Folder[], id: number | null): string {
  if (id === null) return "(root)";
  const path = folderPath(folders, id);
  return path.length > 0 ? path.map((f) => f.name).join(" / ") : "…";
}

/** `ids` plus every folder below them. */
export function descendantIds(folders: Folder[], ids: Iterable<number>): Set<number> {
  const result = new Set(ids);
  let grew = result.size > 0;
  while (grew) {
    grew = false;
    for (const f of folders) {
      if (f.parent_id !== null && result.has(f.parent_id) && !result.has(f.id)) {
        result.add(f.id);
        grew = true;
      }
    }
  }
  return result;
}

/** Folders and documents in the subtrees of `ids` (the folders themselves included). */
export function subtreeTotals(folders: Folder[], ids: Iterable<number>): { folders: number; documents: number } {
  const all = descendantIds(folders, ids);
  const documents = folders.reduce((n, f) => (all.has(f.id) ? n + f.document_count : n), 0);
  return { folders: all.size, documents };
}

export function subtreeDocumentCount(folders: Folder[], id: number): number {
  return subtreeTotals(folders, [id]).documents;
}

/** The folder and all its ancestors (empty for the root). */
export function ancestorIds(folders: Folder[], id: number | null): Set<number> {
  return new Set(folderPath(folders, id).map((f) => f.id));
}
