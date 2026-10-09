import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { expect, it, vi } from "vitest";
import type { Document } from "@/lib/types";
import { DocumentCard } from "./DocumentCard";

const doc = {
  id: "d1",
  title: "Invoice",
  doc_type: "pdf",
  status: "ready",
  document_date: "2026-01-01",
  page_count: null,
  file_size: null,
  tags: [],
  active_job: null,
} as unknown as Document;

function renderCard(onToggleSelect = vi.fn()) {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<DocumentCard doc={doc} onDelete={vi.fn()} onToggleSelect={onToggleSelect} />} />
        <Route path="/documents/:id" element={<p>detail page</p>} />
      </Routes>
    </MemoryRouter>,
  );
  return onToggleSelect;
}

it("keeps the checkbox focusable (not display:none) when nothing is selected", async () => {
  renderCard();
  const box = screen.getByRole("checkbox", { name: "Select Invoice" });
  expect(box.className).not.toMatch(/\bhidden\b/);
  await userEvent.tab(); // first tab stop in the card
  expect(box).toHaveFocus();
});

it("reports plain and shift clicks without navigating", async () => {
  const onToggle = renderCard();
  const box = screen.getByRole("checkbox", { name: "Select Invoice" });
  await userEvent.click(box);
  expect(onToggle).toHaveBeenLastCalledWith("d1", false);
  fireEvent.click(box, { shiftKey: true });
  expect(onToggle).toHaveBeenLastCalledWith("d1", true);
  expect(screen.queryByText("detail page")).not.toBeInTheDocument();
});
