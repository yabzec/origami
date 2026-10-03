import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ChatPage } from "./ChatPage";

const fetchMock = vi.fn();
beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const BOLLETTA = { id: "doc-1", title: "Bolletta marzo", document_date: "2026-03-15" };
const SOURCE = { n: 1, chunk_id: 7, document_id: "doc-1", title: "Bolletta marzo", page_number: 2 };

const frame = (event: unknown) => `data: ${JSON.stringify(event)}\n\n`;

function answer(text: string, auto = [BOLLETTA]) {
  const events = [
    { type: "meta", grounded: true, sources: [SOURCE], auto_documents: auto },
    { type: "delta", text },
    { type: "done" },
  ];
  return new Response(events.map(frame).join(""), { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

function chatBodies() {
  return fetchMock.mock.calls.filter(([url]) => url === "/api/chat").map(([, init]) => JSON.parse(init.body));
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function ask(text: string) {
  await userEvent.type(screen.getByRole("textbox", { name: "Message" }), `${text}{Enter}`);
}

it("sends the question and renders the markdown answer, its citation and the auto chip", async () => {
  fetchMock.mockImplementation(async () => answer("Hai pagato **42 euro** [1]."));
  renderPage();
  await ask("Quanto ho pagato?");
  expect((await screen.findByText("42 euro")).tagName).toBe("STRONG");
  expect(screen.getByRole("link", { name: "[1]" })).toHaveAttribute("href", "/documents/doc-1");
  expect(screen.getByRole("button", { name: "Remove Bolletta marzo" })).toBeInTheDocument();
  expect(screen.getByText("auto")).toBeInTheDocument();
  expect(chatBodies()).toEqual([
    { messages: [{ role: "user", content: "Quanto ho pagato?" }], pinned_ids: [], excluded_ids: [] },
  ]);
});

it("sends the history on follow-ups and keeps a removed auto file excluded", async () => {
  fetchMock.mockImplementationOnce(async () => answer("Prima risposta [1].")).mockImplementationOnce(async () => answer("Seconda risposta."));
  renderPage();
  await ask("Quanto ho pagato?");
  await screen.findByText(/Prima risposta/);
  await userEvent.click(screen.getByRole("button", { name: "Remove Bolletta marzo" }));
  await ask("E a febbraio?");
  await screen.findByText("Seconda risposta.");
  expect(chatBodies()[1]).toEqual({
    messages: [
      { role: "user", content: "Quanto ho pagato?" },
      { role: "assistant", content: "Prima risposta [1]." },
      { role: "user", content: "E a febbraio?" },
    ],
    pinned_ids: [],
    excluded_ids: ["doc-1"],
  });
  expect(screen.queryByRole("button", { name: "Remove Bolletta marzo" })).not.toBeInTheDocument();
});

it("New chat asks for confirmation before clearing the conversation", async () => {
  fetchMock.mockImplementation(async () => answer("Risposta."));
  const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
  renderPage();
  await ask("Domanda?");
  await screen.findByText("Risposta.");
  await userEvent.click(screen.getByRole("button", { name: "New chat" }));
  expect(screen.getByText("Risposta.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "New chat" }));
  expect(confirm).toHaveBeenCalledTimes(2);
  expect(confirm).toHaveBeenCalledWith("Start a new chat? The current conversation will be cleared.");
  expect(screen.queryByText("Risposta.")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Remove Bolletta marzo" })).not.toBeInTheDocument();
});

it("Stop keeps the partial answer and the next question gets its own reply", async () => {
  let calls = 0;
  fetchMock.mockImplementation(async (_url: string, init: RequestInit) => {
    calls += 1;
    if (calls > 1) return answer("Seconda risposta.", []);
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(frame({ type: "meta", grounded: true, sources: [], auto_documents: [] })));
        controller.enqueue(encoder.encode(frame({ type: "delta", text: "Risposta parz" })));
        init.signal?.addEventListener("abort", () => controller.error(new DOMException("Aborted", "AbortError")));
      },
    });
    return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
  });
  renderPage();
  await ask("prima");
  expect(await screen.findByText("Risposta parz")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Stop" }));
  await ask("seconda");
  expect(await screen.findByText("Seconda risposta.")).toBeInTheDocument();
  expect(screen.getByText("Risposta parz")).toBeInTheDocument();
  expect(screen.queryByText(/Connection lost/)).not.toBeInTheDocument();
  expect(chatBodies()[1].messages).toEqual([
    { role: "user", content: "prima" },
    { role: "assistant", content: "Risposta parz" },
    { role: "user", content: "seconda" },
  ]);
});
