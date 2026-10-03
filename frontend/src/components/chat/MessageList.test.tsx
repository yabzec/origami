import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { expect, it } from "vitest";
import type { ChatMessage } from "@/lib/chatReducer";
import { MessageList } from "./MessageList";

it("renders bubbles, sources, the not-grounded banner and the typing indicator", () => {
  const messages: ChatMessage[] = [
    { id: "u1", role: "user", content: "Domanda?" },
    {
      id: "a1",
      role: "assistant",
      content: "Non trovato.",
      grounded: false,
      sources: [{ n: 1, chunk_id: 7, document_id: "doc-1", title: "Bolletta marzo", page_number: 2 }],
    },
    { id: "u2", role: "user", content: "Altro?" },
    { id: "a2", role: "assistant", content: "" },
  ];
  render(
    <MemoryRouter>
      <MessageList messages={messages} streaming />
    </MemoryRouter>,
  );
  expect(screen.getByText("Domanda?")).toBeInTheDocument();
  expect(screen.getByText("Non trovato.")).toBeInTheDocument();
  expect(screen.getByText("Answer not based on your documents.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Bolletta marzo" })).toHaveAttribute("href", "/documents/doc-1");
  expect(screen.getByText("(p. 2)")).toBeInTheDocument();
  expect(screen.getByRole("status", { name: "Assistant is typing" })).toBeInTheDocument();
});
