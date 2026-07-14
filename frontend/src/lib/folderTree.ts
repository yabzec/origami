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
