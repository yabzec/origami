import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Dialog } from "@/components/ui/dialog";
import type { Folder } from "@/lib/types";
import { FolderPicker } from "./FolderPicker";

const FOLDERS: Folder[] = [
  { id: 1, name: "Bollette", parent_id: null, created_at: "2026-01-01" },
  { id: 2, name: "2026", parent_id: 1, created_at: "2026-01-01" },
  { id: 3, name: "Assicurazioni", parent_id: null, created_at: "2026-01-01" },
];

const fetchMock = vi.fn();
beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation(
    async () => new Response(JSON.stringify(FOLDERS), { status: 200, headers: { "Content-Type": "application/json" } }),
  );
});
afterEach(() => vi.unstubAllGlobals());

function Harness({ initial, onDialogClose }: { initial: number | null; onDialogClose: () => void }) {
  const [value, setValue] = useState<number | null>(initial);
  return (
    <Dialog open onClose={onDialogClose} title="Upload">
      <label htmlFor="pick">Folder</label>
      <FolderPicker id="pick" value={value} onChange={setValue} />
    </Dialog>
  );
}

function renderPicker(initial: number | null = null, onDialogClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <Harness initial={initial} onDialogClose={onDialogClose} />
    </QueryClientProvider>,
  );
  return { trigger: screen.getByLabelText("Folder"), onDialogClose };
}

it("drills down and selects the folder at every click", async () => {
  const { trigger } = renderPicker();
  expect(trigger).toHaveTextContent("(root)");
  await userEvent.click(trigger);
  await userEvent.click(await screen.findByRole("button", { name: "Bollette" }));
  expect(trigger).toHaveTextContent("Bollette");
  await userEvent.click(screen.getByRole("button", { name: "2026" }));
  expect(trigger).toHaveTextContent("Bollette / 2026");
  expect(screen.getByText("No subfolders")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /back/i }));
  expect(screen.getByRole("button", { name: "2026" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Done" }));
  expect(screen.queryByRole("dialog", { name: "Choose folder" })).not.toBeInTheDocument();
});

it("opens at the selected folder's level and Esc closes only the picker", async () => {
  const { trigger, onDialogClose } = renderPicker(2);
  await waitFor(() => expect(trigger).toHaveTextContent("Bollette / 2026"));
  await userEvent.click(trigger);
  expect(screen.getByRole("button", { name: "2026" })).toBeInTheDocument(); // sibling level of the selection
  expect(screen.queryByRole("button", { name: "(root)" })).not.toBeInTheDocument();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog", { name: "Choose folder" })).not.toBeInTheDocument();
  expect(onDialogClose).not.toHaveBeenCalled();
});

it("selects the root from the top level", async () => {
  const { trigger } = renderPicker(3);
  await waitFor(() => expect(trigger).toHaveTextContent("Assicurazioni"));
  await userEvent.click(trigger);
  await userEvent.click(screen.getByRole("button", { name: "(root)" }));
  expect(trigger).toHaveTextContent("(root)");
});
