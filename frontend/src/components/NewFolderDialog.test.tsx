import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { NewFolderDialog } from "./NewFolderDialog";

const fetchMock = vi.fn();
beforeEach(() => vi.stubGlobal("fetch", fetchMock));
afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function renderDialog(parentId: number | null = 5) {
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <NewFolderDialog open parentId={parentId} onClose={onClose} />
    </QueryClientProvider>,
  );
  return onClose;
}

it("creates the folder in the given parent on Enter", async () => {
  fetchMock.mockImplementation(async () => json({ id: 9, name: "Bills", parent_id: 5 }, 201));
  const onClose = renderDialog(5);
  await userEvent.type(screen.getByLabelText("Folder name"), "  Bills {Enter}");
  await waitFor(() => expect(onClose).toHaveBeenCalled());
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/folders",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ name: "Bills", parent_id: 5 }) }),
  );
});

it("shows the server error and stays open", async () => {
  fetchMock.mockImplementation(async () =>
    json({ error: { code: "duplicate_folder", message: "Sibling folder with same name exists" } }, 409),
  );
  const onClose = renderDialog();
  await userEvent.type(screen.getByLabelText("Folder name"), "Bills");
  await userEvent.click(screen.getByRole("button", { name: "Create" }));
  expect(await screen.findByText("Sibling folder with same name exists")).toBeInTheDocument();
  expect(onClose).not.toHaveBeenCalled();
});

it("rejects an empty name without calling the server", async () => {
  renderDialog();
  await userEvent.click(screen.getByRole("button", { name: "Create" }));
  expect(screen.getByText("Enter a folder name")).toBeInTheDocument();
  expect(fetchMock).not.toHaveBeenCalled();
});
