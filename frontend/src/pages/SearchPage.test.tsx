import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SearchPage } from "./SearchPage";

const fetchMock = vi.fn();
beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <SearchPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it("submits a search and renders highlighted snippet results", async () => {
  fetchMock.mockImplementation(async (url: string) => {
    if (url === "/api/folders" || url === "/api/tags") return json(200, []);
    if (url === "/api/search")
      return json(200, {
        mode: "hybrid",
        results: [
          {
            document: {
              id: "doc-1",
              title: "Bolletta marzo",
              status: "ready",
              doc_type: "pdf",
              tags: [],
            },
            score: 1,
            snippets: [
              { chunk_id: 9, page_number: 2, source: "content", text: "la <b>bolletta</b> di marzo", similarity: null },
            ],
          },
        ],
      });
    return json(404, { error: { code: "not_found", message: "no" } });
  });

  renderPage();
  await userEvent.type(screen.getByPlaceholderText(/search your documents/i), "bolletta");
  await userEvent.click(screen.getByRole("button", { name: /^search$/i }));

  expect(await screen.findByRole("link", { name: "Bolletta marzo" })).toHaveAttribute("href", "/documents/doc-1");
  expect(screen.getByText("bolletta").tagName).toBe("MARK");
  expect(screen.getByText(/p\. 2/)).toBeInTheDocument();

  const searchCall = fetchMock.mock.calls.find(([url]) => url === "/api/search")!;
  expect(JSON.parse(searchCall[1].body).mode).toBe("hybrid");
});

it("shows the empty state when nothing matches", async () => {
  fetchMock.mockImplementation(async (url: string) => {
    if (url === "/api/search") return json(200, { mode: "hybrid", results: [] });
    return json(200, []);
  });
  renderPage();
  await userEvent.type(screen.getByPlaceholderText(/search your documents/i), "niente");
  await userEvent.click(screen.getByRole("button", { name: /^search$/i }));
  expect(await screen.findByText(/no results/i)).toBeInTheDocument();
});

it("sends the date range with the search", async () => {
  fetchMock.mockImplementation(async (url: string) => {
    if (url === "/api/folders" || url === "/api/tags") return json(200, []);
    return json(200, { mode: "hybrid", results: [] });
  });
  renderPage();
  await userEvent.type(screen.getByPlaceholderText("Search your documents…"), "bolletta");
  fireEvent.change(screen.getByLabelText("From date"), { target: { value: "2026-01-01" } });
  fireEvent.change(screen.getByLabelText("To date"), { target: { value: "2026-03-31" } });
  await userEvent.click(screen.getByRole("button", { name: "Search" }));
  const call = fetchMock.mock.calls.find(([url]) => url === "/api/search");
  expect(JSON.parse(call![1].body).filters).toMatchObject({ date_from: "2026-01-01", date_to: "2026-03-31" });
});
