import { describe, expect, it } from "vitest";
import { buildFolderTree, childrenOf, folderLabel, folderPath } from "./folderTree";
import type { Folder } from "./types";

const folder = (id: number, name: string, parent_id: number | null = null): Folder => ({
  id,
  name,
  parent_id,
  created_at: "2026-01-01",
});

describe("buildFolderTree", () => {
  it("nests children under parents", () => {
    const tree = buildFolderTree([folder(1, "root"), folder(2, "child", 1), folder(3, "grand", 2)]);
    expect(tree).toHaveLength(1);
    expect(tree[0].children[0].name).toBe("child");
    expect(tree[0].children[0].children[0].name).toBe("grand");
  });

  it("sorts siblings alphabetically at every level", () => {
    const tree = buildFolderTree([folder(1, "b"), folder(2, "a"), folder(3, "z", 1), folder(4, "c", 1)]);
    expect(tree.map((n) => n.name)).toEqual(["a", "b"]);
    expect(tree[1].children.map((n) => n.name)).toEqual(["c", "z"]);
  });

  it("treats folders with unknown parents as roots", () => {
    const tree = buildFolderTree([folder(5, "orphan", 999)]);
    expect(tree.map((n) => n.name)).toEqual(["orphan"]);
  });
});

const nested = [folder(1, "Bollette"), folder(2, "2026", 1), folder(3, "Gennaio", 2), folder(4, "Assicurazioni")];

describe("folderPath", () => {
  it("returns the folders from the root to the node", () => {
    expect(folderPath(nested, 3).map((f) => f.name)).toEqual(["Bollette", "2026", "Gennaio"]);
  });

  it("is empty for null and unknown ids", () => {
    expect(folderPath(nested, null)).toEqual([]);
    expect(folderPath(nested, 99)).toEqual([]);
  });

  it("stops on a parent cycle", () => {
    const cyclic = [folder(1, "a", 2), folder(2, "b", 1)];
    expect(folderPath(cyclic, 1).map((f) => f.name)).toEqual(["b", "a"]);
  });
});

describe("childrenOf", () => {
  it("lists the top level sorted by name, including orphans", () => {
    expect(childrenOf([...nested, folder(5, "Zeta", 999)], null).map((f) => f.name)).toEqual([
      "Assicurazioni",
      "Bollette",
      "Zeta",
    ]);
  });

  it("lists the children of a folder", () => {
    expect(childrenOf(nested, 1).map((f) => f.name)).toEqual(["2026"]);
    expect(childrenOf(nested, 3)).toEqual([]);
  });
});

describe("folderLabel", () => {
  it("names the root, joins the path and marks unknown folders", () => {
    expect(folderLabel(nested, null)).toBe("(root)");
    expect(folderLabel(nested, 2)).toBe("Bollette / 2026");
    expect(folderLabel([], 2)).toBe("…");
  });
});
