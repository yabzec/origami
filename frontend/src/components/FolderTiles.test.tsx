import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { Breadcrumb, FolderTiles } from "./FolderTiles";

const FOLDERS = [
  { id: 1, name: "Bollette", parent_id: null, created_at: "", document_count: 4 },
  { id: 2, name: "2026", parent_id: 1, created_at: "", document_count: 1 },
  { id: 3, name: "Auto", parent_id: null, created_at: "", document_count: 0 },
];

it("shows direct subfolders with counts and opens on click", async () => {
  const onOpen = vi.fn();
  render(<FolderTiles folders={FOLDERS} parentId={null} onOpen={onOpen} />);
  expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["📁Auto0 documents", "📁Bollette5 documents"]);
  await userEvent.click(screen.getByRole("button", { name: /Bollette/ }));
  expect(onOpen).toHaveBeenCalledWith(1);
});

it("renders nothing when there are no subfolders", () => {
  const { container } = render(<FolderTiles folders={FOLDERS} parentId={2} onOpen={vi.fn()} />);
  expect(container).toBeEmptyDOMElement();
});

it("breadcrumb links every level", async () => {
  const onNavigate = vi.fn();
  render(<Breadcrumb folders={FOLDERS} folderId={2} onNavigate={onNavigate} />);
  await userEvent.click(screen.getByRole("button", { name: "Root" }));
  expect(onNavigate).toHaveBeenLastCalledWith(null);
  await userEvent.click(screen.getByRole("button", { name: "Bollette" }));
  expect(onNavigate).toHaveBeenLastCalledWith(1);
  expect(screen.getByText("2026")).toHaveAttribute("aria-current", "page");
});

it("checkbox toggles selection without opening the folder", async () => {
  const onOpen = vi.fn();
  const onToggleSelect = vi.fn();
  render(
    <FolderTiles folders={FOLDERS} parentId={null} onOpen={onOpen} selected={new Set(["f:3"])} onToggleSelect={onToggleSelect} />,
  );
  expect(screen.getByRole("checkbox", { name: "Select folder Auto" })).toBeChecked();
  await userEvent.click(screen.getByRole("checkbox", { name: "Select folder Bollette" }));
  expect(onToggleSelect).toHaveBeenCalledWith("f:1", false);
  expect(onOpen).not.toHaveBeenCalled();
});
