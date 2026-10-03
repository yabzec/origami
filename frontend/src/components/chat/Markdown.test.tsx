import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { expect, it } from "vitest";
import type { ChatSource } from "@/lib/types";
import { Markdown } from "./Markdown";

const SOURCES: ChatSource[] = [{ n: 1, chunk_id: 7, document_id: "doc-1", title: "Bolletta marzo", page_number: 2 }];

function renderMarkdown(text: string, sources: ChatSource[] = SOURCES) {
  return render(
    <MemoryRouter>
      <Markdown text={text} sources={sources} />
    </MemoryRouter>,
  );
}

it("renders GFM tables and lists", () => {
  renderMarkdown("| Mese | Euro |\n|---|---|\n| Marzo | 42 |\n\n- uno\n- **due**");
  expect(screen.getByRole("table")).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "42" })).toBeInTheDocument();
  expect(screen.getAllByRole("listitem")).toHaveLength(2);
  expect(screen.getByText("due").tagName).toBe("STRONG");
});

it("renders citation links to the document page", () => {
  renderMarkdown("Hai pagato 42 euro [1].");
  expect(screen.getByRole("link", { name: "[1]" })).toHaveAttribute("href", "/documents/doc-1");
});

it("never renders raw HTML from the model", () => {
  const { container } = renderMarkdown('Testo <img src="x" onerror="alert(1)"> e <script>alert(1)</script> fine');
  expect(container.querySelector("img")).toBeNull();
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector("[onerror]")).toBeNull();
});

it("neutralises javascript: links and opens external links in a new tab", () => {
  const { container } = renderMarkdown("[clic](javascript:alert(1)) e [sito](https://example.com)");
  expect(screen.getByText("clic").closest("a")).toBeNull();
  expect(container.querySelector('a[href^="javascript"]')).toBeNull();
  const external = screen.getByRole("link", { name: "sito" });
  expect(external).toHaveAttribute("href", "https://example.com");
  expect(external).toHaveAttribute("target", "_blank");
  expect(external).toHaveAttribute("rel", "noopener noreferrer");
});
