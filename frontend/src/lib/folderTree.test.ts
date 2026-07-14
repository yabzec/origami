import { describe, expect, it } from "vitest";
import { buildFolderTree } from "./folderTree";
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
