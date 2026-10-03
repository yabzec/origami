import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ContextBar } from "./ContextBar";

it("shows pinned and auto chips with dates and removes a chip on ×", async () => {
  const onRemove = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ContextBar
        pinned={[{ id: "p1", title: "Contratto", document_date: "2026-01-10" }]}
        auto={[{ id: "a1", title: "Bolletta marzo", document_date: "2026-03-15" }]}
        onPin={vi.fn()}
        onRemove={onRemove}
      />
    </QueryClientProvider>,
  );
  expect(screen.getByText("Contratto")).toBeInTheDocument();
  expect(screen.getByText("10/01/2026")).toBeInTheDocument();
  expect(screen.getByText("15/03/2026")).toBeInTheDocument();
  expect(screen.getAllByText("auto")).toHaveLength(1);
  await userEvent.click(screen.getByRole("button", { name: "Remove Bolletta marzo" }));
  expect(onRemove).toHaveBeenCalledWith("a1");
  expect(screen.getByRole("button", { name: "+ Add file" })).toBeInTheDocument();
});
