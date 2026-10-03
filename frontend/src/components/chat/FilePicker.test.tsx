import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FilePicker } from "./FilePicker";

const DOCS = [
  { id: "doc-1", title: "Bolletta marzo", document_date: "2026-03-15", doc_type: "pdf", original_filename: "b.pdf" },
  { id: "doc-2", title: "Bolletta aprile", document_date: "2026-04-15", doc_type: "pdf", original_filename: "a.pdf" },
];

const fetchMock = vi.fn();
beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation(
    async () =>
      new Response(
        JSON.stringify({ mode: "hybrid", results: DOCS.map((document) => ({ document, score: 1, snippets: [] })) }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
  );
});
afterEach(() => vi.unstubAllGlobals());

function renderPicker(onPick = vi.fn(), pinnedIds: string[] = []) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <p>outside</p>
      <FilePicker pinnedIds={pinnedIds} onPick={onPick} />
    </QueryClientProvider>,
  );
  return onPick;
}

it("searches after the debounce, hides pinned files and pins the clicked one", async () => {
  const onPick = renderPicker(vi.fn(), ["doc-2"]);
  await userEvent.click(screen.getByRole("button", { name: "+ Add file" }));
  await userEvent.type(screen.getByRole("textbox", { name: "Search files" }), "bolletta");
  const result = await screen.findByRole("button", { name: /Bolletta marzo/ });
  expect(screen.queryByRole("button", { name: /Bolletta aprile/ })).not.toBeInTheDocument();
  await userEvent.click(result);
  expect(onPick).toHaveBeenCalledWith({ id: "doc-1", title: "Bolletta marzo", document_date: "2026-03-15" });
  expect(screen.queryByRole("dialog", { name: "Add file" })).not.toBeInTheDocument();
  const searches = fetchMock.mock.calls.filter(([url]) => url === "/api/search");
  expect(searches).toHaveLength(1);
  expect(JSON.parse(searches[0][1].body)).toEqual({ query: "bolletta", mode: "hybrid", limit: 10 });
});

it("closes on Escape and on a click outside", async () => {
  renderPicker();
  const trigger = screen.getByRole("button", { name: "+ Add file" });
  await userEvent.click(trigger);
  expect(screen.getByRole("dialog", { name: "Add file" })).toBeInTheDocument();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog", { name: "Add file" })).not.toBeInTheDocument();
  await userEvent.click(trigger);
  await userEvent.click(screen.getByText("outside"));
  expect(screen.queryByRole("dialog", { name: "Add file" })).not.toBeInTheDocument();
});
