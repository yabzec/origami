import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Folder } from "@/lib/types";
import { FolderTree } from "./FolderTree";

const FOLDERS: Folder[] = [
  { id: 1, name: "Bollette", parent_id: null, created_at: "", document_count: 2 },
  { id: 2, name: "2026", parent_id: 1, created_at: "", document_count: 3 },
  { id: 3, name: "Gennaio", parent_id: 2, created_at: "", document_count: 0 },
  { id: 4, name: "Auto", parent_id: null, created_at: "", document_count: 0 },
];

const fetchMock = vi.fn();
beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation(async (path: string) =>
    path === "/api/folders"
      ? new Response(JSON.stringify(FOLDERS), { status: 200, headers: { "Content-Type": "application/json" } })
      : new Response(JSON.stringify({ deleted_folders: 3, deleted_documents: 5, missing_folders: [], missing_documents: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderTree(selectedId: number | null) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onSelect = vi.fn();
  const tree = (id: number | null) => (
    <QueryClientProvider client={qc}>
      <FolderTree selectedId={id} allSelected={false} onSelect={onSelect} onSelectAll={vi.fn()} />
    </QueryClientProvider>
  );
  const utils = render(tree(selectedId));
  return { onSelect, select: (id: number | null) => utils.rerender(tree(id)) };
}

it("shows only top-level folders when nothing is open", async () => {
  renderTree(null);
  expect(await screen.findByText("Bollette")).toBeInTheDocument();
  expect(screen.getByText("Auto")).toBeInTheDocument();
  expect(screen.queryByText("2026")).not.toBeInTheDocument();
});

it("expands the path of the open folder and collapses it when navigating away", async () => {
  const { select } = renderTree(3);
  expect(await screen.findByText("Gennaio")).toBeInTheDocument();
  expect(screen.getByText("2026")).toBeInTheDocument();
  select(4);
  await waitFor(() => expect(screen.queryByText("2026")).not.toBeInTheDocument());
});

it("arrow toggles without navigating; name click navigates", async () => {
  const { onSelect } = renderTree(null);
  await userEvent.click(await screen.findByRole("button", { name: "Expand Bollette" }));
  expect(screen.getByText("2026")).toBeInTheDocument();
  expect(onSelect).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Bollette" }));
  expect(onSelect).toHaveBeenCalledWith(1);
});

it("deleting the open folder's ancestor deletes recursively and navigates to its parent", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  const { onSelect } = renderTree(2);
  await screen.findByText("2026");
  const row = screen.getByRole("button", { name: "Bollette" }).parentElement!;
  await userEvent.click(within(row).getByTitle("Delete"));
  expect(confirm).toHaveBeenCalledWith(
    'Delete folder "Bollette" with 2 subfolders and 5 documents? This cannot be undone.',
  );
  await waitFor(() => expect(onSelect).toHaveBeenCalledWith(null));
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/bulk/delete",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ folder_ids: [1], document_ids: [] }) }),
  );
});
